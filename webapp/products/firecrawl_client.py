"""
Thin client for Firecrawl's scrape/crawl/batch-scrape API, used by the
Product Crawl tool to pull structured product data (title/price/images/
variants/SEO) from an external product page, an entire category page, or
a hand-picked list of product URLs.

Auth: set env var FIRECRAWL_API_KEY (firecrawl.dev -> API Keys).

Pricing (per Firecrawl's published rates): 1 credit per page scraped +
4 credits per page for JSON/schema extraction = 5 credits/product page,
for /scrape, /crawl and /batch/scrape alike.
"""

import json
import os
import re
import time
from collections import Counter
from urllib.parse import urlparse

import httpx

from . import openrouter_client

API_BASE = "https://api.firecrawl.dev/v2"

CREDITS_PER_PAGE = 5  # 1 base scrape + 4 for JSON extraction
POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 280  # stay under typical platform request timeouts
MAX_BATCH_URLS = 50

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "The product's real name/title as shown on the page"},
        "description_html": {
            "type": "string",
            "description": (
                "The full product description as clean HTML — preserve the "
                "real structure (paragraphs as <p>, line breaks as <br>, "
                "bullet lists as <ul><li>, a spec table as <table> if the "
                "page has one). This gets pasted directly into Shopify's "
                "product description field, so it must be valid HTML "
                "markup, never plain text or markdown."
            ),
        },
        "price": {"type": "number", "description": "The current selling price as a plain number, no currency symbol"},
        "compare_at_price": {
            "type": "number",
            "description": "The original/struck-through price if the product is on sale, otherwise omit entirely",
        },
        "sku": {
            "type": "string",
            "description": (
                "The product's real SKU/model number if shown on the "
                "page. If none is shown, COMPOSE a short, sensible SKU "
                "yourself from the product's real attributes (type + key "
                "traits, e.g. 'HOODIE-FLEECE-GRY') — uppercase letters, "
                "digits and hyphens only, no spaces. Never leave this "
                "field empty."
            ),
        },
        "barcode": {"type": "string", "description": "UPC/EAN/ISBN barcode if shown on the page, else omit"},
        "weight_grams": {"type": "number", "description": "Shipping weight in grams if shown on the page, else omit"},
        "seo_title": {
            "type": "string",
            "description": (
                "Write a compelling, search-optimized product title for "
                "this listing, under 70 characters. Base it on the real "
                "product — you may tighten or reorder the wording, but "
                "never invent features or claims that aren't on the page."
            ),
        },
        "seo_description": {
            "type": "string",
            "description": (
                "Write a search-engine meta description for this product, "
                "120-160 characters, summarizing its real key selling "
                "points from the page — no invented claims."
            ),
        },
        "product_category": {
            "type": "string",
            "description": (
                "The single best-fit Shopify standard product category "
                "path for this item, e.g. 'Apparel & Accessories > "
                "Clothing > Shirts & Tops' or 'Home & Garden > Decor > "
                "Home Fragrances > Candles' — infer it from what the "
                "product actually is."
            ),
        },
        "product_type": {"type": "string", "description": "A short product type label, e.g. 'Hoodie', 'Scented Candle'"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "5-8 relevant, lowercase search/browse tags for this product (no # symbol)",
        },
        "images": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every product image URL shown on the page, as full absolute URLs",
        },
        "options": {
            "type": "array",
            "description": (
                "Every selectable option dimension shown on the page (up "
                "to 3), e.g. Color, Size, Finish/Scent/Material — read "
                "this off the swatches/buttons/dropdowns, NOT off which "
                "one happens to be pre-selected."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The option's name, e.g. 'Color', 'Size'"},
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "EVERY selectable value visible for this "
                            "option — every color swatch label, every "
                            "size button, every dropdown entry — not just "
                            "the one currently selected/shown by default. "
                            "If the page shows 4 color swatches, list all "
                            "4, even though only 1 is active on load."
                        ),
                    },
                },
            },
        },
        "selected_variant": {
            "type": "object",
            "description": (
                "The ONE specific option combination that's selected/"
                "shown by default on the page right now, with whatever "
                "price/sku/barcode/weight/image is confirmed for that "
                "exact combination if it differs from the base product-"
                "level price/sku above. Omit any field you can't confirm. "
                "Omit this whole object if the product has no options."
            ),
            "properties": {
                "option_values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The selected value for each dimension, in the same order as `options` above, e.g. [\"Walnut\", \"39\\\"W x 29\\\"H x 10\\\"D\"]",
                },
                "price": {"type": "number"},
                "sku": {"type": "string"},
                "barcode": {"type": "string"},
                "weight_grams": {"type": "number"},
                "image": {"type": "string"},
            },
        },
        "option_value_images": {
            "type": "array",
            "description": (
                "Map each value of whichever ONE option dimension "
                "visually changes the product's look (almost always "
                "Color/Finish/Material — never Size) to the image that "
                "shows it. Many product galleries actually contain a "
                "photo for every color even though only one shows at a "
                "time — check the FULL `images` list (thumbnails, gallery "
                "images) and image filenames for color/finish hints (a "
                "filename containing 'Walnut' or 'Black' etc.), not just "
                "the one large photo currently displayed. Include an "
                "entry for every value you can confidently match to a "
                "real image from `images`; skip values you can't match — "
                "never guess or reuse the wrong photo for a color it "
                "doesn't show. Omit this whole field if no option "
                "visually changes the product's appearance."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "option_name": {"type": "string", "description": "Which option this maps, e.g. 'Color'"},
                    "value": {"type": "string", "description": "The specific value, e.g. 'Walnut'"},
                    "image": {"type": "string", "description": "The matching image URL, copied exactly from `images`"},
                },
            },
        },
        "option_value_prices": {
            "type": "array",
            "description": (
                "Map each value of whichever option dimension actually "
                "changes the price (very often Size — a larger size "
                "usually costs more) to its own confirmed price. Check "
                "the whole page: size/option buttons sometimes show a "
                "price or 'from $X' next to each one, or a size chart / "
                "pricing table lists them. Include an entry ONLY for "
                "values whose price you can actually confirm from the "
                "page — never guess, interpolate, or assume every value "
                "costs the same as the selected one. Omit this whole "
                "field if price doesn't vary by option or you can't "
                "confirm any per-value prices."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "option_name": {"type": "string", "description": "Which option this maps, e.g. 'Size'"},
                    "value": {"type": "string", "description": "The specific value, e.g. '60\"L x 30\"H x 12\"D'"},
                    "price": {"type": "number"},
                },
            },
        },
    },
    "required": ["title"],
}

PRODUCT_PROMPT = (
    "Extract this e-commerce product's real data exactly as shown on the "
    "page — title, full description as HTML, price, compare-at/original "
    "price if on sale, barcode, weight, images. Additionally WRITE an "
    "SEO title, an SEO meta description, a best-fit product category, a "
    "short product type, 5-8 browse tags, and a SKU for the product "
    "(use the real one if shown, otherwise compose a short sensible one "
    "yourself — see the sku field's own instructions). Compose these "
    "fields yourself from the real product content, don't just copy raw "
    "page text for them.\n\n"
    "PRICE — this is a common mistake, read carefully: use ONLY the "
    "actual selling price shown in the product's main buy box, right by "
    "the title/Add-to-Cart button (it usually repeats identically in "
    "several places on the page — the header, a sticky bar, etc.). "
    "IGNORE every other price-looking number on the page: Shop Pay / "
    "Klarna / Afterpay / Affirm installment previews ('4 payments of "
    "$X'), financing/BNPL calculators, a 'coupon applied' or 'final "
    "price after code' popup, a store-wide bundle/threshold banner "
    "('Get 10% off orders $2,000+', 'Buy 2 save 15%' — these are "
    "conditional on cart total, NOT this item's price), shipping "
    "thresholds, or prices of OTHER products (related/recommended "
    "items). Those are real numbers on the page but they are NOT this "
    "product's actual price — do not let them leak into `price`, "
    "`compare_at_price`, `selected_variant.price`, or "
    "`option_value_prices`; never compute a percentage off the real "
    "price and report that as if it were a confirmed variant price.\n\n"
    "OPTIONS/VARIANTS — another common mistake: scan the ENTIRE page top "
    "to bottom for EVERY group of selectable swatches/buttons/dropdowns "
    "before finalizing `options` — do not stop after the first one or "
    "two you find. Products commonly have 2-3 such groups placed one "
    "after another (e.g. Color, then Size, then a further Finish / "
    "Material / Style group below the size picker) — actively check for "
    "a third group even after you've already found two. INCLUDE a group "
    "even if it currently shows only ONE value/button (e.g. a 'Size:' "
    "label with just one fixed measurement and no alternative to pick) "
    "— a group with a single value is still a real option and must "
    "still appear in `options` with that one value; never drop a whole "
    "group just because this particular product doesn't offer a choice "
    "for it. For each group, your job is ONLY to read off the full list "
    "of values available — do NOT try to figure out or guess a price/"
    "SKU for every combination yourself, and do NOT report only the one "
    "value that happens to be pre-selected when the page loads. If the "
    "page shows 4 color swatches, `options` must list all 4 colors even "
    "though only 1 is visually active by default. Separately, "
    "`selected_variant` captures "
    "just the ONE combination that's currently active, with whatever "
    "price/sku/barcode/weight/image is confirmed for that exact "
    "combination. Additionally, fill in `option_value_images` so every "
    "color/finish gets its own product photo instead of just the "
    "currently-selected one, and `option_value_prices` if price varies "
    "by option (e.g. by size) — see those fields' own instructions.\n\n"
    "CRITICAL: only extract if this page IS one specific product's own "
    "page (it has ONE product's dedicated price/add-to-cart/buy box) AND "
    "the page actually loaded real content. Return an empty object {} — "
    "never invent plausible-looking placeholder data — in ANY of these "
    "cases:\n"
    "- The page LISTS many different products side by side (a "
    "collection, category, search-results, or paginated browse page). "
    "Never pick 'the first' or 'the featured' product off a listing page "
    "and describe it as if the whole page were that product's page.\n"
    "- The page is a 404 / \"page not found\" / error page, is blocked "
    "(e.g. \"blocked by an extension\", a bot-check, a login wall), or "
    "otherwise has no real product content — even if it superficially "
    "contains e-commerce-sounding boilerplate text (cart widgets, "
    "\"Continue shopping\" links, chat widgets). If you cannot point to "
    "an actual product name, price and description that are really on "
    "the page, return {} — do not fill in a generic/plausible-sounding "
    "product as a guess."
)


def _check_key() -> None:
    if not os.environ.get("FIRECRAWL_API_KEY"):
        raise RuntimeError("FIRECRAWL_API_KEY env var is not set.")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}",
        "Content-Type": "application/json",
    }


def _json_format() -> dict:
    return {"type": "json", "schema": PRODUCT_SCHEMA, "prompt": PRODUCT_PROMPT}


def _page_failed(metadata: dict) -> bool:
    """Ground-truth check, not a prompt instruction: the extraction LLM
    reliably invents a plausible-looking product even when fed a 404 or
    blocked page's boilerplate (cart widgets, "Continue shopping" links)
    despite explicit instructions not to — so never trust its output for
    a page whose real HTTP status wasn't a success."""
    status = metadata.get("statusCode")
    return status is not None and not (200 <= status < 300)


_PRICE_NUMBER_RE = re.compile(r"\d[\d,]*\.\d{2}\b")


def _price_frequencies(markdown: str) -> Counter:
    """Count how many times each price-looking number ("123.45" style)
    appears in the page's raw text. The product's real, displayed price
    typically repeats across the page (header, sticky add-to-cart bar,
    structured data); a one-off promotional number (a coupon popup's
    "final price", a Shop Pay/Klarna installment preview) usually
    doesn't — used to catch the extraction model picking up a real but
    irrelevant number instead of the actual price."""
    counts = Counter()
    for match in _PRICE_NUMBER_RE.finditer(markdown or ""):
        try:
            counts[round(float(match.group().replace(",", "")), 2)] += 1
        except ValueError:
            continue
    return counts


def _verify_and_fix_price(extracted: dict, markdown: str) -> None:
    """Cross-check price/compare_at_price against how often each number
    actually appears on the page — mutates `extracted` in place. Fixes
    the case where the model reports a real-but-unrelated number (a
    "coupon applied" popup, a BNPL installment preview) as the price:
    if `compare_at_price` is the one that actually repeats across the
    page while `price` barely appears, they've very likely been swapped/
    confused — swap them back and drop the now-baseless "discount"."""
    price = extracted.get("price")
    if price is None or not markdown:
        return
    compare_at = extracted.get("compare_at_price")
    freqs = _price_frequencies(markdown)
    try:
        price_freq = freqs.get(round(float(price), 2), 0)
    except (TypeError, ValueError):
        return
    if compare_at is not None:
        try:
            compare_freq = freqs.get(round(float(compare_at), 2), 0)
        except (TypeError, ValueError):
            compare_freq = 0
        if compare_freq >= 2 and compare_freq > price_freq:
            extracted["price"] = compare_at
            extracted["compare_at_price"] = None


_COMMON_DISCOUNT_FRACTIONS = (0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.5)


def _looks_like_computed_discount(candidate: float, base_price: float) -> bool:
    """True if `candidate` is suspiciously close to an exact round-
    percentage discount off `base_price` (95%, 90%, 85%... of it). A
    genuinely different variant's own price (a bigger size costing more)
    is very unlikely to land on an exact round percentage of another
    variant's price — but a store-wide "spend $X get 10% off" bundle
    banner produces exactly that pattern when misread as a specific
    variant's price."""
    if not base_price:
        return False
    return any(abs(candidate - base_price * frac) < 0.02 for frac in _COMMON_DISCOUNT_FRACTIONS)


def _verify_variant_prices(extracted: dict, markdown: str) -> None:
    """Same class of bug as _verify_and_fix_price, one level deeper: a
    store-wide bundle/threshold discount banner ("Get 10% off orders
    $2,000+") can get mistaken for a SPECIFIC variant's price in
    `selected_variant.price` or `option_value_prices`, the same way a
    coupon popup can get mistaken for the base price. Drop any such
    value unless it either repeats independently on the page (≥2 times)
    or isn't just a round-percentage fraction of the base price."""
    if not markdown:
        return
    base_price = extracted.get("price")
    if base_price is None:
        return
    try:
        base_price = float(base_price)
    except (TypeError, ValueError):
        return
    freqs = _price_frequencies(markdown)

    def is_suspect(value) -> bool:
        try:
            v = round(float(value), 2)
        except (TypeError, ValueError):
            return False
        if freqs.get(v, 0) >= 2:
            return False  # repeats independently on the page — trust it
        return _looks_like_computed_discount(v, base_price)

    selected = extracted.get("selected_variant")
    if isinstance(selected, dict) and selected.get("price") is not None and is_suspect(selected["price"]):
        selected["price"] = None

    for entry in (extracted.get("option_value_prices") or []):
        if isinstance(entry, dict) and entry.get("price") is not None and is_suspect(entry["price"]):
            entry["price"] = None


REFINE_MODEL = "anthropic/claude-opus-5"


def refine_with_ai(extracted: dict, markdown: str, model: str = REFINE_MODEL) -> dict:
    """Second pass over the SAME page content: Firecrawl's own internal
    extraction model isn't disclosed or configurable, and has shown real
    mistakes (a coupon-popup price mistaken for the real one, a missing
    option dimension, no per-size pricing). Hand the raw page text + the
    first-pass JSON to a premium reasoning model to verify, correct and
    complete it. Costs one extra OpenRouter call per product on top of
    the Firecrawl credits — best-effort: falls back to the first pass on
    any failure rather than losing the product entirely."""
    if not markdown:
        return extracted

    schema_hints = "\n".join(
        f"- {key}: {prop.get('description', '')}"
        for key, prop in PRODUCT_SCHEMA["properties"].items()
    )
    prompt = (
        "You are verifying and correcting structured e-commerce product "
        "data that another, less careful AI extracted from a web page. "
        "Treat the FIRST-PASS JSON below as a rough draft that may "
        "contain real mistakes — not ground truth. Re-derive everything "
        "from the RAW PAGE CONTENT.\n\n"
        "Specifically fix these known failure modes:\n"
        "1. PRICE: the real price is the one in the main buy box — "
        "never a Shop Pay/Klarna/Afterpay/Affirm installment preview, a "
        "coupon-popup 'final price after code', a store-wide bundle/"
        "threshold banner ('Get 10% off orders $2,000+' — conditional "
        "on cart total, not this item's price), or another product's "
        "price. NEVER compute a percentage off the real price yourself "
        "and report that as a confirmed variant price. If price "
        "genuinely varies by an option value (very common for size — "
        "bigger usually costs more), fill `option_value_prices` with "
        "every confirmed per-value price you can find written verbatim "
        "on the page (a price shown next to each option button, a "
        "size/pricing chart, etc.) — never guess, compute, or assume "
        "every value shares the selected one's price. If you cannot "
        "find per-value prices actually written on the page, leave "
        "`option_value_prices` empty rather than inventing one.\n"
        "2. OPTIONS: re-scan the ENTIRE raw text for every selectable "
        "option group — don't stop after 1-2; explicitly check for a "
        "3rd group (Finish/Material/Style, often placed right after "
        "Size). Also check the first-pass JSON didn't drop a group that "
        "only has ONE value (e.g. a 'Size:' label with a single fixed "
        "measurement and no alternative) — a single-value group is "
        "still a real option and belongs in `options` with that one "
        "value; add it back if missing.\n"
        "3. Everything else: keep it grounded in the raw text only, "
        "correct anything wrong, fill in anything the first pass "
        "missed — never invent data that isn't really on the page.\n\n"
        f"Field meanings:\n{schema_hints}\n\n"
        "Respond with ONLY the corrected JSON object in the exact same "
        "shape as the first-pass JSON, no markdown fences, no extra "
        "text.\n\n"
        f"RAW PAGE CONTENT:\n{markdown[:60000]}\n\n"
        f"FIRST-PASS JSON:\n{json.dumps(extracted, ensure_ascii=False)}"
    )

    try:
        raw = openrouter_client.text_chat(prompt, max_tokens=4000, model=model)
    except Exception:
        return extracted

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    try:
        refined = json.loads(cleaned)
        if isinstance(refined, dict) and refined.get("title"):
            return refined
    except (ValueError, json.JSONDecodeError):
        pass
    return extracted


def _extract_products(pages: list[dict], fallback_url: str) -> list[dict]:
    products = []
    for page in pages:
        extracted = page.get("json") or {}
        metadata = page.get("metadata") or {}
        if _page_failed(metadata):
            continue  # 404/blocked/error page — extraction on these is unreliable, skip
        if not extracted.get("title"):
            continue  # not a product page — skip (e.g. the category page itself)
        markdown = page.get("markdown") or ""
        extracted = refine_with_ai(extracted, markdown)
        _verify_and_fix_price(extracted, markdown)
        _verify_variant_prices(extracted, markdown)
        products.append({
            "source_url": metadata.get("sourceURL") or metadata.get("url") or fallback_url,
            "extracted": extracted,
        })
    return products


def scrape_product(url: str) -> dict:
    """Scrape one product URL and return its extracted structured data.
    Costs a fixed 5 credits (Firecrawl doesn't echo per-call credit spend
    on /scrape, only on /crawl and /batch/scrape status, so we report the
    known rate)."""
    _check_key()
    payload = {"url": url, "formats": ["markdown", _json_format()], "onlyMainContent": True}
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{API_BASE}/scrape", headers=_headers(), json=payload)
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        body = r.json()

    data = body.get("data") or {}
    extracted = data.get("json") or {}
    metadata = data.get("metadata") or {}
    if _page_failed(metadata):
        raise RuntimeError(
            f"Trang trả về lỗi HTTP {metadata.get('statusCode')} (không "
            "phải trang sản phẩm hợp lệ) — kiểm tra lại URL."
        )
    if not extracted.get("title"):
        raise RuntimeError(
            "Không trích xuất được dữ liệu sản phẩm từ URL này — có thể "
            "đây không phải trang sản phẩm, hoặc trang chặn crawler."
        )
    markdown = data.get("markdown") or ""
    extracted = refine_with_ai(extracted, markdown)
    _verify_and_fix_price(extracted, markdown)
    _verify_variant_prices(extracted, markdown)
    return {
        "products": [{"source_url": metadata.get("sourceURL") or url, "extracted": extracted}],
        "credits_used": CREDITS_PER_PAGE,
    }


def _poll_job(client: httpx.Client, status_url: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        sr = client.get(status_url, headers=_headers())
        if sr.is_error:
            raise RuntimeError(f"Firecrawl {sr.status_code}: {sr.text[:500]}")
        status_body = sr.json()
        status = status_body.get("status")
        if status == "completed":
            return status_body
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"Firecrawl job kết thúc với trạng thái: {status}.")
        if time.monotonic() > deadline:
            raise RuntimeError("Crawl quá lâu (>4-5 phút) — thử giảm số lượng trang rồi thử lại.")
        time.sleep(POLL_INTERVAL_SEC)


def discover_product_links(page_url: str, path_hint: str = "/products/") -> list[str]:
    """Scrape `page_url` for its real, rendered list of links (costs 1
    base credit — no JSON extraction requested), then keep only same-
    domain links whose path contains `path_hint`.

    Deliberately NOT using Firecrawl's own /crawl link-discovery here:
    tested against a real Shopify collection page, /crawl's own hop
    traversal found only sibling pagination URLs (?page=1, ?page=3...)
    and never the actual /products/ links on the page — likely because
    the storefront renders its product tiles in a way that isn't picked
    up by /crawl's lightweight discovery pass. A direct /scrape with the
    "links" format sees the real, fully-rendered link list instead."""
    _check_key()
    with httpx.Client(timeout=90) as client:
        r = client.post(
            f"{API_BASE}/scrape", headers=_headers(),
            json={"url": page_url, "formats": ["links"], "onlyMainContent": False},
        )
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        links = ((r.json().get("data") or {}).get("links")) or []

    domain = urlparse(page_url).netloc
    seen, product_links = set(), []
    for link in links:
        if urlparse(link).netloc != domain or path_hint not in link:
            continue
        if link not in seen:
            seen.add(link)
            product_links.append(link)
    return product_links


def crawl_category_page(page_url: str, limit: int) -> dict:
    """Discover the real product links on `page_url` (a category/
    collection/listing page — including a specific `?page=N` of it) and
    scrape each one. Total cost = 1 credit (link discovery) + 5 credits
    per product page scraped, capped at `limit` products."""
    links = discover_product_links(page_url)
    if not links:
        raise RuntimeError(
            "Không tìm thấy link sản phẩm nào (dạng /products/...) trên "
            "trang này — trang có thể dùng URL sản phẩm khác kiểu, hoặc "
            "không phải trang danh mục."
        )
    result = batch_scrape_products(links[: max(1, min(limit, MAX_BATCH_URLS))])
    result["credits_used"] = result.get("credits_used", 0) + 1  # the link-discovery scrape itself
    return result


def batch_scrape_products(urls: list[str]) -> dict:
    """Scrape a hand-picked list of known product URLs (no link discovery).
    Use when the user already has exact product links, e.g. pasted from a
    supplier's listing page — cheaper and more precise than /crawl for
    that case since nothing is discovered/guessed."""
    _check_key()
    urls = urls[:MAX_BATCH_URLS]
    payload = {"urls": urls, "formats": ["markdown", _json_format()], "onlyMainContent": True}

    with httpx.Client(timeout=60) as client:
        r = client.post(f"{API_BASE}/batch/scrape", headers=_headers(), json=payload)
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        job_id = r.json().get("id")
        if not job_id:
            raise RuntimeError("Firecrawl không trả về job id.")
        status_body = _poll_job(client, f"{API_BASE}/batch/scrape/{job_id}")

    return {
        "products": _extract_products(status_body.get("data") or [], urls[0] if urls else ""),
        "credits_used": status_body.get("creditsUsed") or 0,
        "total_pages_crawled": status_body.get("total") or 0,
    }
