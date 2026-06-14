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
    """A query that matches surfaces lst_002 and runs all the way through:
    selected_item -> outfit_suggestion -> fit_card, with error staying None."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))

    session = agent.run_agent(
        "vintage graphic tee under $30, size M", _EXAMPLE_WARDROBE
    )

    assert session["error"] is None
    assert session["parsed"]["size"] == "M"
    assert session["parsed"]["max_price"] == 30.0
    assert session["selected_item"]["id"] == "lst_002"
    assert session["search_results"][0]["id"] == "lst_002"
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
    assert session["selected_item"]["id"] == "lst_002"
