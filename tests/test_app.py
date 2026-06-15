"""
Tests for the Gradio glue (app.py · handle_query).

handle_query maps a finished session dict onto the three output panels:
    (listing_text, outfit_suggestion, fit_card)

It must: guard an empty query, route the wardrobe choice, and show the error
message ALONE (other panels blank) when the search found nothing.

The no-results path never reaches the LLM, so it needs no mock; the happy path
mocks the Groq boundary (tools._get_groq_client) like the other suites.

Run from the project root:  pytest
"""

from types import SimpleNamespace

import app
import tools
from utils.data_loader import get_empty_wardrobe


def _fake_groq_client(content: str = "canned llm text"):
    def create(**kwargs):
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = SimpleNamespace(create=create)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_handle_query_blank_input_is_guarded():
    """A blank query short-circuits with a prompt in panel 1 and blank panels 2–4 —
    it should not even run the agent."""
    listing, price, outfit, fit = app.handle_query("   ", "Example wardrobe")
    assert listing.strip()          # some 'please enter a query' guidance
    assert price == ""
    assert outfit == ""
    assert fit == ""


def test_handle_query_no_results_shows_error_in_first_panel_only():
    """The empty-search error goes in the listing panel; the other three stay blank."""
    listing, price, outfit, fit = app.handle_query(
        "designer ballgown size XXS under $5", "Example wardrobe"
    )
    assert "couldn't find" in listing.lower()
    assert price == ""
    assert outfit == ""
    assert fit == ""


def test_handle_query_happy_path_formats_listing_and_fills_panels(monkeypatch):
    """On a match, panel 1 is a readable listing (price, platform) and panels 3/4
    carry the outfit suggestion and fit card. rank_by_profile re-orders results
    based on the example wardrobe profile, so we assert structure not a specific item."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))
    monkeypatch.setattr(tools, "_fetch_trend_ranking", lambda terms: terms[:3])

    listing, price, outfit, fit = app.handle_query(
        "vintage graphic tee under $30, size M", "Example wardrobe"
    )
    assert "$" in listing          # some price is shown
    assert "·" in listing          # the formatted listing separator is present
    assert outfit == "styled!"
    assert fit == "styled!"


def test_handle_query_shows_price_check_panel(monkeypatch):
    """Stretch 2: a successful search fills a dedicated price-check panel (panel 2)
    with the compare_price verdict. rank_by_profile may select a different top item
    than lst_002, so we assert the verdict structure rather than a specific price."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))
    monkeypatch.setattr(tools, "_fetch_trend_ranking", lambda terms: terms[:3])

    listing, price, outfit, fit = app.handle_query(
        "vintage graphic tee under $30, size M", "Example wardrobe"
    )
    assert "deal" in price.lower() or "fair" in price.lower() or "high" in price.lower()
    assert "$" in price            # some price figure is cited in the verdict
    assert outfit == "styled!" and fit == "styled!"


def test_handle_query_routes_empty_wardrobe_choice(monkeypatch):
    """Selecting the new-user option must pass the empty wardrobe into run_agent."""
    captured = {}

    def spy_run_agent(query, wardrobe):
        captured["wardrobe"] = wardrobe
        return {
            "error": None,
            "selected_item": {
                "title": "x", "price": 1.0, "condition": "good", "platform": "depop",
                "size": "M", "brand": None, "colors": ["black"], "style_tags": ["y2k"],
                "description": "d", "id": "lst_x",
            },
            "outfit_suggestion": "o",
            "fit_card": "f",
        }

    monkeypatch.setattr(app, "run_agent", spy_run_agent)
    app.handle_query("anything", "Empty wardrobe (new user)")
    assert captured["wardrobe"] == get_empty_wardrobe()


def test_handle_query_prepends_retry_banner_above_listing(monkeypatch):
    """Stretch 1: when the agent recovered via the retry ladder, handle_query shows
    the retry_note as a banner ABOVE the formatted listing."""
    note = "↔ test retry note — loosened the size filter"

    def stub_run_agent(query, wardrobe):
        return {
            "error": None,
            "retry_note": note,
            "selected_item": {
                "title": "Vintage Knit Vest", "price": 25.0, "condition": "good",
                "platform": "depop", "size": "M", "brand": None,
                "colors": ["brown"], "style_tags": ["argyle"],
                "description": "d", "id": "lst_030",
            },
            "outfit_suggestion": "o",
            "fit_card": "f",
        }

    monkeypatch.setattr(app, "run_agent", stub_run_agent)
    listing, price, outfit, fit = app.handle_query("argyle knit vest size L", "Example wardrobe")
    assert note in listing
    assert listing.index(note) < listing.index("Vintage Knit Vest")  # banner sits above
    assert outfit == "o" and fit == "f"


def test_handle_query_prepends_profile_note_banner(monkeypatch):
    """When run_agent returns a profile_note, handle_query prepends it as a banner
    above the listing text so the user knows their taste shaped the result."""
    fake_session = {
        "error": None,
        "selected_item": {
            "id": "lst_002", "title": "Y2K Baby Tee", "description": "cute tee",
            "price": 18.0, "condition": "excellent", "platform": "depop",
            "size": "S/M", "brand": None, "colors": ["white"], "style_tags": ["y2k"],
        },
        "retry_note": None,
        "profile_note": "↑ Ranked for your style — you tend toward y2k, vintage",
        "price_check": None,
        "outfit_suggestion": "pair with jeans",
        "fit_card": "casual chic",
    }
    monkeypatch.setattr(app, "run_agent", lambda q, w: fake_session)
    listing, price, outfit, fitcard = app.handle_query("graphic tee", "Example wardrobe")
    assert "↑ Ranked for your style" in listing
    assert "Y2K Baby Tee" in listing   # listing body still present


def test_handle_query_shows_trend_check_banner(monkeypatch):
    """Stretch 4: when run_agent returns a trend_check, handle_query renders its verdict
    as a banner above the listing text."""
    trend_verdict = "🔥 On-trend — vintage styles are rising on Google right now."
    fake_session = {
        "error": None,
        "selected_item": {
            "id": "lst_002", "title": "Y2K Baby Tee", "description": "cute tee",
            "price": 18.0, "condition": "excellent", "platform": "depop",
            "size": "S/M", "brand": None, "colors": ["white"], "style_tags": ["y2k"],
        },
        "retry_note": None,
        "profile_note": None,
        "price_check": None,
        "trend_check": {
            "band": "on_trend",
            "verdict": trend_verdict,
            "trending": ["vintage", "y2k"],
            "item_tags_on_trend": ["y2k"],
            "size": "M",
            "source": "google_trends",
        },
        "outfit_suggestion": "pair with jeans",
        "fit_card": "casual chic",
    }
    monkeypatch.setattr(app, "run_agent", lambda q, w: fake_session)
    listing, price, outfit, fitcard = app.handle_query("graphic tee", "Example wardrobe")
    assert trend_verdict in listing
    assert "Y2K Baby Tee" in listing   # listing body still present below the banner
