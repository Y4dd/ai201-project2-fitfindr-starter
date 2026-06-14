"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re
import warnings

from dotenv import load_dotenv

# groq 0.15 imports pydantic.v1 internals, which make pydantic emit a UserWarning on
# Python 3.14+ ("Core Pydantic V1 functionality isn't compatible..."). It's a harmless
# third-party compat notice — our API calls work fine — so silence it before importing
# groq, which is what triggers it. (pytest.ini filters the same warning for the test run.)
warnings.filterwarnings(
    "ignore", message="Core Pydantic V1 functionality", category=UserWarning
)

from groq import Groq  # noqa: E402  (import after the warnings filter, on purpose)

from utils.data_loader import load_listings

load_dotenv()

# Filler words stripped from the query before keyword scoring (locked in planning.md).
_STOP_WORDS = {"a", "an", "the", "for", "in", "with", "looking", "want", "need"}

# Groq model used by the two generative tools.
_MODEL = "llama-3.3-70b-versatile"

# Returned by create_fit_card when there is no outfit to caption (locked in planning.md).
_NO_OUTFIT_MSG = "⚠️ No outfit to write up yet — run a search that finds an item first."


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from the environment.

    The key is read from the shell environment (already exported in this project);
    a .env file is supported but optional.
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Export it in your shell (or add it to a .env file)."
        )
    return Groq(api_key=api_key)


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    Pipeline (see planning.md, Tool 1): price filter -> token size filter ->
    keyword-overlap scoring -> drop score 0 -> sort by score desc, ties by price.
    """
    listings = load_listings()

    # 1. Price filter (inclusive upper bound).
    if max_price is not None:
        listings = [item for item in listings if item["price"] <= max_price]

    # 2. Size filter (token match): split the listing's size on slashes/spaces and
    #    keep it only if the requested size equals one of those tokens.
    #    So "M" matches "S/M" and "M/L", but not "XL" or "US 8".
    if size is not None:
        wanted = size.strip().lower()
        kept = []
        for item in listings:
            tokens = re.split(r"[/\s]+", item["size"].lower())
            if wanted in tokens:
                kept.append(item)
        listings = kept

    # 3. Relevance score: distinct query keywords (minus stop words) that appear
    #    as a substring in a per-listing blob of its searchable text fields.
    keywords = {w for w in description.lower().split() if w not in _STOP_WORDS}

    scored = []
    for item in listings:
        blob = " ".join(
            [
                item["title"],
                item["description"],
                " ".join(item["style_tags"]),
                item["category"],
                " ".join(item["colors"]),
                item["brand"] or "",
            ]
        ).lower()
        score = sum(1 for kw in keywords if kw in blob)
        if score > 0:  # 4. Drop listings with no keyword overlap.
            scored.append((score, item))

    # 5. Sort by score (highest first), breaking ties by lower price.
    scored.sort(key=lambda pair: (-pair[0], pair[1]["price"]))
    return [item for _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.
        If the Groq call fails, a graceful non-empty fallback string is returned.
    """
    item_desc = (
        f"{new_item['title']} — a {new_item['category']} piece, "
        f"colors: {', '.join(new_item['colors'])}; "
        f"style: {', '.join(new_item['style_tags'])}."
    )

    items = wardrobe.get("items", [])
    if items:
        # Populated wardrobe: name the user's real pieces so the model can pair them.
        closet = "\n".join(
            f"- {it['name']} (id {it['id']}; colors: {', '.join(it['colors'])}; "
            f"style: {', '.join(it['style_tags'])})"
            for it in items
        )
        prompt = (
            "You are a thrift-savvy personal stylist. A shopper is considering this "
            f"secondhand find:\n{item_desc}\n\n"
            f"Here is what they already own:\n{closet}\n\n"
            "Suggest 1–2 complete outfits that pair the new find with specific pieces "
            "from their wardrobe. Name the wardrobe pieces you use (you may cite the id). "
            "Be concrete and concise — no preamble."
        )
    else:
        # Empty wardrobe: general styling advice instead of named pieces.
        prompt = (
            "You are a thrift-savvy personal stylist. A shopper is considering this "
            f"secondhand find:\n{item_desc}\n\n"
            "They haven't told you what's in their closet yet, so give general styling "
            "advice for this piece: the silhouettes, colors, and overall vibe it pairs "
            "well with, and 1–2 example outfits built around common basics. "
            "Be concrete and concise — no preamble."
        )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception:
        # Network/LLM error — fall through to a graceful, non-empty fallback.
        pass

    return (
        f"I couldn't reach the styling model just now, but the {new_item['title']} is a "
        "versatile piece — try grounding it with neutral basics in "
        f"{new_item['colors'][0] if new_item['colors'] else 'a complementary color'} "
        "and a contrasting layer so it stands out."
    )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)
    """
    # Guard: nothing to caption — return a descriptive error string, never raise.
    if not outfit or not outfit.strip():
        return _NO_OUTFIT_MSG

    prompt = (
        "Write a short, casual outfit caption for a social post (Instagram/TikTok "
        "OOTD vibe), 2–4 sentences. Sound like a real person hyping a thrifted find — "
        "not a product listing.\n"
        f"The find: {new_item['title']} (${new_item['price']}, on "
        f"{new_item['platform']}).\n"
        f"The outfit: {outfit}\n"
        "Mention the item name, price, and platform once each, naturally."
    )

    try:
        client = _get_groq_client()
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=1.0,  # high so captions vary across calls/inputs
        )
        text = (response.choices[0].message.content or "").strip()
        if text:
            return text
    except Exception:
        # Network/LLM error — fall through to a graceful, non-empty caption.
        pass

    return (
        f"Thrifted the {new_item['title']} for ${new_item['price']} on "
        f"{new_item['platform']} and styled it up — {outfit}. Obsessed. 🫶"
    )
