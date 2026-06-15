"""
app.py

Gradio interface for FitFindr. The layout and wiring are already set up —
your job is to fill in handle_query() so it calls run_agent() and maps
the session results to the three output panels.

Run with:
    python app.py

Then open the localhost URL shown in your terminal (usually http://localhost:7860,
but check your terminal — the port may differ).
"""

import gradio as gr

from agent import run_agent
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── query handler ─────────────────────────────────────────────────────────────

def handle_query(user_query: str, wardrobe_choice: str) -> tuple[str, str, str, str]:
    """
    Called by Gradio when the user submits a query.

    Args:
        user_query:     The text the user typed into the search box.
        wardrobe_choice: Either "Example wardrobe" or "Empty wardrobe (new user)".

    Returns:
        A tuple of four strings:
            (listing_text, price_check, outfit_suggestion, fit_card)
        Each string maps to one of the four output panels in the UI. The price-check
        panel (Stretch 2) sits between the listing and the outfit.
    """
    # 1. Guard an empty query — don't bother the agent.
    if not user_query or not user_query.strip():
        return "Type what you're looking for to get started — e.g. 'vintage graphic tee under $30, size M'.", "", "", ""

    # 2. Pick the wardrobe the user selected.
    wardrobe = (
        get_empty_wardrobe()
        if wardrobe_choice == "Empty wardrobe (new user)"
        else get_example_wardrobe()
    )

    # 3. Run the planning loop — the session dict is the single source of truth.
    session = run_agent(user_query, wardrobe)

    # 4. No match: show the error in the listing panel, leave the other three blank.
    if session["error"]:
        return session["error"], "", "", ""

    # 5. A match: format the listing and hand the generated strings to their panels.
    #    Stretch 1 — if the retry ladder loosened a filter to find this off-spec item,
    #    prepend its note as a banner above the listing so the user knows why.
    listing_text = _format_listing(session["selected_item"])
    profile_note = session.get("profile_note")
    retry_note = session.get("retry_note")
    if profile_note:
        listing_text = f"{profile_note}\n\n{listing_text}"
    if retry_note:
        listing_text = f"{retry_note}\n\n{listing_text}"

    # Stretch 2 — the price-check verdict goes in its own panel (empty if unavailable).
    price_check = session.get("price_check")
    price_text = price_check["verdict"] if price_check else ""

    return listing_text, price_text, session["outfit_suggestion"], session["fit_card"]


def _format_listing(item: dict) -> str:
    """Render a selected listing dict into readable text for the listing panel."""
    header = f"{item['title']}  ·  {item['id']}"
    price_line = f"${item['price']:g} · {item['condition']} condition · {item['platform']}"
    size_line = f"Size {item['size']}" + (f" · {item['brand']}" if item['brand'] else "")
    tags_line = f"Colors: {', '.join(item['colors'])} · Style: {', '.join(item['style_tags'])}"
    return f"{header}\n{price_line}\n{size_line}\n{tags_line}\n\n{item['description']}"


# ── interface ─────────────────────────────────────────────────────────────────

EXAMPLE_QUERIES = [
    "vintage graphic tee under $30",
    "90s track jacket in size M",
    "flowy midi skirt under $40",
    "black combat boots size 8",
    "designer ballgown size XXS under $5",   # deliberate no-results test
]

def build_interface():
    with gr.Blocks(title="FitFindr") as demo:
        gr.Markdown("""
# FitFindr 🛍️
Find secondhand pieces and get outfit ideas based on your wardrobe.
Describe what you're looking for — include size and price if you want to filter.
        """)

        with gr.Row():
            query_input = gr.Textbox(
                label="What are you looking for?",
                placeholder="e.g. vintage graphic tee under $30, size M",
                lines=2,
                scale=3,
            )
            wardrobe_choice = gr.Radio(
                choices=["Example wardrobe", "Empty wardrobe (new user)"],
                value="Example wardrobe",
                label="Wardrobe",
                scale=1,
            )

        submit_btn = gr.Button("Find it", variant="primary")

        with gr.Row():
            listing_output = gr.Textbox(
                label="🛍️ Top listing found",
                lines=8,
                interactive=False,
            )
            price_output = gr.Textbox(
                label="💰 Price check",
                lines=8,
                interactive=False,
            )
            outfit_output = gr.Textbox(
                label="👗 Outfit idea",
                lines=8,
                interactive=False,
            )
            fitcard_output = gr.Textbox(
                label="✨ Your fit card",
                lines=8,
                interactive=False,
            )

        gr.Examples(
            examples=[[q, "Example wardrobe"] for q in EXAMPLE_QUERIES],
            inputs=[query_input, wardrobe_choice],
            label="Try these queries",
        )

        submit_btn.click(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice],
            outputs=[listing_output, price_output, outfit_output, fitcard_output],
        )
        query_input.submit(
            fn=handle_query,
            inputs=[query_input, wardrobe_choice],
            outputs=[listing_output, price_output, outfit_output, fitcard_output],
        )

    return demo


if __name__ == "__main__":
    demo = build_interface()
    demo.launch()
