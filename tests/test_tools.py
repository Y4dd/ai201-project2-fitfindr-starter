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
