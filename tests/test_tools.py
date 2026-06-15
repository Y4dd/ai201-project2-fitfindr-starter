"""
Isolation tests for the three FitFindr tools (tools.py).

Each tool is tested on its own, before being wired into the agent loop, and each
has at least one required failure-mode test (see CLAUDE.md error-handling table):

    search_listings  — no matches must return []  (never raise)
    suggest_outfit   — empty wardrobe must return a useful non-empty string
    create_fit_card  — empty/whitespace outfit must return an error string

The two generative tools talk to the Groq LLM. Their tests mock that boundary so
the suite stays deterministic and runnable offline (no API key / network needed).

Run from the project root:  pytest
"""

from types import SimpleNamespace

import tools
from utils.data_loader import get_example_wardrobe, load_listings

# A real listing dict to feed the generative tools (the anchor item from planning.md).
_ITEM = next(item for item in load_listings() if item["id"] == "lst_002")
_EXAMPLE_WARDROBE = get_example_wardrobe()
_EMPTY_WARDROBE = {"items": []}


def _fake_groq_client(content: str = "canned llm text", capture: dict | None = None):
    """Return a stand-in for the Groq client whose chat.completions.create()
    returns `content` and (optionally) records the kwargs it was called with.
    Lets us test our prompt-building/branching glue without a network call."""

    def create(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


# ─────────────────────────────────────────────────────────────────────────────
# Tool 1: search_listings  (pure / deterministic — no LLM)
# ─────────────────────────────────────────────────────────────────────────────

def test_search_ranks_best_keyword_match_first():
    """The anchor query from planning.md: 'vintage graphic tee', size M, <= $30
    surfaces lst_002 (Y2K Baby Tee) as the top result — it hits all three
    keywords, passes the token size filter ('M' in 'S/M') and the price ceiling."""
    results = tools.search_listings(
        "vintage graphic tee", size="M", max_price=30.0
    )
    assert results, "expected at least one match for the anchor query"
    assert results[0]["id"] == "lst_002"


def test_search_no_match_returns_empty_list():
    """REQUIRED failure mode: an impossible query returns [] — never raises."""
    results = tools.search_listings(
        "designer ballgown", size="XXS", max_price=5.0
    )
    assert results == []


def test_search_every_result_respects_max_price():
    """The price ceiling is inclusive and applied to every survivor."""
    results = tools.search_listings("vintage", max_price=20.0)
    assert results, "expected some vintage items under $20"
    assert all(item["price"] <= 20.0 for item in results)


def test_search_size_filter_is_token_based():
    """'M' must match a slash size like 'S/M' but exclude an 'L'-only listing.
    Same query under size='L' flips which of the two is eligible."""
    in_m = {item["id"] for item in tools.search_listings("graphic tee", size="M")}
    in_l = {item["id"] for item in tools.search_listings("graphic tee", size="L")}

    assert "lst_002" in in_m      # size "S/M" -> token "m"
    assert "lst_006" not in in_m  # size "L" -> no "m" token
    assert "lst_006" in in_l      # size "L" -> token "l"
    assert "lst_002" not in in_l  # size "S/M" -> no "l" token


# ─────────────────────────────────────────────────────────────────────────────
# Tool 2: suggest_outfit  (LLM-backed — Groq boundary mocked)
# ─────────────────────────────────────────────────────────────────────────────

def test_suggest_outfit_empty_wardrobe_returns_nonempty_string(monkeypatch):
    """REQUIRED failure mode: an empty wardrobe still yields a useful, non-empty
    string (general styling advice) — never a crash and never ''."""
    monkeypatch.setattr(
        tools, "_get_groq_client", lambda: _fake_groq_client("General styling advice.")
    )
    out = tools.suggest_outfit(_ITEM, _EMPTY_WARDROBE)
    assert isinstance(out, str) and out.strip()


def test_suggest_outfit_falls_back_gracefully_when_llm_fails(monkeypatch):
    """If the Groq call raises, the tool catches it and returns a non-empty
    fallback string instead of propagating the exception (no crash/no silent fail)."""
    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(tools, "_get_groq_client", boom)
    out = tools.suggest_outfit(_ITEM, _EXAMPLE_WARDROBE)
    assert isinstance(out, str) and out.strip()


def test_suggest_outfit_gives_llm_the_item_and_wardrobe(monkeypatch):
    """With a populated wardrobe, the prompt must carry both the new item and the
    user's actual pieces, so the model can name real wardrobe items back."""
    capture: dict = {}
    monkeypatch.setattr(
        tools, "_get_groq_client", lambda: _fake_groq_client("an outfit", capture)
    )
    tools.suggest_outfit(_ITEM, _EXAMPLE_WARDROBE)

    assert capture, "expected suggest_outfit to call the Groq LLM"
    prompt = " ".join(m["content"] for m in capture["messages"]).lower()
    assert _ITEM["title"].lower() in prompt
    assert any(it["name"].lower() in prompt for it in _EXAMPLE_WARDROBE["items"])


# ─────────────────────────────────────────────────────────────────────────────
# Tool 3: create_fit_card  (LLM-backed — Groq boundary mocked)
# ─────────────────────────────────────────────────────────────────────────────

_NO_OUTFIT_MSG = "⚠️ No outfit to write up yet — run a search that finds an item first."


def test_create_fit_card_empty_outfit_returns_error_string(monkeypatch):
    """REQUIRED failure mode: empty/whitespace outfit returns the descriptive error
    string and never reaches the LLM (so it can't raise)."""
    def fail_if_called():
        raise AssertionError("LLM must not be called when outfit is empty")

    monkeypatch.setattr(tools, "_get_groq_client", fail_if_called)
    assert tools.create_fit_card("", _ITEM) == _NO_OUTFIT_MSG
    assert tools.create_fit_card("   \n\t ", _ITEM) == _NO_OUTFIT_MSG


def test_create_fit_card_uses_high_temp_and_item_details(monkeypatch):
    """On the happy path it calls Groq at a high temperature (for variety) and feeds
    the model the outfit plus the item's name, price, and platform."""
    capture: dict = {}
    monkeypatch.setattr(
        tools, "_get_groq_client", lambda: _fake_groq_client("✨ fresh fit caption", capture)
    )
    outfit = "fitted baby tee with baggy dark-wash jeans and chunky white sneakers"
    result = tools.create_fit_card(outfit, _ITEM)

    assert result == "✨ fresh fit caption"
    assert capture["temperature"] >= 0.9  # locked ≈ 1.0 so captions vary
    prompt = " ".join(m["content"] for m in capture["messages"]).lower()
    assert outfit.lower() in prompt
    assert _ITEM["title"].lower() in prompt
    assert str(_ITEM["price"]) in prompt
    assert _ITEM["platform"].lower() in prompt


# ─────────────────────────────────────────────────────────────────────────────
# Tool 4: compare_price  (Stretch 2 — pure / deterministic, no LLM, zero-mock)
# ─────────────────────────────────────────────────────────────────────────────

def _priced(id: str, category: str, price: float) -> dict:
    """Minimal listing stub — compare_price only reads id, category, and price."""
    return {"id": id, "category": category, "price": price}


def test_compare_price_insufficient_data_for_accessories():
    """REQUIRED failure mode: an accessories item has only 2 same-category peers in
    our data (3 accessories minus itself), so compare_price returns the
    insufficient_data band with median None — it never raises and never guesses."""
    belt = next(i for i in load_listings() if i["id"] == "lst_014")  # accessories
    result = tools.compare_price(belt, load_listings())

    assert result["band"] == "insufficient_data"
    assert result["median"] is None
    assert result["n_comparables"] == 2
    assert result["price"] == belt["price"]
    assert result["category"] == "accessories"
    assert isinstance(result["verdict"], str) and result["verdict"].strip()


def test_compare_price_great_deal_band():
    """Item priced at/below the 25th percentile of its peers → great_deal."""
    item = _priced("x", "tops", 10.0)
    peers = [_priced("a", "tops", 20.0), _priced("b", "tops", 30.0),
             _priced("c", "tops", 40.0), _priced("d", "tops", 50.0)]
    result = tools.compare_price(item, peers)

    assert result["band"] == "great_deal"
    assert result["median"] == 35.0
    assert result["n_comparables"] == 4


def test_compare_price_fair_band():
    """Item priced mid-range (25th–75th percentile) → fair."""
    item = _priced("x", "tops", 30.0)
    peers = [_priced("a", "tops", 10.0), _priced("b", "tops", 20.0),
             _priced("c", "tops", 40.0), _priced("d", "tops", 50.0)]
    result = tools.compare_price(item, peers)

    assert result["band"] == "fair"
    assert result["median"] == 30.0


def test_compare_price_high_band():
    """Item priced above the 75th percentile of its peers → high."""
    item = _priced("x", "tops", 60.0)
    peers = [_priced("a", "tops", 10.0), _priced("b", "tops", 20.0),
             _priced("c", "tops", 30.0), _priced("d", "tops", 40.0)]
    result = tools.compare_price(item, peers)

    assert result["band"] == "high"
    assert result["median"] == 25.0


def test_compare_price_excludes_item_from_its_own_peer_group():
    """The item must not be compared against itself: a same-id copy in `comparables`
    is dropped, so neither n_comparables nor the median counts the item's price."""
    item = _priced("x", "tops", 1000.0)
    comparables = [
        _priced("x", "tops", 1000.0),   # the item itself — must be excluded by id
        _priced("a", "tops", 10.0),
        _priced("b", "tops", 20.0),
        _priced("c", "tops", 30.0),
    ]
    result = tools.compare_price(item, comparables)

    assert result["n_comparables"] == 3     # the duplicate is gone
    assert result["median"] == 20.0         # not 25.0 (which would include the 1000)


def test_compare_price_only_compares_same_category():
    """Peers in other categories are ignored — only same-category listings count."""
    item = _priced("x", "tops", 10.0)
    comparables = [
        _priced("a", "tops", 20.0),
        _priced("b", "tops", 30.0),
        _priced("c", "tops", 40.0),
        _priced("d", "bottoms", 1.0),   # different category — ignored
        _priced("e", "shoes", 2.0),
    ]
    result = tools.compare_price(item, comparables)

    assert result["category"] == "tops"
    assert result["n_comparables"] == 3


def test_compare_price_anchor_numbers_match_spec():
    """The planning.md headline example is real: the $18 Y2K tee (lst_002) reads as a
    great_deal against the 14 other tops (median $21.50)."""
    tee = next(i for i in load_listings() if i["id"] == "lst_002")
    result = tools.compare_price(tee, load_listings())

    assert result["band"] == "great_deal"
    assert result["n_comparables"] == 14
    assert result["median"] == 21.5
    assert "21.5" in result["verdict"]
    assert "tops" in result["verdict"]
