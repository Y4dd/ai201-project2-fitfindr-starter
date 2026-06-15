"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import json
import pathlib
import re

import tools
from tools import search_listings, suggest_outfit, create_fit_card, compare_price, rank_by_profile
from utils.data_loader import load_listings

PROFILE_PATH = "data/style_profile.json"

# ── query parsing (hybrid: regex → LLM fallback → raw query) ───────────────────

# Canonical size labels. Word sizes ("Medium") and abbreviations ("M") both map here
# so the parser is robust to how the user phrases it (see planning.md, Planning Loop).
_SIZE_CANON = {
    "xxs": "XXS", "extra extra small": "XXS",
    "xs": "XS", "extra small": "XS",
    "s": "S", "small": "S",
    "m": "M", "med": "M", "medium": "M",
    "l": "L", "large": "L",
    "xl": "XL", "x large": "XL", "xlarge": "XL", "extra large": "XL",
    "xxl": "XXL", "xx large": "XXL", "xxlarge": "XXL",
}

# Price phrasings: "under $30", "below 40", "less than $25", "max $20", "< $30",
# then bare "$30", "30$", and "30 dollars" / "30 bucks".
_PRICE_PATTERNS = (
    r"(?:under|below|less than|at most|no more than|up to|max(?:imum)?|<)\s*\$?\s*(\d+(?:\.\d+)?)",
    r"\$\s*(\d+(?:\.\d+)?)",
    r"(\d+(?:\.\d+)?)\s*\$",
    r"(\d+(?:\.\d+)?)\s*(?:dollars?|bucks?|usd)\b",
)

# Size with an explicit marker ("size M", "sz 8", "in Medium"): markers let single
# letters and digits match unambiguously ("in M" yes, "in black" no).
_SIZE_MARKED = re.compile(
    r"\b(?:size|sz|in)\s+"
    r"(xxs|xs|s|m|l|xl|xxl|small|med|medium|large|x[- ]?large|xx[- ]?large|"
    r"extra[- ]?(?:small|large)|\d{1,2})\b",
    re.IGNORECASE,
)
# Bare word sizes (no marker needed): full words + multi-char abbreviations only —
# never bare single letters ("a small bag" is fine; a lone "m" would be too risky).
_SIZE_WORD = re.compile(
    r"\b(xxs|xs|small|medium|large|x[- ]?large|xx[- ]?large|"
    r"extra[- ]?(?:small|large)|xl|xxl)\b",
    re.IGNORECASE,
)


def _canon_size(token: str) -> str:
    """Normalize a captured size token to a canonical label ('Medium' -> 'M')."""
    key = token.strip().lower().replace("-", " ")
    key = re.sub(r"\s+", " ", key)
    if key in _SIZE_CANON:
        return _SIZE_CANON[key]
    if key.isdigit():
        return key
    return token.strip().upper()


def _parse_query(query: str) -> dict:
    """Tier 1 (pure regex): pull `size` and `max_price` out of a free-text query,
    leaving the remaining words as the search `description`.

    Returns a dict ``{"description": str, "size": str | None, "max_price": float | None}``.
    Never raises; an unrecognized query just yields the whole string as description.
    """
    rest = query

    # Price: first matching pattern wins; remove its span so it can't pollute keywords.
    max_price: float | None = None
    for pattern in _PRICE_PATTERNS:
        m = re.search(pattern, rest, re.IGNORECASE)
        if m:
            max_price = float(m.group(1))
            rest = rest[: m.start()] + " " + rest[m.end():]
            break

    # Size: prefer a marked match (consumes the marker too, e.g. "in Medium"), else a
    # bare word size.
    size: str | None = None
    m = _SIZE_MARKED.search(rest)
    if not m:
        m = _SIZE_WORD.search(rest)
    if m:
        size = _canon_size(m.group(1))
        rest = rest[: m.start()] + " " + rest[m.end():]

    # Description: whatever survives, with punctuation flattened and whitespace collapsed.
    description = re.sub(r"[,.;]", " ", rest)
    description = re.sub(r"\s+", " ", description).strip()

    return {"description": description, "size": size, "max_price": max_price}


def _llm_parse_query(query: str) -> dict | None:
    """Tier 2 (LLM fallback): ask the model to parse a query the regex couldn't, and
    return ``{"description", "size", "max_price"}``. Returns ``None`` if the model is
    unreachable or doesn't return usable JSON, so the loop can fall back to the raw query.
    """
    prompt = (
        "Extract secondhand-clothing search filters from the query below. "
        "Respond with ONLY a JSON object with keys: "
        '"description" (string of search keywords), '
        '"size" (a size like "M"/"L"/"8", or null), '
        '"max_price" (a number in dollars, or null).\n'
        f"Query: {query}"
    )
    try:
        client = tools._get_groq_client()
        response = client.chat.completions.create(
            model=tools._MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        text = (response.choices[0].message.content or "").strip()
        text = re.sub(r"^```(?:json)?|```$", "", text).strip()  # tolerate code fences
        data = json.loads(text)

        description = str(data.get("description") or "").strip()
        if not description:
            return None
        size = data.get("size")
        size = str(size).strip() if size else None
        max_price = data.get("max_price")
        max_price = float(max_price) if max_price is not None else None
        return {"description": description, "size": size, "max_price": max_price}
    except Exception:
        return None


def _no_results_message(parsed: dict) -> str:
    """Actionable error string for the empty-search branch — echoes the filters back
    and, when any were set, admits the retry ladder already tried loosening them
    (Stretch 1) so the message doesn't suggest a fix we silently attempted."""
    msg = f"I couldn't find any listings matching '{parsed['description']}'"
    if parsed["size"]:
        msg += f" in size {parsed['size']}"
    if parsed["max_price"] is not None:
        msg += f" under ${parsed['max_price']:g}"

    relaxed = [name for name, on in
               (("size", parsed["size"]), ("price", parsed["max_price"] is not None)) if on]
    if relaxed:
        noun = "filter" if len(relaxed) == 1 else "filters"
        msg += f" — even after dropping the {' and '.join(relaxed)} {noun}"

    msg += ". Try broader search terms (e.g. 'dress')."
    return msg


# ── retry ladder (Stretch 1) ──────────────────────────────────────────────────

def _retry_note(item: dict, *, dropped_size: bool, dropped_price: bool) -> str:
    """Banner text for a recovered (off-spec) search: name the filter(s) the ladder
    loosened and the surfaced item's real size/price, so the user understands why an
    off-spec piece is being shown. (Wording is a UI/error-copy choice — tests assert
    the contract, i.e. that the dropped filter name + the item's size/price appear.)"""
    relaxed = [name for name, on in
               (("size", dropped_size), ("price", dropped_price)) if on]
    noun = "filter" if len(relaxed) == 1 else "filters"
    return (
        f"↔ No exact match — I loosened the {' and '.join(relaxed)} {noun} to find this. "
        f"Closest piece: size {item['size']}, ${item['price']:g}."
    )


def _search_with_fallback(
    description: str, size: str | None, max_price: float | None
) -> tuple[list[dict], str | None]:
    """Stretch 1 — ordered relaxation ladder. Return ``(results, retry_note)``.

    Runs `search_listings` on a ladder of progressively looser filters and returns
    the first non-empty result set, along with a note describing what was loosened:

      - **Attempt 0 (exact):** the query as given. A hit here ⇒ ``retry_note = None``.
      - **Attempt 1 (drop size):** only if a `size` was set.
      - **Attempt 2 (drop size + price):** only if a `max_price` was set.

    No-op attempts (nothing to relax) are skipped, and `description` is never dropped.
    If every applicable attempt is empty, return ``([], None)``. Pure and never raises
    (delegates entirely to `search_listings`, which never raises).
    """
    # Attempt 0 — exact match wins outright, no note.
    results = search_listings(description, size, max_price)
    if results:
        return results, None

    # Attempt 1 — drop the size filter (skip if there was no size to drop).
    if size is not None:
        results = search_listings(description, None, max_price)
        if results:
            return results, _retry_note(results[0], dropped_size=True, dropped_price=False)

    # Attempt 2 — drop size + price (skip if there was no price to drop). Only the
    # filters the user actually set count as "loosened" in the note.
    if max_price is not None:
        results = search_listings(description, None, None)
        if results:
            return results, _retry_note(
                results[0], dropped_size=size is not None, dropped_price=True
            )

    # Nothing left to relax and still empty.
    return [], None


# ── style-profile helpers (Stretch 3) ────────────────────────────────────────

def _empty_profile() -> dict:
    return {"style_tags": {}, "colors": {}, "categories": {}, "brands": {},
            "price_sum": 0.0, "price_count": 0, "runs": 0}


def _seed_profile_from_wardrobe(wardrobe: dict) -> dict:
    """Build a starter profile from the wardrobe so cold-start re-ranking already
    reflects the user's existing taste rather than being completely neutral."""
    profile = _empty_profile()
    for item in wardrobe.get("items", []):
        for tag in item.get("style_tags", []):
            profile["style_tags"][tag] = profile["style_tags"].get(tag, 0) + 1
        for color in item.get("colors", []):
            profile["colors"][color] = profile["colors"].get(color, 0) + 1
        cat = item.get("category", "")
        if cat:
            profile["categories"][cat] = profile["categories"].get(cat, 0) + 1
    return profile


def _load_profile(wardrobe: dict) -> dict:
    """Read the style profile from PROFILE_PATH.

    On a missing or corrupt file, seed the profile from the wardrobe (non-empty
    wardrobe) or return an empty skeleton (empty wardrobe). Never raises.
    """
    try:
        text = pathlib.Path(PROFILE_PATH).read_text(encoding="utf-8")
        return json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        if wardrobe.get("items"):
            return _seed_profile_from_wardrobe(wardrobe)
        return _empty_profile()


def _update_profile(profile: dict, selected_item: dict) -> dict:
    """Fold the selected listing's signals into the profile and return the updated copy.

    Increments counts for each style_tag, color, category, and (if non-null) brand
    by 1; accumulates price into price_sum/price_count; bumps runs.
    """
    p = json.loads(json.dumps(profile))  # shallow-safe copy via JSON round-trip

    for tag in selected_item.get("style_tags", []):
        p["style_tags"][tag] = p["style_tags"].get(tag, 0) + 1
    for color in selected_item.get("colors", []):
        p["colors"][color] = p["colors"].get(color, 0) + 1
    cat = selected_item.get("category", "")
    if cat:
        p["categories"][cat] = p["categories"].get(cat, 0) + 1
    brand = selected_item.get("brand") or ""
    if brand:
        p["brands"][brand] = p["brands"].get(brand, 0) + 1

    price = selected_item.get("price")
    if price is not None:
        p["price_sum"] = p.get("price_sum", 0.0) + float(price)
        p["price_count"] = p.get("price_count", 0) + 1

    p["runs"] = p.get("runs", 0) + 1
    return p


def _save_profile(profile: dict) -> None:
    """Write the profile dict to PROFILE_PATH as formatted JSON. Never raises."""
    try:
        pathlib.Path(PROFILE_PATH).write_text(
            json.dumps(profile, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _profile_note(profile: dict) -> str | None:
    """Return a banner string from the profile's top taste signals, or None if the
    profile is cold (no style_tags or colors with nonzero counts).

    Format: "↑ Ranked for your style — you tend toward y2k, vintage, white"
    (top 3 signals across style_tags and colors combined, sorted by count desc).
    """
    signals = list(profile.get("style_tags", {}).items()) + \
              list(profile.get("colors", {}).items())
    signals = [(tag, count) for tag, count in signals if count > 0]
    if not signals:
        return None
    top = sorted(signals, key=lambda x: x[1], reverse=True)[:3]
    taste_str = ", ".join(tag for tag, _ in top)
    return f"↑ Ranked for your style — you tend toward {taste_str}"


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "retry_note": None,          # Stretch 1: set when the ladder loosened a filter
        "selected_item": None,       # top result, passed into suggest_outfit
        "price_check": None,         # Stretch 2: compare_price verdict dict (None on error path)
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
        "style_profile": None,       # Stretch 3: loaded at run start, updated on selection
        "profile_note": None,        # Stretch 3: banner from pre-update profile taste signals
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.
    """
    session = _new_session(query, wardrobe)

    # Step 2 — parse (hybrid): regex first; if it leaves no description, ask the LLM;
    # if that fails too, fall back to the raw query so search always gets usable input.
    parsed = _parse_query(query)
    if not parsed["description"].strip():
        llm = _llm_parse_query(query)
        if llm:
            parsed = {
                "description": llm["description"],
                "size": llm["size"] or parsed["size"],
                "max_price": parsed["max_price"]
                if llm["max_price"] is None
                else llm["max_price"],
            }
    if not parsed["description"].strip():
        parsed["description"] = query.strip()
    session["parsed"] = parsed

    # Step 3a (Stretch 3) — load style profile before the search.
    session["style_profile"] = _load_profile(wardrobe)

    # Step 3b — search with fallback (Stretch 1): exact, then relax size, then size+price.
    results, retry_note = _search_with_fallback(
        parsed["description"], parsed["size"], parsed["max_price"]
    )
    session["search_results"] = results
    session["retry_note"] = retry_note

    # Step 4 — the conditional branch: nothing even after loosening => error and stop.
    # suggest_outfit and rank_by_profile are never called with empty input.
    if not session["search_results"]:
        session["error"] = _no_results_message(parsed)
        return session

    # Step 5 (Stretch 3) — re-rank survivors by style profile; select top result.
    pre_update_profile = session["style_profile"]
    ranked = rank_by_profile(session["search_results"], pre_update_profile)
    session["search_results"] = ranked
    selected = ranked[0]
    session["selected_item"] = selected
    session["profile_note"] = _profile_note(pre_update_profile)

    # Step 6 (Stretch 2) — price check: non-branching step after selection.
    session["price_check"] = compare_price(selected, load_listings())

    # Step 7 (Stretch 3) — learn from the selection and persist.
    session["style_profile"] = _update_profile(pre_update_profile, selected)
    _save_profile(session["style_profile"])

    # Steps 8–9 — style it, caption it, return the filled session.
    session["outfit_suggestion"] = suggest_outfit(selected, wardrobe)
    session["fit_card"] = create_fit_card(session["outfit_suggestion"], selected)
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
