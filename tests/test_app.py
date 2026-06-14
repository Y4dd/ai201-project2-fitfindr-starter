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
    """A blank query short-circuits with a prompt in panel 1 and blank panels 2/3 —
    it should not even run the agent."""
    listing, outfit, fit = app.handle_query("   ", "Example wardrobe")
    assert listing.strip()          # some 'please enter a query' guidance
    assert outfit == ""
    assert fit == ""


def test_handle_query_no_results_shows_error_in_first_panel_only():
    """The empty-search error goes in the listing panel; the other two stay blank."""
    listing, outfit, fit = app.handle_query(
        "designer ballgown size XXS under $5", "Example wardrobe"
    )
    assert "couldn't find" in listing.lower()
    assert outfit == ""
    assert fit == ""


def test_handle_query_happy_path_formats_listing_and_fills_panels(monkeypatch):
    """On a match, panel 1 is a readable listing (title, price, platform) and panels
    2/3 carry the outfit suggestion and fit card."""
    monkeypatch.setattr(tools, "_get_groq_client", lambda: _fake_groq_client("styled!"))

    listing, outfit, fit = app.handle_query(
        "vintage graphic tee under $30, size M", "Example wardrobe"
    )
    assert "Y2K Baby Tee" in listing
    assert "$18" in listing
    assert "depop" in listing.lower()
    assert outfit == "styled!"
    assert fit == "styled!"


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
