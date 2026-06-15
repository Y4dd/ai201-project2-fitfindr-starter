"""
Tests for the FitFindr planning loop (agent.py).

These exercise the wiring M4 adds on top of the already-tested tools:
  - the hybrid query parser (regex tier 1, LLM tier 2, raw-query tier 3)
  - run_agent's session state + the CONDITIONAL branch on search results
    (an empty search must NOT call the generative tools — it returns early)

The generative tools (suggest_outfit / create_fit_card) talk to Groq, so we mock
that boundary the same way tests/test_tools.py does — monkeypatch
tools._get_groq_client — keeping the suite deterministic and offline.

Run from the project root:  pytest
"""

import json
import pathlib
from types import SimpleNamespace

import agent
import tools
from utils.data_loader import get_example_wardrobe

_EXAMPLE_WARDROBE = get_example_wardrobe()


def _fake_groq_client(content: str = "canned llm text"):
    """Stand-in Groq client whose chat.completions.create() returns `content`."""

    def create(**kwargs):
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


# ─────────────────────────────────────────────────────────────────────────────
# Tier 1: the regex parser  (pure / deterministic — no LLM)
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_extracts_description_size_and_price():
    """The anchor query: pull size + price out, leave clean description behind."""
    parsed = agent._parse_query("vintage graphic tee under $30, size M")
    assert parsed["description"] == "vintage graphic tee"
    assert parsed["size"] == "M"
    assert parsed["max_price"] == 30.0


def test_parse_handles_word_size_and_trailing_dollar():
    """The cases tier 2 was meant to cover, handled deterministically in regex:
    'Medium' normalizes to 'M' and '30$' (dollar after the number) parses to 30.0."""
    parsed = agent._parse_query("cozy sweater in Medium 30$")
    assert parsed["size"] == "M"
    assert parsed["max_price"] == 30.0
    assert parsed["description"] == "cozy sweater"


def test_parse_no_filters_leaves_size_and_price_none():
    """A plain query has no size/price; the whole thing is the description."""
    parsed = agent._parse_query("vintage graphic tee")
    assert parsed["description"] == "vintage graphic tee"
    assert parsed["size"] is None
    assert parsed["max_price"] is None


# ─────────────────────────────────────────────────────────────────────────────
# run_agent: happy path — full session state filled
# ─────────────────────────────────────────────────────────────────────────────

def test_run_agent_happy_path_fills_full_session(monkeypatch):
    """A query that matches and runs all the way through:
    selected_item -> outfit_suggestion -> fit_card, with error staying None.
    Note: rank_by_profile re-orders results by the example wardrobe profile, so
    we assert a match was found and the session is complete, not a specific item id."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))

    session = agent.run_agent(
        "vintage graphic tee under $30, size M", _EXAMPLE_WARDROBE
    )

    assert session["error"] is None
    assert session["parsed"]["size"] == "M"
    assert session["parsed"]["max_price"] == 30.0
    assert session["selected_item"] is not None
    assert session["search_results"]
    assert session["selected_item"]["id"] == session["search_results"][0]["id"]
    assert session["retry_note"] is None   # exact match — nothing was loosened
    assert isinstance(session["outfit_suggestion"], str) and session["outfit_suggestion"]
    assert isinstance(session["fit_card"], str) and session["fit_card"]


# ─────────────────────────────────────────────────────────────────────────────
# run_agent: the CONDITIONAL branch — no results must short-circuit
# ─────────────────────────────────────────────────────────────────────────────

def test_run_agent_no_results_sets_error_and_skips_generative_tools(monkeypatch):
    """REQUIRED branch behavior: when search_listings returns [], run_agent sets
    session['error'], returns early, and NEVER calls suggest_outfit/create_fit_card
    with empty input. We prove the skip by making those tools explode if reached."""
    def explode(*args, **kwargs):
        raise AssertionError("generative tool must not run on an empty search")

    monkeypatch.setattr(agent, "suggest_outfit", explode)
    monkeypatch.setattr(agent, "create_fit_card", explode)

    session = agent.run_agent("designer ballgown size XXS under $5", _EXAMPLE_WARDROBE)

    assert session["search_results"] == []
    assert isinstance(session["error"], str) and session["error"].strip()
    assert session["retry_note"] is None   # the ladder fully failed — no recovery note
    assert session["selected_item"] is None
    assert session["outfit_suggestion"] is None
    assert session["fit_card"] is None


def test_run_agent_error_message_names_the_failed_filters(monkeypatch):
    """The no-results error should be actionable — echo back what was searched
    so the user knows which filter to loosen."""
    monkeypatch.setattr(agent, "suggest_outfit", lambda *a, **k: "unused")
    monkeypatch.setattr(agent, "create_fit_card", lambda *a, **k: "unused")

    session = agent.run_agent("designer ballgown size XXS under $5", _EXAMPLE_WARDROBE)
    err = session["error"].lower()
    assert "designer ballgown" in err
    assert "xxs" in err
    assert "5" in err


# ─────────────────────────────────────────────────────────────────────────────
# Tier 2: LLM parse fallback
# ─────────────────────────────────────────────────────────────────────────────

def test_llm_parse_query_reads_json_from_model(monkeypatch):
    """_llm_parse_query asks the model for JSON and coerces it into the parsed dict
    (max_price as float)."""
    payload = '{"description": "graphic tee", "size": "M", "max_price": 30}'
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client(payload))

    parsed = agent._llm_parse_query("size M under $30")
    assert parsed["description"] == "graphic tee"
    assert parsed["size"] == "M"
    assert parsed["max_price"] == 30.0


def test_llm_parse_query_returns_none_on_unparseable_output(monkeypatch):
    """If the model doesn't return usable JSON, _llm_parse_query returns None so the
    loop falls through to the raw-query tier instead of crashing."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("nope, sorry"))
    assert agent._llm_parse_query("size M under $30") is None


def test_run_agent_uses_llm_fallback_when_regex_finds_no_description(monkeypatch):
    """When the regex extracts only filters (empty description), the loop consults the
    LLM parser and uses its description to drive the search."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))
    monkeypatch.setattr(
        agent,
        "_llm_parse_query",
        lambda q: {"description": "vintage graphic tee", "size": "M", "max_price": 30.0},
    )

    session = agent.run_agent("size M under $30", _EXAMPLE_WARDROBE)
    assert session["parsed"]["description"] == "vintage graphic tee"
    # rank_by_profile re-orders by the example wardrobe; assert a result was found
    # (the specific id depends on profile scoring, not just keyword relevance).
    assert session["selected_item"] is not None
    assert session["selected_item"]["id"] in {r["id"] for r in session["search_results"]}


# ─────────────────────────────────────────────────────────────────────────────
# Stretch 1: _search_with_fallback — the ordered relaxation ladder
# (pure / deterministic — exercises the real search_listings over the real dataset)
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_exact_match_returns_no_note():
    """Attempt 0 hits, so nothing is relaxed and retry_note stays None."""
    results, note = agent._search_with_fallback("vintage graphic tee", "M", 30.0)
    assert results and results[0]["id"] == "lst_002"
    assert note is None


def test_fallback_recovers_by_dropping_size():
    """No size-L argyle vest exists, so the ladder drops the size filter and recovers
    the size-M one (lst_030). The note names the loosened 'size' and the item's real
    price."""
    results, note = agent._search_with_fallback("argyle knit vest", "L", None)
    assert results and results[0]["id"] == "lst_030"
    assert note is not None
    assert "size" in note.lower()
    assert "$25" in note            # the recovered item's real price


def test_fallback_recovers_by_dropping_price():
    """With no size set, attempt 1 (drop size) is a no-op and is skipped; attempt 2
    drops the price ceiling and recovers the $44 boots (lst_028). Note names 'price'."""
    results, note = agent._search_with_fallback("suede chelsea boots", None, 30.0)
    assert results and results[0]["id"] == "lst_028"
    assert note is not None
    assert "price" in note.lower()
    assert "$44" in note


def test_fallback_recovers_by_dropping_size_and_price():
    """Canonical full ladder: attempts 0 and 1 are both empty, so attempt 2 drops BOTH
    size and price and recovers lst_036; the note names both loosened filters."""
    results, note = agent._search_with_fallback("velvet blazer", "M", 30.0)
    assert results and results[0]["id"] == "lst_036"
    assert note is not None
    assert "size" in note.lower() and "price" in note.lower()


def test_fallback_unrecoverable_returns_empty_and_none():
    """REQUIRED failure mode: when every applicable attempt is empty, the helper
    returns ([], None) — it never raises and never fabricates a note."""
    results, note = agent._search_with_fallback("designer ballgown", "XXS", 5.0)
    assert results == []
    assert note is None


def test_fallback_with_no_filters_has_nothing_to_relax():
    """No size and no price means there is nothing to loosen — an empty attempt 0 is
    final, returning ([], None) without crashing on the skipped attempts."""
    results, note = agent._search_with_fallback("designer ballgown", None, None)
    assert results == []
    assert note is None


# ─────────────────────────────────────────────────────────────────────────────
# Stretch 1: run_agent wires the ladder — a recovered near-miss runs to completion
# ─────────────────────────────────────────────────────────────────────────────

def test_run_agent_recovered_search_sets_retry_note_and_completes(monkeypatch):
    """A near-miss (a size-L vest that only exists in M) is recovered by the ladder:
    run_agent stores retry_note, selects the off-spec item, and still runs the
    generative tools all the way to a fit card."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))

    session = agent.run_agent("argyle knit vest size L", _EXAMPLE_WARDROBE)

    assert session["error"] is None
    assert session["selected_item"]["id"] == "lst_030"
    assert isinstance(session["retry_note"], str) and session["retry_note"].strip()
    assert session["fit_card"]


# ─────────────────────────────────────────────────────────────────────────────
# Stretch 2: run_agent wires the price check — a successful search stores a verdict
# ─────────────────────────────────────────────────────────────────────────────

def test_run_agent_happy_path_runs_price_check(monkeypatch):
    """A successful search stores a compare_price result in session['price_check'].
    Note: rank_by_profile re-orders results, so we assert a price_check verdict exists
    and has the expected structure rather than assuming a specific item is selected."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))

    session = agent.run_agent("vintage graphic tee under $30, size M", _EXAMPLE_WARDROBE)

    assert session["selected_item"] is not None
    assert session["price_check"] is not None
    assert "band" in session["price_check"]
    assert "n_comparables" in session["price_check"]


def test_run_agent_no_results_leaves_price_check_none(monkeypatch):
    """The price check is a post-selection step, so the no-results error path never
    runs it — price_check stays None alongside the other downstream fields."""
    monkeypatch.setattr(agent, "suggest_outfit", lambda *a, **k: "unused")
    monkeypatch.setattr(agent, "create_fit_card", lambda *a, **k: "unused")

    session = agent.run_agent("designer ballgown size XXS under $5", _EXAMPLE_WARDROBE)

    assert session["error"]
    assert session["price_check"] is None


# ─────────────────────────────────────────────────────────────────────────────
# Stretch 3 helpers: _load_profile, _update_profile, _save_profile
# (pure over tmp files — no network mocks needed)
# ─────────────────────────────────────────────────────────────────────────────

def test_load_profile_missing_file_returns_empty_skeleton(monkeypatch, tmp_path):
    """REQUIRED: missing data/style_profile.json → empty profile dict, never raises."""
    monkeypatch.setattr(agent, "PROFILE_PATH", str(tmp_path / "no_such_file.json"))
    profile = agent._load_profile({})
    # Must contain all required keys with zero/empty values.
    assert profile["style_tags"] == {}
    assert profile["colors"] == {}
    assert profile["categories"] == {}
    assert profile["brands"] == {}
    assert profile["price_sum"] == 0.0
    assert profile["price_count"] == 0
    assert profile["runs"] == 0


def test_load_profile_corrupt_file_returns_empty_skeleton(monkeypatch, tmp_path):
    """Corrupt JSON in the profile file → empty profile, never raises."""
    p = tmp_path / "corrupt.json"
    p.write_text("not valid json {{{")
    monkeypatch.setattr(agent, "PROFILE_PATH", str(p))
    profile = agent._load_profile({})
    assert profile["style_tags"] == {}
    assert profile["runs"] == 0


def test_load_profile_reads_existing_file(monkeypatch, tmp_path):
    """When the file exists and is valid, _load_profile returns its contents."""
    saved = {"style_tags": {"y2k": 3}, "colors": {"white": 2},
             "categories": {"tops": 5}, "brands": {},
             "price_sum": 54.0, "price_count": 3, "runs": 3}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(saved))
    monkeypatch.setattr(agent, "PROFILE_PATH", str(p))
    profile = agent._load_profile({})
    assert profile["style_tags"] == {"y2k": 3}
    assert profile["runs"] == 3


def test_update_profile_folds_all_signals():
    """_update_profile increments tags, colors, category, brand, accumulates price, bumps runs."""
    profile = {"style_tags": {"y2k": 1}, "colors": {}, "categories": {},
               "brands": {}, "price_sum": 20.0, "price_count": 1, "runs": 1}
    item = {
        "id": "lst_002", "style_tags": ["y2k", "vintage"], "colors": ["white", "pink"],
        "category": "tops", "brand": None, "price": 18.0,
    }
    updated = agent._update_profile(profile, item)
    # Tags: y2k went from 1 → 2; vintage is new → 1.
    assert updated["style_tags"]["y2k"] == 2
    assert updated["style_tags"]["vintage"] == 1
    # Colors: white and pink added.
    assert updated["colors"]["white"] == 1
    assert updated["colors"]["pink"] == 1
    # Category: tops added.
    assert updated["categories"]["tops"] == 1
    # Brand: None → skipped (no brand key added for empty string).
    assert "" not in updated["brands"] or updated["brands"].get("", 0) == 0
    # Price accumulated.
    assert updated["price_sum"] == 38.0
    assert updated["price_count"] == 2
    # Runs bumped.
    assert updated["runs"] == 2


def test_save_profile_writes_to_profile_path(monkeypatch, tmp_path):
    """_save_profile serializes the dict to PROFILE_PATH as valid JSON."""
    p = tmp_path / "out.json"
    monkeypatch.setattr(agent, "PROFILE_PATH", str(p))
    profile = {"style_tags": {"y2k": 2}, "colors": {}, "categories": {},
               "brands": {}, "price_sum": 18.0, "price_count": 1, "runs": 1}
    agent._save_profile(profile)
    loaded = json.loads(p.read_text())
    assert loaded["style_tags"] == {"y2k": 2}
    assert loaded["runs"] == 1


def test_load_profile_missing_file_seeds_from_wardrobe(monkeypatch, tmp_path):
    """Missing file + non-empty wardrobe → seeded profile (not empty skeleton)."""
    monkeypatch.setattr(agent, "PROFILE_PATH", str(tmp_path / "no_such_file.json"))
    wardrobe = {"items": [{"style_tags": ["vintage"], "colors": ["blue"], "category": "tops"}]}
    profile = agent._load_profile(wardrobe)
    assert profile["style_tags"].get("vintage") == 1
    assert profile["colors"].get("blue") == 1
    assert profile["categories"].get("tops") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Stretch 3: run_agent wiring — load → re-rank → profile_note → update → save
# ─────────────────────────────────────────────────────────────────────────────

def test_run_agent_stores_style_profile_and_profile_note(monkeypatch, tmp_path):
    """run_agent stores the loaded style_profile in the session; profile_note is set
    to a non-empty string when the profile has taste signals (warm profile)."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))

    # Provide a warm profile so a banner is expected.
    warm = {"style_tags": {"y2k": 3, "vintage": 2}, "colors": {"white": 4},
            "categories": {"tops": 5}, "brands": {},
            "price_sum": 54.0, "price_count": 3, "runs": 3}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(warm))
    monkeypatch.setattr(agent, "PROFILE_PATH", str(p))

    session = agent.run_agent("vintage graphic tee under $30, size M", _EXAMPLE_WARDROBE)
    assert session["error"] is None
    assert isinstance(session["style_profile"], dict)
    assert session["style_profile"]["runs"] >= 3   # at least the pre-loaded value (was bumped by _update_profile)
    # profile_note should be a non-empty string naming at least one taste signal.
    assert isinstance(session["profile_note"], str)
    assert len(session["profile_note"]) > 0


def test_run_agent_no_results_leaves_profile_note_none(monkeypatch, tmp_path):
    """On the no-results path rank_by_profile never runs, so profile_note stays None."""
    monkeypatch.setattr(agent, "suggest_outfit", lambda *a, **k: "unused")
    monkeypatch.setattr(agent, "create_fit_card", lambda *a, **k: "unused")

    warm = {"style_tags": {"y2k": 3}, "colors": {}, "categories": {}, "brands": {},
            "price_sum": 0.0, "price_count": 0, "runs": 1}
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(warm))
    monkeypatch.setattr(agent, "PROFILE_PATH", str(p))

    session = agent.run_agent("designer ballgown size XXS under $5", _EXAMPLE_WARDROBE)
    assert session["error"] is not None
    assert session["profile_note"] is None


def test_run_agent_cold_profile_gives_none_profile_note(monkeypatch, tmp_path):
    """Cold profile (empty style_tags + colors) → no banner, profile_note is None."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))
    monkeypatch.setattr(agent, "PROFILE_PATH", str(tmp_path / "no_file.json"))

    # Use empty wardrobe so _load_profile returns an empty skeleton (not wardrobe-seeded).
    from utils.data_loader import get_empty_wardrobe
    session = agent.run_agent("vintage graphic tee under $30", get_empty_wardrobe())
    assert session["error"] is None
    assert session["profile_note"] is None
