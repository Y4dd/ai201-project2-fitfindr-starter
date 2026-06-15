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
import re

import tools
from tools import search_listings, suggest_outfit, create_fit_card


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
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
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

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
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

    # Step 3 — search with fallback (Stretch 1): exact, then relax size, then size+price.
    # _search_with_fallback returns the first non-empty result set plus a retry_note
    # naming what (if anything) it had to loosen to find a match.
    results, retry_note = _search_with_fallback(
        parsed["description"], parsed["size"], parsed["max_price"]
    )
    session["search_results"] = results
    session["retry_note"] = retry_note

    # Step 4 — the conditional branch: nothing even after loosening => error and stop.
    # suggest_outfit is never called with empty input.
    if not session["search_results"]:
        session["error"] = _no_results_message(parsed)
        return session

    # Steps 5–7 — a match: style it, caption it, return the filled session.
    selected = session["search_results"][0]
    session["selected_item"] = selected
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
