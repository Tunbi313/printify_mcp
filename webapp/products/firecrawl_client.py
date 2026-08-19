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

import os
import time
from urllib.parse import urlparse

import httpx

API_BASE = "https://api.firecrawl.dev/v2"

CREDITS_PER_PAGE = 5  # 1 base scrape + 4 for JSON extraction
POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 280  # stay under typical platform request timeouts
MAX_BATCH_URLS = 50

_VARIANT_OPTION_PROPS = {}
for _n in (1, 2, 3):
    _VARIANT_OPTION_PROPS[f"option{_n}_name"] = {
        "type": "string", "description": f"Option {_n}'s name, e.g. Size, Color, Scent — omit if unused",
    }
    _VARIANT_OPTION_PROPS[f"option{_n}_value"] = {
        "type": "string", "description": f"Option {_n}'s value for this variant, e.g. Large, Red — omit if unused",
    }

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
        "variants": {
            "type": "array",
            "description": "Product options/variants (e.g. Size, Color, Scent), if any — up to 3 option dimensions",
            "items": {
                "type": "object",
                "properties": {
                    **_VARIANT_OPTION_PROPS,
                    "price": {"type": "number", "description": "This variant's price, if it differs from the base price"},
                    "sku": {
                        "type": "string",
                        "description": (
                            "This variant's real SKU if shown, else "
                            "compose one from the base SKU plus this "
                            "variant's option values (e.g. base "
                            "'HOODIE-FLEECE' + Size L + Color Grey -> "
                            "'HOODIE-FLEECE-GRY-L'). Never leave empty."
                        ),
                    },
                    "barcode": {"type": "string", "description": "This variant's barcode, if shown"},
                    "weight_grams": {"type": "number", "description": "This variant's weight in grams, if it differs"},
                    "image": {"type": "string", "description": "This variant's specific image URL, if the page shows one"},
                },
            },
        },
    },
    "required": ["title"],
}

PRODUCT_PROMPT = (
    "Extract this e-commerce product's real data exactly as shown on the "
    "page — title, full description as HTML, price, compare-at/original "
    "price if on sale, barcode, weight, images, and any size/color/scent "
    "variants (up to 3 option dimensions each). Additionally WRITE an "
    "SEO title, an SEO meta description, a best-fit product category, a "
    "short product type, 5-8 browse tags, and a SKU for the product and "
    "each variant (use the real one if shown, otherwise compose a short "
    "sensible one yourself — see the sku field's own instructions). "
    "Compose these fields yourself from the real product content, don't "
    "just copy raw page text for them.\n\n"
    "CRITICAL: only extract if this page IS one specific product's own "
    "page (it has ONE product's dedicated price/add-to-cart/buy box). "
    "If this page instead LISTS many different products side by side — "
    "a collection, category, search-results, or paginated browse page — "
    "return an empty object {} even if one product looks visually "
    "prominent or featured on it. Never pick 'the first' or 'the "
    "featured' product off a listing page and describe it as if the "
    "whole page were that product's page."
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


def _extract_products(pages: list[dict], fallback_url: str) -> list[dict]:
    products = []
    for page in pages:
        extracted = page.get("json") or {}
        metadata = page.get("metadata") or {}
        if not extracted.get("title"):
            continue  # not a product page — skip (e.g. the category page itself)
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
    payload = {"url": url, "formats": [_json_format()], "onlyMainContent": True}
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{API_BASE}/scrape", headers=_headers(), json=payload)
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        body = r.json()

    data = body.get("data") or {}
    extracted = data.get("json") or {}
    metadata = data.get("metadata") or {}
    if not extracted.get("title"):
        raise RuntimeError(
            "Không trích xuất được dữ liệu sản phẩm từ URL này — có thể "
            "đây không phải trang sản phẩm, hoặc trang chặn crawler."
        )
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
    payload = {"urls": urls, "formats": [_json_format()], "onlyMainContent": True}

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
