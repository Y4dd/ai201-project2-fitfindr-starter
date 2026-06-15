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
import statistics
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


# ── Tool 4: compare_price (Stretch 2) ─────────────────────────────────────────

# Verdict templates per band (wording locked in planning.md). Prices use :g so a
# whole-dollar float renders without the trailing ".0" (18.0 -> "18", 21.5 -> "21.5").
_PRICE_VERDICTS = {
    "great_deal": "💰 Great deal — ${price:g} is below the ${median:g} median for "
                  "{category} ({n} comparable listings).",
    "fair": "💰 Fair price — ${price:g} sits near the ${median:g} median for "
            "{category} ({n} comparable listings).",
    "high": "💰 Priced high — ${price:g} is above the ${median:g} median for "
            "{category} ({n} comparable listings).",
}


def compare_price(new_item: dict, comparables: list[dict]) -> dict:
    """
    Judge whether new_item's asking price is a good deal versus same-category peers.

    Pure and deterministic — no LLM, no filesystem. run_agent passes the full dataset
    (load_listings()) as `comparables`; the tool self-selects the peer group, so it can
    also be called standalone in tests with any list of listing dicts.

    Args:
        new_item:    The listing being judged. Reads its category, price, and id.
        comparables: Candidate listings to compare against. The tool narrows these to
                     same-category peers, excluding new_item itself by id.

    Returns:
        A dict with six keys:
            band          — "great_deal" | "fair" | "high" | "insufficient_data"
            verdict       — a human-readable sentence built from the numbers
            price         — new_item's price (float)
            median        — median peer price (float), or None when insufficient
            n_comparables — number of same-category peers used (int)
            category      — new_item's category (str)

    Banding (see planning.md, Tool 4): percentile = share of peers priced strictly
    below new_item; <= 25 -> great_deal, > 75 -> high, else fair.

    Failure mode: fewer than 3 same-category peers (e.g. any accessories item, which
    has only 2 peers in our data) -> band="insufficient_data", median=None. Never raises.
    """
    category = new_item["category"]
    price = new_item["price"]
    item_id = new_item.get("id")

    # Peer group: same category, excluding the item itself by id.
    peers = [
        c for c in comparables
        if c["category"] == category and c.get("id") != item_id
    ]
    n = len(peers)

    # Guard: too few peers to judge — graceful insufficient_data (never raises).
    if n < 3:
        return {
            "band": "insufficient_data",
            "verdict": (
                f"💰 Not enough comparable {category} listings to judge this "
                f"price (only {n} found)."
            ),
            "price": price,
            "median": None,
            "n_comparables": n,
            "category": category,
        }

    prices = [c["price"] for c in peers]
    median = statistics.median(prices)
    percentile = 100 * sum(1 for p in prices if p < price) / n

    if percentile <= 25:
        band = "great_deal"
    elif percentile > 75:
        band = "high"
    else:
        band = "fair"

    verdict = _PRICE_VERDICTS[band].format(
        price=price, median=median, category=category, n=n
    )
    return {
        "band": band,
        "verdict": verdict,
        "price": price,
        "median": median,
        "n_comparables": n,
        "category": category,
    }


# ── Tool 6 seam: Google Trends fetch (isolated so tests can monkeypatch it) ───

def _fetch_trend_ranking(terms: list[str]) -> list[str]:
    """Call Google Trends via pytrends to rank `terms` by live search momentum.

    Returns `terms` ordered by mean interest over the last 3 months (descending),
    with alphabetical tie-breaking for stability. Raises on any failure (429,
    network error, empty response) so `check_trends` can handle it gracefully.
    """
    from pytrends.request import TrendReq  # lazy import — pytrends only needed at runtime

    pytrends = TrendReq(hl="en-US", tz=360)
    pytrends.build_payload(terms, timeframe="today 3-m")
    df = pytrends.interest_over_time()
    if df is None or df.empty:
        raise ValueError("Google Trends returned no data")
    term_cols = [c for c in df.columns if c in terms]
    if not term_cols:
        raise ValueError("No matching term columns in Google Trends response")
    means = df[term_cols].mean()
    return sorted(means.index.tolist(), key=lambda t: (-means[t], t))


# ── Tool 5: rank_by_profile (Stretch 3) ──────────────────────────────────────

def rank_by_profile(listings: list[dict], profile: dict) -> list[dict]:
    """Re-rank an already-relevance-sorted list of listings by the user's learned
    style profile, blending search-position score with profile-affinity score.

    Pure and deterministic — no LLM, no filesystem. Returns the same listings in
    a (possibly new) order; never adds or drops items.

    Args:
        listings: Listings already sorted by search relevance (best first).
        profile:  Style-profile dict with keys:
                    style_tags  — {tag: count}
                    colors      — {color: count}
                    categories  — {category: count}
                    brands      — {brand: count}
                    price_sum, price_count, runs  (not used in scoring)

    Returns:
        The same listing dicts reordered by a blended score:
            final = 0.6 * rel_norm + 0.4 * aff_norm
        where rel_norm encodes original position and aff_norm is min-max affinity.
        Stable sort: equal scores preserve the incoming (relevance) order.
        Never raises.
    """
    n = len(listings)
    if n == 0:
        return listings

    # Step 1: compute raw affinity for each listing.
    style_weights = profile.get("style_tags", {})
    color_weights = profile.get("colors", {})
    cat_weights = profile.get("categories", {})
    brand_weights = profile.get("brands", {})

    affinities = []
    for listing in listings:
        aff = sum(style_weights.get(tag, 0) for tag in listing.get("style_tags", []))
        aff += sum(color_weights.get(c, 0) for c in listing.get("colors", []))
        aff += cat_weights.get(listing.get("category", ""), 0)
        aff += brand_weights.get(listing.get("brand") or "", 0)
        affinities.append(aff)

    # Step 2: normalize affinities (min-max); all-equal (incl. all-zero) → 0.0.
    aff_min, aff_max = min(affinities), max(affinities)
    if aff_max == aff_min:
        aff_norms = [0.0] * n
    else:
        aff_norms = [(a - aff_min) / (aff_max - aff_min) for a in affinities]

    # Step 3: blend with relevance position (best index = 1.0, worst = 0.0).
    scored = []
    for i, (listing, aff_norm) in enumerate(zip(listings, aff_norms)):
        rel_norm = (n - 1 - i) / (n - 1) if n > 1 else 1.0
        final = 0.6 * rel_norm + 0.4 * aff_norm
        scored.append((final, listing))

    # Step 4: stable sort descending — equal scores keep incoming (relevance) order.
    scored.sort(key=lambda x: x[0], reverse=True)
    return [listing for _, listing in scored]


# ── Tool 6: check_trends (Stretch 4) ─────────────────────────────────────────

def check_trends(
    new_item: dict,
    listings: list[dict],
    size: str | None = None,
) -> dict:
    """Check which styles are trending in live Google Trends among size-available listings.

    Args:
        new_item: The selected listing (reads its style_tags and title).
        listings: Full dataset from load_listings() — used only to build the size bucket.
        size:     The requested size from the parsed query. None → all listings.

    Returns a dict with six keys:
        band              — "on_trend" | "off_trend" | "insufficient_data" | "unavailable"
        verdict           — human-readable one-line string for the banner
        trending          — top-3 style tags by search momentum ([] on insufficient/unavailable)
        item_tags_on_trend— item's style_tags that appear in trending ([] when off/unavailable)
        size              — the scope used (or None)
        source            — "google_trends" on a live hit, else "unavailable"

    Never raises — a failed Trends call degrades to band="unavailable".
    """
    # Step 1: size bucket — reuse search_listings' token-match rule.
    if size is not None:
        wanted = size.strip().lower()
        bucket = [
            l for l in listings
            if wanted in re.split(r"[/\s]+", l["size"].lower())
        ]
    else:
        bucket = list(listings)
    n = len(bucket)

    # Step 2: insufficient data guard — short-circuit before any network call.
    if n < 3:
        return {
            "band": "insufficient_data",
            "verdict": (
                f"🔍 Not enough size {size} listings to assemble a trend read "
                f"(only {n} found)."
            ),
            "trending": [],
            "item_tags_on_trend": [],
            "size": size,
            "source": "unavailable",
        }

    # Step 3: build up to 5 candidate style tags — item's own tags first so it can be judged.
    item_tags = new_item.get("style_tags", [])
    candidates: list[str] = list(item_tags)
    tag_counts: dict[str, int] = {}
    for l in bucket:
        for tag in l.get("style_tags", []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for tag, _ in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0])):
        if tag not in candidates:
            candidates.append(tag)
        if len(candidates) >= 5:
            break

    if not candidates:
        return _unavailable_dict(size)

    # Step 4: live ranking via the seam — tests monkeypatch this (like _get_groq_client).
    try:
        ranked = _fetch_trend_ranking(candidates)
        trending = ranked[:3]
    except Exception:
        return _unavailable_dict(size)

    # Steps 5 & 6: on-trend tags and band.
    item_tags_on_trend = [tag for tag in item_tags if tag in trending]
    band = "on_trend" if item_tags_on_trend else "off_trend"

    if band == "on_trend":
        tags_str = " & ".join(item_tags_on_trend[:2])
        item_name = new_item.get("title", "item")
        size_scope = f" (within what's available in size {size})" if size else ""
        verdict = (
            f"🔥 On-trend — your {item_name}'s {tags_str} styles are among the "
            f"top-rising fashion searches on Google right now{size_scope}."
        )
    else:
        rising_str = ", ".join(trending)
        size_label = f" in {size}" if size else ""
        verdict = (
            f"🌿 Under the radar — none of this piece's styles are in the top "
            f"fashion searches right now (currently rising{size_label}: {rising_str})."
        )

    return {
        "band": band,
        "verdict": verdict,
        "trending": trending,
        "item_tags_on_trend": item_tags_on_trend,
        "size": size,
        "source": "google_trends",
    }


def _unavailable_dict(size: str | None) -> dict:
    return {
        "band": "unavailable",
        "verdict": "🌐 Couldn't reach Google Trends just now — trend check unavailable for this run.",
        "trending": [],
        "item_tags_on_trend": [],
        "size": size,
        "source": "unavailable",
    }
