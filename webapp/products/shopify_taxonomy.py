"""
Matches a free-text category guess (from AI extraction) to a real entry
in Shopify's Standard Product Taxonomy. Shopify's CSV importer only
accepts a "Product Category" value that EXACTLY matches a taxonomy
breadcrumb or ID — anything else is silently dropped and the product is
left "uncategorized" on import. Since an LLM asked to name a category
will happily invent a plausible-sounding but non-existent path (e.g.
"Apparel & Accessories > Clothing > Hats" instead of the real "...
Clothing Accessories > Hats > Baseball Caps"), every AI guess is snapped
to the nearest real category here before it's written to the CSV.

Matching is TF-IDF-style: generic words that appear across hundreds of
categories ("Home", "Apparel", "Accessories") barely count, while words
specific to few categories ("candle", "trucker", "hoodie") dominate the
score — otherwise broad ancestor words drown out the actual product type.

Data source: Shopify's public product-taxonomy repo (dist/en/categories.txt),
vendored locally in data/shopify_categories.txt so this needs no network
call and no extra AI/API cost.
"""

import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "shopify_categories.txt"
STOPWORDS = {"a", "an", "the", "and", "or", "for", "with", "of", "in", "on", "to", "other"}
MIN_SCORE = 1.2  # calibrated against real product queries — see tests


def _normalize(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {_normalize(w) for w in words if w not in STOPWORDS and len(w) > 2}


@lru_cache(maxsize=1)
def _load_categories() -> tuple[list[dict], dict]:
    categories = []
    with open(DATA_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Split on " : " (spaces around the colon) — a plain ":" would
            # also match inside the "gid://..." scheme prefix itself.
            gid_part, sep, breadcrumb = line.partition(" : ")
            if not sep:
                continue
            breadcrumb = breadcrumb.strip()
            if not breadcrumb:
                continue
            leaf = breadcrumb.rsplit(">", 1)[-1].strip()
            categories.append({
                "id": gid_part.strip().rsplit("/", 1)[-1],
                "breadcrumb": breadcrumb,
                "leaf_tokens": _tokenize(leaf),
                "all_tokens": _tokenize(breadcrumb),
            })

    doc_freq = Counter()
    for cat in categories:
        doc_freq.update(cat["all_tokens"])
    n = len(categories)
    idf = {tok: math.log(n / df) for tok, df in doc_freq.items()}
    return categories, idf


def top_candidates(query: str, n: int = 25) -> list[str]:
    """Return up to `n` real taxonomy breadcrumbs ranked by lexical
    overlap with `query`. This is a cheap first pass to narrow ~14,600
    categories down to a shortlist — too imprecise to trust alone (bag-
    of-words scoring confuses e.g. "Candles" with "Scented Sachets"), so
    callers should have an LLM pick the final match from this shortlist."""
    categories, idf = _load_categories()
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    scored = []
    for cat in categories:
        score = 0.0
        for tok in query_tokens:
            weight = idf.get(tok)
            if not weight:
                continue
            if tok in cat["leaf_tokens"]:
                score += weight * 3
            elif tok in cat["all_tokens"]:
                score += weight
        if score >= MIN_SCORE:
            scored.append((score, cat["breadcrumb"]))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [breadcrumb for _, breadcrumb in scored[:n]]


def find_best_category(query: str) -> str:
    """Cheap lexical-only best guess (no AI call) — kept for callers that
    don't need the higher accuracy of `pick_category`."""
    candidates = top_candidates(query, n=1)
    return candidates[0] if candidates else ""


def pick_category(title: str, product_type: str, category_guess: str) -> str:
    """Shortlist real categories lexically, then have the AI pick the one
    correct match by NUMBER (asking an LLM to echo text back verbatim is
    unreliable — it'll paraphrase or truncate; picking a list index is
    the robust pattern), so the result is always either a real taxonomy
    breadcrumb or empty, never an invented path. Costs one extra cheap
    OpenRouter text call per product on top of the Firecrawl extraction."""
    from . import openrouter_client  # local import: keeps this module usable without the AI client for pure-lexical callers

    query = f"{category_guess} {product_type} {title}"
    candidates = top_candidates(query, n=25)
    if not candidates:
        return ""

    numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates, start=1))
    prompt = (
        f'A product is titled "{title}"'
        + (f" (product type: {product_type})" if product_type else "")
        + ".\n\nWhich numbered category below is the single best match for "
        "this product? Reply with ONLY the number, nothing else. If none "
        f"of them fit well, reply with 0.\n\n{numbered}"
    )
    try:
        raw = openrouter_client.text_chat(prompt, max_tokens=500).strip()
    except Exception:
        return ""

    match = re.search(r"\d+", raw)
    if not match:
        return ""
    index = int(match.group())
    if 1 <= index <= len(candidates):
        return candidates[index - 1]
    return ""
