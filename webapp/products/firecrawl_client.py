"""
Thin client for Firecrawl's scrape/crawl/batch-scrape API, used by the
Product Crawl tool to pull structured product data (title/price/images/
variants/SEO) from an external product page, an entire category page, or
a hand-picked list of product URLs.

Auth: set env var FIRECRAWL_API_KEY (firecrawl.dev -> API Keys).

Pricing used here: full product-import JSON/schema extraction costs 5 credits
per page (1 browser scrape + 4 extraction). The compact collection CSV instead
uses a deterministic browser action and costs only 1 credit per fallback page.
"""

import html
import json
import math
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import httpx

from . import openrouter_client

API_BASE = "https://api.firecrawl.dev/v2"

CREDITS_PER_PAGE = 5  # 1 base scrape + 4 for JSON extraction
COLLECTION_CREDITS_PER_PAGE = 1  # browser scrape + deterministic action result
COLLECTION_CACHE_MAX_AGE_MS = 6 * 60 * 60 * 1000
POLL_INTERVAL_SEC = 3
POLL_TIMEOUT_SEC = 280  # stay under typical platform request timeouts
MAX_BATCH_URLS = 50
MAX_COLLECTION_PAGES = 500
MAX_COLLECTION_PRODUCTS = 50_000
STABLE_COLLECTION_SORT = "shelved-descending"
MAX_INFINITE_SCROLL_ROUNDS = 22
SEARCH_API_WORKERS = 20
SEARCH_API_PAGE_SIZE = 50
SITEBASE_SEARCH_API_PATH = "/api/catalog/next/products.json"
SITEBASE_COLLECTION_API_PATH = "/api/catalog/next/collections.json"
SEARCH_API_BROWSER_CONCURRENCY = 6
SITEBASE_GALLERY_BROWSER_BATCH_SIZE = 40
SITEBASE_GALLERY_BROWSER_CONCURRENCY = 5
PUMPBEAR_SEARCH_BROWSER_CONCURRENCY = 1
SFCC_SEARCH_PAGE_SIZE = 48
SFCC_HOST_SUFFIXES = ("boxlunch.com", "hottopic.com")
ETSY_PAGE_SIZE = 36
SHOPIFY_COLLECTION_PAGE_SIZE = 250
SHOPIFY_SEARCH_WORKERS = 4
SHOPIFY_SEARCH_MAX_ATTEMPTS = 5
SHOPIFY_SEARCH_RETRY_BASE_SEC = 1.0
SHOPIFY_STOREFRONT_API_VERSION = "2026-10"
SHOPIFY_STOREFRONT_PAGE_SIZE = 250
WOOCOMMERCE_STORE_PAGE_SIZE = 100
WOOCOMMERCE_STORE_WORKERS = 12
WOOCOMMERCE_STORE_MAX_ATTEMPTS = 2
WOOCOMMERCE_STORE_RETRY_BASE_SEC = 0.5
COLLECTION_PRODUCT_ACTION_SCRIPT = r"""(async () => {
  let schema = null;
  for (const node of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const value = JSON.parse(node.textContent || '{}');
      const candidates = Array.isArray(value)
        ? value : [value, ...((value && value['@graph']) || [])];
      schema = candidates.find(item => item && (
        item['@type'] === 'Product' ||
        item['@type'] === 'ProductGroup' ||
        (Array.isArray(item['@type']) && (
          item['@type'].includes('Product') || item['@type'].includes('ProductGroup')
        ))
      ));
      if (schema) break;
    } catch (_) {}
  }
  const firstVariant = schema && Array.isArray(schema.hasVariant)
    ? schema.hasVariant[0] : null;
  const offers = schema && (schema.offers || (firstVariant && firstVariant.offers));
  const offer = Array.isArray(offers) ? offers[0] : (offers || {});
  let shopify = null;
  if (/\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?products\/[^/]+\/?$/i.test(location.pathname)) {
    try {
      const response = await fetch(location.pathname.replace(/\/$/, '') + '.js', {
        headers: {'Accept': 'application/json'}
      });
      const value = response.ok ? await response.json() : null;
      if (value && value.title && Array.isArray(value.images)) shopify = value;
    } catch (_) {}
  }
  const selectors = [
    '.carousel-image[data-src-zoom-image]',
    '.media_gallery__thumbnail--horizontal img[data-zoom]',
    '.thumbnail-carousel-slide img[data-zoom]',
    '[component="media_gallery"] img[data-zoom]',
    '.product__media-list img',
    '.product-single__media img',
    '.product-images img.product-thumbnail-image',
    'product-gallery img',
    '[data-product-media] img',
    '[data-media-id] img',
    'main [class*="product-gallery"] img',
    'main [class*="product__media"] img',
    'main [class*="product-media"] img',
    'main [id*="product-gallery"] img',
    'main [id*="product-media"] img',
    '#altImages img'
  ];
  function normaliseImage(url) {
    if (!url) return '';
    try {
      const absolute = new URL(url, location.href).href;
      if (/amazon\./i.test(location.hostname) && /m\.media-amazon\.com/i.test(absolute)) {
        return absolute.replace(/\._[^.]+(?=\.(?:jpe?g|png|webp)(?:$|\?))/i, '');
      }
      if (/cdn\.media\.amplience\.net$/i.test(new URL(absolute).hostname)) {
        const source = new URL(absolute);
        source.search = '';
        return source.href.replace(/NaN$/, '');
      }
      return absolute;
    } catch (_) {
      return url;
    }
  }
  function usableProductImage(url) {
    return Boolean(url)
      && !/\.svg(?:$|[?#])/i.test(url)
      && !/(?:woocommerce[-_]?placeholder|(?:^|[/_.-])placeholder(?:[/_.-]|$)|awaiting[-_]?product[-_]?image)/i.test(url);
  }
  function bestImage(img) {
    const dynamic = img.getAttribute('data-a-dynamic-image');
    if (dynamic) {
      try {
        const entries = Object.entries(JSON.parse(dynamic));
        entries.sort((a, b) => ((b[1] || [0, 0])[0] * (b[1] || [0, 0])[1])
          - ((a[1] || [0, 0])[0] * (a[1] || [0, 0])[1]));
        if (entries[0]) return normaliseImage(entries[0][0]);
      } catch (_) {}
    }
    const srcset = img.getAttribute('srcset');
    const largest = srcset && srcset.split(',').map(item => {
      const parts = item.trim().split(/\s+/);
      return {url: parts[0], width: parseInt(parts[1], 10) || 0};
    }).sort((a, b) => b.width - a.width)[0];
    return normaliseImage(img.getAttribute('data-src-zoom-image')
      || img.getAttribute('data-old-hires')
      || img.dataset.zoom || (largest && largest.url)
      || img.getAttribute('src') || img.currentSrc || img.src);
  }
  let images = (shopify && shopify.images || []).map(item =>
    normaliseImage(typeof item === 'string' ? item : item && item.src)
  ).filter(Boolean);
  for (const selector of selectors) {
    if (images.length) break;
    images = Array.from(document.querySelectorAll(selector))
      .map(bestImage).filter(Boolean);
    if (images.length) break;
  }
  if (images.length <= 1) {
    const root = document.querySelector('main') || document.body;
    const blocked = /(related|recommend|upsell|cross-sell|review|avatar|logo|icon|payment|badge|recent)/i;
    const candidates = Array.from(root.querySelectorAll('img')).filter(img => {
      let node = img;
      for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
        const marker = `${node.id || ''} ${typeof node.className === 'string' ? node.className : ''}`;
        if (blocked.test(marker)) return false;
      }
      return Boolean(
        img.getAttribute('data-src-zoom-image')
        || img.getAttribute('data-old-hires')
        || img.getAttribute('data-a-dynamic-image')
        || img.getAttribute('srcset')
        || img.naturalWidth >= 600
      );
    }).map(bestImage).filter(Boolean);
    if (candidates.length > images.length) images = candidates;
  }
  if (!images.length && schema && schema.image) {
    const raw = Array.isArray(schema.image) ? schema.image : [schema.image];
    images = raw.map(item => normaliseImage(typeof item === 'string'
      ? item : (item && (item.url || item.contentUrl)))).filter(Boolean);
  }
  const compareElement = document.querySelector(
    '.product-price__origin, .price__compare, .compare-at-price, '
    + '[class*="compare_at"], [class*="compare-at"]'
  );
  const compareText = compareElement && compareElement.textContent;
  function parseLocalMoney(text) {
    const match = String(text || '').replace(/\s/g, '').match(/\d[\d.,]*/);
    if (!match) return null;
    let value = match[0];
    const comma = value.lastIndexOf(',');
    const dot = value.lastIndexOf('.');
    const decimalAt = Math.max(comma, dot);
    if (decimalAt >= 0 && value.length - decimalAt - 1 <= 2) {
      value = value.slice(0, decimalAt).replace(/[.,]/g, '')
        + '.' + value.slice(decimalAt + 1).replace(/[.,]/g, '');
    } else {
      value = value.replace(/[.,]/g, '');
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }
  const compareValue = parseLocalMoney(compareText);
  const variants = shopify && Array.isArray(shopify.variants) ? shopify.variants : [];
  const selectedVariant = variants.find(item => item && item.available !== false)
    || variants[0] || {};
  const shopifyPrice = selectedVariant.price != null
    ? Number(selectedVariant.price) / 100
    : (shopify && shopify.price != null ? Number(shopify.price) / 100 : null);
  const shopifyCompare = selectedVariant.compare_at_price != null
    ? Number(selectedVariant.compare_at_price) / 100
    : (shopify && shopify.compare_at_price != null
      ? Number(shopify.compare_at_price) / 100 : null);
  return {
    title: (shopify && shopify.title) || (schema && schema.name)
      || document.querySelector('h1,h2,h3')?.textContent?.trim() || '',
    price: shopifyPrice != null
      ? shopifyPrice : (offer.price != null ? offer.price : offer.lowPrice),
    compare_at_price: shopifyCompare != null
      ? shopifyCompare : compareValue,
    currency: (window.Shopify && window.Shopify.currency
      && window.Shopify.currency.active) || offer.priceCurrency || '',
    product_url: location.href.split('#')[0].split('?')[0],
    images: Array.from(new Set(
      images.map(normaliseImage).filter(usableProductImage)
    ))
  };
})()"""


class CollectionBatchIncomplete(RuntimeError):
    """Firecrawl exposed a terminal label before every batch item finished."""


def _collection_batch_progress(body: dict) -> tuple[str, int, int]:
    """Return a trustworthy status for Firecrawl's eventually-consistent batch.

    Large batches can temporarily report ``status=completed`` while
    ``completed`` is still below ``total``. Treat the numeric counters as the
    source of truth so the UI cannot offer a partial CSV.
    """
    raw_status = str(body.get("status") or "processing").lower()
    data_count = len(body.get("data") or [])
    try:
        total = max(0, int(body.get("total") or 0))
    except (TypeError, ValueError):
        total = 0
    try:
        completed = max(0, int(body.get("completed") or data_count))
    except (TypeError, ValueError):
        completed = data_count

    status = raw_status
    if raw_status == "completed" and total > 0 and completed < total:
        status = "processing"
    return status, completed, total


COLLECTION_PAGINATION_KEYS = {
    "page",
    "page_num",
    "page_number",
    "pagenum",
    "p",
    "cursor",
    "after",
    "offset",
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


# The collection-export screen intentionally asks Firecrawl for only the
# fields that will be written to its compact CSV.  Keeping this separate from
# PRODUCT_SCHEMA also prevents the more expensive product-import enrichment
# (SEO copy, taxonomy, variants, and the OpenRouter refinement pass) from
# leaking into a simple catalogue export.
COLLECTION_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "The product's exact title as shown on its product page",
        },
        "price": {
            "type": "number",
            "description": "The current selling price as a plain number without a currency symbol",
        },
        "compare_at_price": {
            "type": "number",
            "description": "The original crossed-out price when the product is genuinely on sale; otherwise omit",
        },
        "currency": {
            "type": "string",
            "description": "The three-letter ISO currency code shown with the product price, e.g. USD",
        },
        "images": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Every product-gallery image URL, as an absolute URL; exclude icons, logos and unrelated recommendations",
        },
    },
    "required": ["title"],
}

COLLECTION_PRODUCT_PROMPT = (
    "Extract only this product's exact title, current selling price, original "
    "crossed-out price when present, ISO currency code, and every image "
    "belonging to its product gallery. Use the actual price in "
    "the main buy box; ignore installment amounts, coupon previews, shipping "
    "thresholds, crossed-out prices, and prices of related products. Return "
    "all full-size product image URLs, including images for other colors or "
    "variants when they are present in the gallery. Exclude logos, icons, "
    "payment badges, banners, review avatars, and recommended-product images. "
    "If this is not a real product page, return an empty object."
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


def _collection_json_format() -> dict:
    return {
        "type": "json",
        "schema": COLLECTION_PRODUCT_SCHEMA,
        "prompt": COLLECTION_PRODUCT_PROMPT,
    }


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


def _scrape_links(client: httpx.Client, page_url: str) -> list[str]:
    """Return the rendered links from one page using one base credit."""
    r = client.post(
        f"{API_BASE}/scrape",
        headers=_headers(),
        json={"url": page_url, "formats": ["links"], "onlyMainContent": False},
    )
    if r.is_error:
        raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
    return ((r.json().get("data") or {}).get("links")) or []


def _post_scrape_with_retry(
    client: httpx.Client,
    payload: dict,
    *,
    timeout: float | None = None,
    attempts: int = 3,
) -> httpx.Response:
    """Retry transient Firecrawl transport errors and retryable responses."""
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            response = client.post(
                f"{API_BASE}/scrape",
                headers=_headers(),
                json=payload,
                timeout=timeout,
            )
            if (
                response.status_code not in {408, 429, 500, 502, 503, 504}
                or "SCRAPE_ACTION_ERROR" in response.text
            ):
                return response
            last_error = RuntimeError(
                f"Firecrawl {response.status_code}: {response.text[:500]}"
            )
        except httpx.HTTPError as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.6 * (2**attempt))
    raise RuntimeError(f"Không kết nối ổn định được tới Firecrawl: {last_error}")


def _advertised_product_count(page_text: str) -> int | None:
    """Read counts such as ``412 results``, ``results found`` or products."""
    matches = re.findall(
        r"(?<![\d.,])([\d][\d.,\s]*)\s+"
        r"(?:results?(?:\s+found)?|products?|items?)(?![A-Za-z])",
        page_text or "",
        flags=re.IGNORECASE,
    )
    matches.extend(re.findall(
        r"showing\s+[\d.,]+\s*(?:[-–—]|to)\s*[\d.,]+\s+of\s+([\d][\d.,\s]*)",
        page_text or "",
        flags=re.IGNORECASE,
    ))
    counts = []
    for raw in matches:
        digits = re.sub(r"\D", "", raw)
        if digits:
            counts.append(int(digits))
    return max(counts) if counts else None


def _is_search_results_url(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/").lower()
    query = {
        key.lower(): value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    }
    return (
        path == "/search"
        or path.endswith("/search")
        or bool(str(query.get("s") or "").strip())
    )


def _is_pumpbear_search_url(url: str) -> bool:
    """Recognise PumpBear's post-ShopBase server-rendered search pages."""
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    return hostname == "pumpbear.com" and parsed.path.rstrip("/").lower() == "/search"


def _pumpbear_search_start_url(search_url: str) -> str:
    """Drop obsolete ShopBase controls and start PumpBear HTML pagination at 1."""
    parsed = urlparse(search_url)
    obsolete = {
        "page", "limit", "minimal", "infinite", "sort_field", "sort_direction",
    }
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in obsolete | {"sort"}
    ]
    query.extend([("sort", "relevance"), ("page", "1")])
    return urlunparse(parsed._replace(
        query=urlencode(query, doseq=True),
        fragment="",
    ))


def _is_wordpress_search_url(url: str) -> bool:
    """WordPress/WooCommerce searches conventionally use the ``s`` query."""
    query = {
        key.lower(): value
        for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
    }
    return bool(str(query.get("s") or "").strip())


def _wordpress_search_start_url(search_url: str) -> str:
    """Reset both query-string and pretty-permalink WordPress pagination."""
    parsed = urlparse(search_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in COLLECTION_PAGINATION_KEYS | {"paged"}
    ]
    path = re.sub(r"/page/\d+/?$", "/", parsed.path, flags=re.IGNORECASE)
    return urlunparse(parsed._replace(
        path=path,
        query=urlencode(query, doseq=True),
        fragment="",
    ))


def _is_printerval_search_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    return hostname == "printerval.com" and parsed.path.rstrip("/").lower() == "/search"


def _is_sfcc_search_url(url: str) -> bool:
    """Recognise Salesforce Commerce Cloud search storefronts we support.

    SFCC product grids use offset pagination (``start``/``sz``).  Keeping this
    platform adapter explicit prevents unrelated search pages from paying for
    an unnecessary browser probe or matching products from navigation menus.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if any(
        hostname == suffix or hostname.endswith(f".{suffix}")
        for suffix in SFCC_HOST_SUFFIXES
    ):
        return True
    query_keys = {
        key.lower()
        for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
    }
    return "cgid" in query_keys and bool(query_keys & {"start", "sz", "srule"})


def _product_links_from_action_results(data: dict) -> list[str]:
    """Return URLs accumulated by the final executeJavascript action."""
    action_data = data.get("actions") or {}
    returns = action_data.get("javascriptReturns") or []
    for item in reversed(returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, list):
            return [link for link in value if isinstance(link, str)]
    return []


def _pumpbear_search_browser_script(max_products: int) -> str:
    """Collect every PumpBear search page inside one same-origin browser run."""
    script = r"""
(async () => {
  const maxProducts = __MAX_PRODUCTS__;
  const concurrency = __CONCURRENCY__;
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

  function cleanImage(raw, pageUrl) {
    if (!raw) return '';
    try {
      const image = new URL(raw, pageUrl);
      if (image.hostname.toLowerCase() !== 'cdn.pumpbear.com') return '';
      if (!/^\/products\//i.test(image.pathname)) return '';
      ['w', 'h', 'width', 'height', 'min_width', 'min_height', 'max_width', 'max_height']
        .forEach(key => image.searchParams.delete(key));
      image.hash = '';
      return image.href;
    } catch (_) {
      return '';
    }
  }

  function bestImage(image, pageUrl) {
    const srcset = image.getAttribute('srcset');
    const largest = srcset && srcset.split(',').map(item => {
      const parts = item.trim().split(/\s+/);
      return {url: parts[0], width: parseInt(parts[1], 10) || 0};
    }).sort((a, b) => b.width - a.width)[0];
    return cleanImage(
      image.getAttribute('data-src') || image.getAttribute('data-original')
      || (largest && largest.url) || image.getAttribute('src') || image.currentSrc,
      pageUrl
    );
  }

  function productUrl(link, pageUrl) {
    try {
      const value = new URL(link.getAttribute('href'), pageUrl);
      if (value.hostname !== location.hostname || !/^\/products\/[^/]+\/?$/i.test(value.pathname)) {
        return '';
      }
      value.search = '';
      value.hash = '';
      return value.href.replace(/\/$/, '');
    } catch (_) {
      return '';
    }
  }

  function cardRoot(link) {
    return link.closest(
      'article, li, [data-product-id], [data-product-card], '
      + '[class*="product-card"], [class*="product_card"]'
    ) || link;
  }

  function cardProduct(link, url, pageUrl) {
    const root = cardRoot(link);
    const imageNodes = Array.from(root.querySelectorAll('img'));
    const images = Array.from(new Set(
      imageNodes.map(image => bestImage(image, pageUrl)).filter(Boolean)
    ));
    const titleNode = root.querySelector(
      '[data-product-title], [class*="product-title"], [class*="product_title"], h2, h3'
    );
    const title = String(
      link.getAttribute('title') || (titleNode && titleNode.textContent)
      || (imageNodes[0] && imageNodes[0].getAttribute('alt')) || ''
    ).replace(/\s+/g, ' ').trim();
    const money = Array.from(String(root.textContent || '').matchAll(/\$\s*([\d,]+(?:\.\d{1,2})?)/g))
      .map(match => Number(match[1].replace(/,/g, '')))
      .filter(Number.isFinite);
    if (!title || !money.length || !images.length) return null;
    return {
      title,
      price: money[0],
      compare_at_price: money.find(value => value > money[0]) || null,
      currency: 'USD',
      product_url: url,
      images
    };
  }

  function pageData(doc, pageUrl) {
    const found = new Map();
    const root = doc.querySelector('main') || doc;
    for (const link of root.querySelectorAll('a[href]')) {
      const url = productUrl(link, pageUrl);
      if (!url || found.has(url)) continue;
      found.set(url, cardProduct(link, url, pageUrl));
    }
    return found;
  }

  function advertisedCount(doc) {
    const text = String((doc.body || doc.documentElement).innerText || '');
    const matches = Array.from(text.matchAll(/([\d][\d,]*)\s+results?\b/gi));
    const values = matches.map(match => Number(match[1].replace(/,/g, '')))
      .filter(Number.isFinite);
    return values.length ? Math.max(...values) : 0;
  }

  async function loadPage(pageNumber, baseUrl, attempts = 4) {
    const url = new URL(baseUrl);
    url.searchParams.set('page', String(pageNumber));
    let lastStatus = 0;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      let retryAfter = 0;
      try {
        const response = await fetch(url.href, {
          credentials: 'same-origin',
          headers: {'Accept': 'text/html,application/xhtml+xml'}
        });
        lastStatus = response.status;
        if (response.ok) {
          const html = await response.text();
          const doc = new DOMParser().parseFromString(html, 'text/html');
          return {page: pageNumber, entries: pageData(doc, url.href), error: ''};
        }
        const retryHeader = Number(response.headers.get('retry-after') || 0);
        if (Number.isFinite(retryHeader) && retryHeader > 0) {
          retryAfter = retryHeader * 1000;
        }
      } catch (_) {}
      if (attempt + 1 < attempts) {
        // PumpBear starts rate-limiting a browser that requests pages too
        // quickly. Its block lasts longer than the old 0.75/1.5s retry.
        const backoff = Math.max(retryAfter, 5000 * (2 ** attempt));
        await sleep(backoff + ((pageNumber % 5) * 120));
      }
    }
    return {
      page: pageNumber,
      entries: new Map(),
      error: `page ${pageNumber} HTTP ${lastStatus || 'network'}`
    };
  }

  const baseUrl = new URL(location.href);
  baseUrl.searchParams.set('sort', 'relevance');
  baseUrl.searchParams.set('page', '1');
  const firstEntries = pageData(document, baseUrl.href);
  const advertised = advertisedCount(document);
  const pageSize = firstEntries.size;
  if (!advertised || !pageSize) {
    return {error: 'missing advertised count or first-page products'};
  }
  const required = Math.min(advertised, maxProducts);
  const pageCount = Math.max(1, Math.ceil(required / pageSize));
  const entries = new Map(firstEntries);
  const failedPages = [];

  const pageNumbers = Array.from({length: Math.max(0, pageCount - 1)}, (_, index) => index + 2);
  for (let offset = 0; offset < pageNumbers.length; offset += concurrency) {
    const batch = pageNumbers.slice(offset, offset + concurrency);
    const results = await Promise.all(batch.map(page => loadPage(page, baseUrl.href)));
    for (const result of results) {
      if (result.error) failedPages.push(result.page);
      for (const [url, product] of result.entries) {
        if (!entries.has(url) || (!entries.get(url) && product)) entries.set(url, product);
      }
    }
    if (offset + concurrency < pageNumbers.length) {
      await sleep(650);
      // A short periodic cooldown prevents Cloudflare's rolling request
      // window from rejecting a contiguous block of later pages.
      if ((offset + concurrency) % 12 === 0) await sleep(3000);
    }
  }

  // Retry only failed pages after a longer cooldown. Keeping recovery in this
  // same browser preserves PumpBear/Cloudflare cookies and still costs one
  // Firecrawl scrape credit.
  if (failedPages.length) {
    const retryPages = Array.from(new Set(failedPages));
    failedPages.length = 0;
    await sleep(10000);
    for (const page of retryPages) {
      const result = await loadPage(page, baseUrl.href, 5);
      if (result.error) failedPages.push(result.page);
      for (const [url, product] of result.entries) {
        if (!entries.has(url) || (!entries.get(url) && product)) entries.set(url, product);
      }
      await sleep(1200);
    }
  }

  const productUrls = Array.from(entries.keys()).slice(0, required);
  const products = productUrls.map(url => entries.get(url)).filter(Boolean);
  return {
    advertised_count: advertised,
    pages_scanned: pageCount,
    product_urls: productUrls,
    products,
    failed_pages: failedPages
  };
})()
"""
    return (
        script.replace("__MAX_PRODUCTS__", str(int(max_products)))
        .replace("__CONCURRENCY__", str(PUMPBEAR_SEARCH_BROWSER_CONCURRENCY))
    )


def _discover_pumpbear_search_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Traverse PumpBear's new SSR pagination in one Firecrawl browser credit."""
    if not _is_pumpbear_search_url(search_url):
        return None

    start_url = _pumpbear_search_start_url(search_url)
    response = _post_scrape_with_retry(
        client,
        {
            "url": start_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 180_000,
            "actions": [
                {"type": "wait", "milliseconds": 1500},
                {
                    "type": "executeJavascript",
                    "script": _pumpbear_search_browser_script(max_products),
                },
            ],
        },
        timeout=210,
    )
    if response.is_error:
        raise RuntimeError(
            f"Firecrawl PumpBear search {response.status_code}: {response.text[:500]}"
        )

    data = response.json().get("data") or {}
    action_value = None
    for item in reversed(((data.get("actions") or {}).get("javascriptReturns")) or []):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and (
            "product_urls" in value or "error" in value
        ):
            action_value = value
            break
    if action_value is None:
        raise RuntimeError("PumpBear không trả kết quả phân trang từ trình duyệt.")
    if action_value.get("error"):
        raise RuntimeError(f"Không đọc được phân trang PumpBear: {action_value['error']}.")

    failed_pages = action_value.get("failed_pages") or []
    if failed_pages:
        sample = ", ".join(str(page) for page in failed_pages[:8])
        raise RuntimeError(f"PumpBear chặn hoặc lỗi ở trang: {sample}.")

    try:
        advertised_count = int(action_value.get("advertised_count") or 0)
        pages_scanned = int(action_value.get("pages_scanned") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("PumpBear trả tổng số sản phẩm không hợp lệ.") from exc
    required_count = min(advertised_count, max_products)
    product_urls = []
    for link in action_value.get("product_urls") or []:
        product_url = _canonical_product_link(
            link, start_url, domain, path_hint
        )
        if product_url and product_url not in product_urls:
            product_urls.append(product_url)
            if len(product_urls) >= required_count:
                break
    if not advertised_count or len(product_urls) != required_count:
        raise RuntimeError(
            f"PumpBear công bố {advertised_count} sản phẩm nhưng chỉ đọc được "
            f"{len(product_urls)} URL duy nhất; đã dừng để tránh CSV thiếu dữ liệu."
        )

    products_by_url = {}
    for product in action_value.get("products") or []:
        if not isinstance(product, dict):
            continue
        product_url = _canonical_product_link(
            str(product.get("product_url") or ""), start_url, domain, path_hint
        )
        images = [
            str(image)
            for image in dict.fromkeys(product.get("images") or [])
            if isinstance(image, str) and image.startswith(("http://", "https://"))
        ][:2]
        if (
            product_url in product_urls
            and product.get("title")
            and product.get("price") not in (None, "")
            and images
        ):
            products_by_url[product_url] = {
                **product,
                "product_url": product_url,
                "images": images,
            }

    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    return {
        "urls": product_urls,
        "products": [
            products_by_url[url] for url in product_urls if url in products_by_url
        ],
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": advertised_count > max_products,
        "discovery_mode": "pumpbear_search_html",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "waf_blocked": False,
        "full_gallery_available": False,
        "card_diagnostics": {
            "returned": len(products_by_url),
            "with_title": len(products_by_url),
            "with_price": len(products_by_url),
            "with_images": len(products_by_url),
            "missing_sample": [
                url for url in product_urls if url not in products_by_url
            ][:10],
        },
    }


def _discover_printerval_search_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Traverse Printerval's two-level lazy grid and Next pagination.

    A Printerval search page lazily fills up to 60 cards, then exposes a real
    Next control (for example ``Showing 1 - 60 of 1,049``).  Plain scrolling
    therefore stops at page one.  Same-origin iframes let one browser action
    fully hydrate each subsequent page without losing the accumulated IDs.
    """
    if not _is_printerval_search_url(search_url):
        return None

    parsed = urlparse(search_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in COLLECTION_PAGINATION_KEYS | {"paged"}
    ]
    normalised_url = urlunparse(parsed._replace(
        query=urlencode(query, doseq=True),
        fragment="",
    ))
    script = f"""
(async () => {{
  const maxProducts = {int(max_products)};
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  function rangeInfo(doc) {{
    const pattern = /showing\\s+([\\d,]+)\\s*(?:[-–—]|to)\\s*([\\d,]+)\\s+of\\s+([\\d,]+)/i;
    const nodes = Array.from(doc.querySelectorAll('div, p, span')).filter(node =>
      node.children.length <= 3 && pattern.test((node.textContent || '').trim())
    );
    for (const node of nodes) {{
      const match = (node.textContent || '').trim().match(pattern);
      if (match) return {{
        node,
        first: Number(match[1].replace(/,/g, '')),
        last: Number(match[2].replace(/,/g, '')),
        total: Number(match[3].replace(/,/g, ''))
      }};
    }}
    return {{node: null, first: 0, last: 0, total: 0}};
  }}
  function entriesWithin(root) {{
    const found = new Map();
    if (!root) return found;
    function productId(link) {{
      try {{
        const url = new URL(link.getAttribute('href'), location.origin);
        const match = url.pathname.match(/-p(\\d+)(?:\\.html)?\\/?$/i);
        return url.hostname === location.hostname && match ? match[1] : '';
      }} catch (_) {{ return ''; }}
    }}
    function productIdsWithin(node) {{
      const ids = new Set();
      if (!node) return ids;
      if (node.matches && node.matches('a[href]')) {{
        const id = productId(node);
        if (id) ids.add(id);
      }}
      for (const link of node.querySelectorAll('a[href]')) {{
        const id = productId(link);
        if (id) ids.add(id);
        if (ids.size > 1) break;
      }}
      return ids;
    }}
    function hasPrice(node) {{
      if (!node) return false;
      if (node.querySelector(
        '[data-price], [data-product-price], [data-sale-price], '
        + '[itemprop="price"], meta[property="product:price:amount"]'
      )) return true;
      return /(US\\$|CA\\$|AU\\$|\\$|€|£|¥|₫)\\s*[\\d,.]+/.test(node.innerText || '');
    }}
    function productCard(anchor, id) {{
      let node = anchor.parentElement;
      let best = node || anchor;
      for (let depth = 0; node && depth < 12; depth += 1, node = node.parentElement) {{
        const ids = productIdsWithin(node);
        if (ids.size > 1) break;
        if (ids.size === 1 && ids.has(id)) {{
          best = node;
          if (node.querySelector('img') && hasPrice(node)) return node;
        }}
      }}
      return best;
    }}
    function bestImage(img) {{
      const srcset = img.getAttribute('srcset');
      const largest = srcset && srcset.split(',').map(item => {{
        const parts = item.trim().split(/\\s+/);
        return {{url: parts[0], width: parseInt(parts[1], 10) || 0}};
      }}).sort((a, b) => b.width - a.width)[0];
      const raw = img.getAttribute('data-src') || img.getAttribute('data-original')
        || img.getAttribute('data-lazy-src') || (largest && largest.url)
        || img.getAttribute('src') || img.currentSrc || '';
      try {{ return new URL(raw, location.origin).href; }} catch (_) {{ return ''; }}
    }}
    function money(card) {{
      const text = (card && card.innerText || '').replace(/\\s+/g, ' ');
      const matches = Array.from(text.matchAll(
        /(US\\$|CA\\$|AU\\$|\\$|â‚¬|Â£|Â¥|â‚«)\\s*([\\d,.]+)/g
      ));
      const values = matches.map(match => Number(
        match[2].replace(/,(?=\\d{{3}}(?:\\D|$))/g, '').replace(',', '.')
      )).filter(Number.isFinite);
      const symbol = matches[0] && matches[0][1] || '';
      const currencies = {{'$': 'USD', 'US$': 'USD', 'CA$': 'CAD', 'AU$': 'AUD',
        'â‚¬': 'EUR', 'Â£': 'GBP', 'Â¥': 'JPY', 'â‚«': 'VND'}};
      const priceNode = card && card.querySelector(
        '[itemprop="price"], meta[property="product:price:amount"], '
        + '[data-price], [data-product-price], [data-sale-price]'
      );
      const rawAttribute = priceNode && (
        priceNode.getAttribute('content') || priceNode.getAttribute('data-price')
        || priceNode.getAttribute('data-product-price')
        || priceNode.getAttribute('data-sale-price')
      );
      const attributePrice = rawAttribute == null ? null : Number(
        String(rawAttribute).replace(/[^\\d,.]/g, '')
          .replace(/,(?=\\d{{3}}(?:\\D|$))/g, '').replace(',', '.')
      );
      const price = values[0] == null
        ? (Number.isFinite(attributePrice) ? attributePrice : null)
        : values[0];
      const compare = values.slice(1).find(value => price != null && value > price) || null;
      return {{price, compare_at_price: compare, currency: currencies[symbol] || ''}};
    }}
    for (const anchor of root.querySelectorAll('a[href]')) {{
      try {{
        const url = new URL(anchor.getAttribute('href'), location.origin);
        if (url.hostname !== location.hostname) continue;
        const match = url.pathname.match(/-p(\\d+)(?:\\.html)?\\/?$/i);
        if (!match) continue;
        url.search = '';
        url.hash = '';
        const card = productCard(anchor, match[1]);
        const images = Array.from((card || anchor).querySelectorAll('img'))
          .filter(img => {{
            const source = [
              img.getAttribute('src'), img.getAttribute('data-src'),
              img.getAttribute('data-original'), img.getAttribute('data-lazy-src')
            ].filter(Boolean).join(' ');
            const semantic = [
              img.getAttribute('aria-label'), img.getAttribute('class'),
              img.parentElement && img.parentElement.getAttribute('class'),
              img.parentElement && img.parentElement.getAttribute('aria-label')
            ].filter(Boolean).join(' ');
            const alt = (img.getAttribute('alt') || '').trim();
            return !/\\.svg(?:$|[?#])/i.test(source)
              && !/(wishlist|favorite|favourite|heart|avatar|logo|icon|rating|badge)/i.test(semantic)
              && !/^(wishlist|favorite|favourite|heart|avatar|logo|icon|rating|star|badge)$/i.test(alt);
          }})
          .map(bestImage)
          .filter(image =>
            image
            && !/\\.svg(?:$|[?#])/i.test(image)
            && !/\\/assets\\/images\\/(?:heart|wishlist|favorite|star|rating|badge)[._-]/i.test(image)
          );
        const image = anchor.querySelector('img');
        const candidates = [
          anchor.getAttribute('title'), (anchor.textContent || '').trim(),
          image && image.getAttribute('alt'),
          card && card.querySelector('h2, h3, h4, [class*="title"], [class*="name"]')?.textContent
        ].map(value => (value || '').trim()).filter(value => value.length > 3);
        const title = candidates.sort((a, b) => b.length - a.length)[0] || '';
        const prices = money(card);
        const previous = found.get(match[1]) || {{
          title: '', price: null, compare_at_price: null, currency: '',
          product_url: url.href, images: []
        }};
        found.set(match[1], {{
          title: previous.title.length >= title.length ? previous.title : title,
          price: previous.price != null ? previous.price : prices.price,
          compare_at_price: previous.compare_at_price != null
            ? previous.compare_at_price : prices.compare_at_price,
          currency: previous.currency || prices.currency,
          product_url: previous.product_url || url.href,
          images: Array.from(new Set([...previous.images, ...images])).slice(0, 2)
        }});
      }} catch (_) {{}}
    }}
    // Printerval pages also contain product-shaped links in recommendation,
    // navigation and promotion blocks. They often have a title/image but no
    // price. Counting those links can make pagination stop before every real
    // search card has been visited. A result-grid product card always exposes
    // all three fields used by the compact CSV.
    return new Map(Array.from(found.entries()).filter(([, product]) =>
      Boolean(product.title) && product.price != null && product.images.length > 0
    ));
  }}
  function pageEntries(doc) {{
    const range = rangeInfo(doc);
    const expected = range.last >= range.first
      ? range.last - range.first + 1 : 0;
    let root = range.node;
    while (root && root !== doc.body) {{
      const entries = entriesWithin(root);
      if (!expected || entries.size >= expected) return entries;
      root = root.parentElement;
    }}
    const candidates = [
      doc.querySelector('[class*="search-result"]'),
      doc.querySelector('[class*="product-list"]'),
      doc.querySelector('[class*="product-grid"]'),
      doc.querySelector('main'),
      doc.body
    ].filter(Boolean);
    let best = new Map();
    for (const candidate of candidates) {{
      const entries = entriesWithin(candidate);
      if (entries.size > best.size) best = entries;
      if (expected && entries.size === expected) return entries;
    }}
    return best;
  }}
  function nextUrl(doc) {{
    const selectors = [
      'a[rel="next"][href]', '.pagination a.next[href]',
      'a.next[href]', 'a[aria-label*="next" i][href]'
    ];
    let node = doc.querySelector(selectors.join(','));
    if (!node) {{
      node = Array.from(doc.querySelectorAll('a[href]')).find(anchor =>
        /^next(?:\\s*[›»>])?$/i.test((anchor.textContent || '').trim())
      );
    }}
    if (!node) return '';
    try {{ return new URL(node.getAttribute('href'), location.origin).href; }}
    catch (_) {{ return ''; }}
  }}
  async function hydrate(win) {{
    let last = -1;
    let stable = 0;
    for (let round = 0; round < 12; round += 1) {{
      const doc = win.document;
      win.scrollTo(0, Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight));
      await sleep(450);
      const size = pageEntries(doc).size;
      if (size === last) stable += 1; else stable = 0;
      last = size;
      if (stable >= 2 && nextUrl(doc)) break;
    }}
    return win.document;
  }}
  function loadFrame(frame, url) {{
    return new Promise((resolve, reject) => {{
      const timer = setTimeout(() => reject(new Error('Printerval page timeout')), 20000);
      frame.onload = () => {{ clearTimeout(timer); resolve(frame.contentWindow); }};
      frame.onerror = () => {{ clearTimeout(timer); reject(new Error('Printerval page failed')); }};
      frame.src = url;
    }});
  }}

  const all = new Map();
  let doc = await hydrate(window);
  let range = rangeInfo(doc);
  const total = range.total;
  if (!total) throw new Error('Printerval advertised total was not found');
  const required = Math.min(total, maxProducts);
  pageEntries(doc).forEach((product, id) => all.set(id, product));
  let target = nextUrl(doc);
  let pagesScanned = 1;
  const visited = new Set([location.href]);
  const frame = document.createElement('iframe');
  frame.style.cssText = 'position:fixed;left:-10000px;top:0;width:1280px;height:900px;';
  document.body.appendChild(frame);
  try {{
    while (target && all.size < required && pagesScanned < 500) {{
      if (visited.has(target)) break;
      visited.add(target);
      const win = await loadFrame(frame, target);
      doc = await hydrate(win);
      pageEntries(doc).forEach((product, id) => all.set(id, product));
      target = nextUrl(doc);
      pagesScanned += 1;
    }}
  }} finally {{
    frame.remove();
  }}
  const returnedProducts = Array.from(all.values()).slice(0, required);
  return {{
    advertised_count: total,
    pages_scanned: pagesScanned,
    links: returnedProducts.map(product => product.product_url),
    products: returnedProducts,
    card_diagnostics: {{
      returned: returnedProducts.length,
      with_title: returnedProducts.filter(product => Boolean(product.title)).length,
      with_price: returnedProducts.filter(product => product.price != null).length,
      with_images: returnedProducts.filter(product => product.images.length > 0).length,
      missing_sample: returnedProducts.filter(product =>
        !product.title || product.price == null || !product.images.length
      ).slice(0, 20).map(product => ({{
        product_url: product.product_url,
        title: Boolean(product.title),
        price: product.price,
        image_count: product.images.length
      }}))
    }}
  }};
}})()
"""
    response = _post_scrape_with_retry(
        client,
        {
            "url": normalised_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": [
                {"type": "wait", "milliseconds": 800},
                {"type": "executeJavascript", "script": script},
            ],
        },
        timeout=150,
    )
    if response.is_error:
        raise RuntimeError(
            f"Firecrawl Printerval {response.status_code}: {response.text[:500]}"
        )
    data = response.json().get("data") or {}
    action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
    action_value = None
    for item in reversed(action_returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and isinstance(value.get("links"), list):
            action_value = value
            break
    if action_value is None:
        raise RuntimeError("Firecrawl không trả về dữ liệu phân trang Printerval.")

    advertised_count = action_value.get("advertised_count")
    pages_scanned = action_value.get("pages_scanned")
    if not isinstance(advertised_count, int) or advertised_count < 1:
        raise RuntimeError("Không đọc được tổng số sản phẩm Printerval công bố.")
    if not isinstance(pages_scanned, int) or pages_scanned < 1:
        pages_scanned = 1
    required_count = min(advertised_count, max_products)
    products_by_id = {}
    for link in action_value["links"]:
        if not isinstance(link, str):
            continue
        product_url = _canonical_product_link(link, normalised_url, domain, path_hint)
        match = re.search(r"-p(\d+)(?:\.html)?/?$", urlparse(product_url).path, re.I)
        if product_url and match:
            products_by_id.setdefault(match.group(1), product_url)
        if len(products_by_id) >= required_count:
            break
    if len(products_by_id) != required_count:
        raise RuntimeError(
            f"Printerval công bố {advertised_count} sản phẩm nhưng sau khi đi hết "
            f"{pages_scanned} trang chỉ thu được {len(products_by_id)} product ID duy nhất. "
            "Hệ thống đã dừng để tránh xuất CSV thiếu dữ liệu."
        )
    collection_products_by_id = {}
    for raw_product in action_value.get("products") or []:
        if not isinstance(raw_product, dict):
            continue
        product_url = _canonical_product_link(
            str(raw_product.get("product_url") or ""),
            normalised_url,
            domain,
            path_hint,
        )
        match = re.search(r"-p(\d+)(?:\.html)?/?$", urlparse(product_url).path, re.I)
        title = html.unescape(str(raw_product.get("title") or "")).strip()
        price = raw_product.get("price")
        images = []
        for image in raw_product.get("images") or []:
            if not isinstance(image, str) or not image.strip():
                continue
            absolute = urljoin(normalised_url, image.strip())
            if urlparse(absolute).scheme in ("http", "https") and absolute not in images:
                images.append(absolute)
            if len(images) >= 2:
                break
        if not match:
            continue
        collection_products_by_id.setdefault(match.group(1), {
            "title": title,
            "price": price,
            "compare_at_price": raw_product.get("compare_at_price"),
            "currency": str(raw_product.get("currency") or "").upper(),
            "product_url": product_url,
            "images": images,
        })

    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    return {
        "urls": list(products_by_id.values()),
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": advertised_count > max_products,
        "discovery_mode": "printerval_search",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "waf_blocked": False,
        "products": [
            collection_products_by_id[product_id]
            for product_id in products_by_id
            if product_id in collection_products_by_id
        ],
        "card_diagnostics": action_value.get("card_diagnostics") or {},
    }


def _discover_wordpress_search_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Follow a WordPress/WooCommerce search through its real pagination.

    WordPress searches commonly live at ``/?s=term`` rather than ``/search``.
    The browser action follows the theme's actual next-page URL and falls back
    to both pretty ``/page/N/`` and plain ``paged=N`` pagination when needed.
    """
    if not _is_wordpress_search_url(search_url):
        return None

    normalised_url = _wordpress_search_start_url(search_url)
    script = f"""
(async () => {{
  const maxProducts = {int(max_products)};
  const source = new URL(location.href);
  function advertisedCount(doc) {{
    const countText = Array.from(doc.querySelectorAll(
      '.woocommerce-result-count, [class*="result-count"], [data-result-count]'
    )).map(node => node.innerText || node.textContent || '').join('\\n');
    const bodyText = doc.body && doc.body.innerText || '';
    const text = countText + '\\n' + bodyText;
    const patterns = [
      /\\bof\\s+([\\d,.]+)\\s+(?:results?|products?|items?)\\b/gi,
      /showing\\s+\\d+\\s*[^\\d\\s]+\\s*\\d+\\s+of\\s+([\\d,.]+)/gi,
      /([\\d,.]+)\\s+results?\\b/gi,
      /showing\\s+\\d+[–-]\\d+\\s+of\\s+([\\d,.]+)/gi,
      /([\\d,.]+)\\s+products?\\b/gi
    ];
    const values = [];
    patterns.forEach(pattern => {{
      for (const match of text.matchAll(pattern)) {{
        const value = Number(match[1].replace(/\\D/g, ''));
        if (value) values.push(value);
      }}
    }});
    return Math.max(0, ...values);
  }}
  function productLinks(doc) {{
    const selectors = [
      'ul.products li.product a[href*="/product/"]',
      '.woocommerce ul.products li.product a[href*="/product/"]',
      'main article.product a[href*="/product/"]',
      'main [class*="product-grid"] a[href*="/product/"]',
      '#main [class*="product"] a[href*="/product/"]'
    ];
    const anchors = [];
    for (const selector of selectors) {{
      const found = Array.from(doc.querySelectorAll(selector));
      if (found.length) {{ anchors.push(...found); break; }}
    }}
    if (!anchors.length) {{
      const root = doc.querySelector('main, #main, .site-main') || doc.body;
      anchors.push(...Array.from(root.querySelectorAll('a[href*="/product/"]'))
        .filter(anchor => !anchor.closest(
          'header, footer, nav, .menu, .related, .recommend, .upsell'
        )));
    }}
    return anchors.map(anchor => {{
      try {{ return new URL(anchor.getAttribute('href'), location.origin).href; }}
      catch (_) {{ return ''; }}
    }}).filter(Boolean);
  }}
  function nextPageUrl(doc) {{
    const node = doc.querySelector(
      '.woocommerce-pagination a.next[href], a.next.page-numbers[href], '
      + 'nav.pagination a.next[href], a[rel="next"][href], '
      + '.load-more[data-url], [data-load-more][data-url]'
    );
    const value = node && (
      node.getAttribute('href') || node.getAttribute('data-url')
    );
    if (!value) return '';
    try {{ return new URL(value, location.origin).href; }} catch (_) {{ return ''; }}
  }}
  function fallbackPageUrls(page) {{
    const basePath = source.pathname.replace(/\/page\/\d+\/?$/i, '/');

    // Most WordPress sites with pretty permalinks use /page/2/?s=term.
    const prettyTarget = new URL(source.href);
    prettyTarget.pathname = `${{basePath.replace(/\/?$/, '/')}}page/${{page}}/`;
    prettyTarget.searchParams.delete('paged');

    // Plain-permalink installations may instead use ?paged=2.
    const queryTarget = new URL(source.href);
    queryTarget.pathname = basePath;
    queryTarget.searchParams.set('paged', String(page));
    return [prettyTarget.href, queryTarget.href];
  }}
  async function loadPage(targetUrls) {{
    for (const targetUrl of targetUrls) {{
      const response = await fetch(targetUrl, {{credentials: 'same-origin'}});
      if (response.ok) {{
        return new DOMParser().parseFromString(await response.text(), 'text/html');
      }}
      // A missing pretty-permalink candidate can still have a valid ?paged=
      // equivalent. Try it before treating pagination as exhausted.
      if (response.status !== 404) {{
        throw new Error(`WordPress search ${{targetUrl}} HTTP ${{response.status}}`);
      }}
    }}
    return null;
  }}

  const total = advertisedCount(document);
  const required = Math.min(total || maxProducts, maxProducts);
  const uniqueLinks = new Set(productLinks(document));
  if (!uniqueLinks.size) throw new Error('WordPress product grid was not found');
  let page = 1;
  let pagesScanned = 1;
  let nextUrl = nextPageUrl(document);
  const visited = new Set([location.href]);
  let noNewPages = 0;
  let paginationExhausted = false;
  while (uniqueLinks.size < required && page < 500) {{
    page += 1;
    const candidates = Array.from(new Set([
      ...(nextUrl ? [nextUrl] : []),
      ...fallbackPageUrls(page)
    ])).filter(target => !visited.has(target));
    if (!candidates.length) {{
      paginationExhausted = true;
      break;
    }}
    candidates.forEach(target => visited.add(target));
    const before = uniqueLinks.size;
    const doc = await loadPage(candidates);
    if (!doc) {{
      paginationExhausted = true;
      break;
    }}
    pagesScanned += 1;
    productLinks(doc).forEach(link => uniqueLinks.add(link));
    nextUrl = nextPageUrl(doc);
    noNewPages = uniqueLinks.size === before ? noNewPages + 1 : 0;
    if (noNewPages >= 2) {{
      paginationExhausted = true;
      break;
    }}
  }}
  return {{
    advertised_count: total,
    pages_scanned: pagesScanned,
    scan_complete: total > 0
      ? uniqueLinks.size >= required
      : paginationExhausted,
    links: Array.from(uniqueLinks).slice(0, required)
  }};
}})()
"""
    response = _post_scrape_with_retry(
        client,
        {
            "url": normalised_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": [
                {"type": "wait", "milliseconds": 800},
                {"type": "executeJavascript", "script": script},
            ],
        },
        timeout=150,
    )
    if response.is_error:
        raise RuntimeError(
            f"Firecrawl WordPress search {response.status_code}: {response.text[:500]}"
        )

    data = response.json().get("data") or {}
    action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
    action_value = None
    for item in reversed(action_returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and isinstance(value.get("links"), list):
            action_value = value
            break
    if action_value is None:
        raise RuntimeError("Firecrawl không trả về product grid WordPress.")

    raw_advertised_count = action_value.get("advertised_count")
    try:
        advertised_count = int(raw_advertised_count)
    except (TypeError, ValueError):
        advertised_count = 0
    has_advertised_count = advertised_count > 0
    pages_scanned = action_value.get("pages_scanned")
    if not isinstance(pages_scanned, int) or pages_scanned < 1:
        pages_scanned = 1

    required_count = (
        min(advertised_count, max_products)
        if has_advertised_count
        else max_products
    )
    product_urls = []
    seen_urls = set()
    for link in action_value["links"]:
        if not isinstance(link, str):
            continue
        product_url = _canonical_product_link(
            link, normalised_url, domain, path_hint
        )
        if product_url and product_url not in seen_urls:
            seen_urls.add(product_url)
            product_urls.append(product_url)
            if len(product_urls) >= required_count:
                break

    if has_advertised_count and len(product_urls) != required_count:
        raise RuntimeError(
            f"Trang WordPress công bố {advertised_count} sản phẩm nhưng chỉ thu được "
            f"{len(product_urls)} URL sản phẩm duy nhất sau {pages_scanned} trang. "
            "Hệ thống đã dừng để tránh xuất CSV thiếu dữ liệu."
        )
    if not has_advertised_count:
        if not product_urls:
            raise RuntimeError("Không tìm thấy URL sản phẩm WordPress.")
        if not action_value.get("scan_complete") and len(product_urls) < max_products:
            raise RuntimeError(
                "Không đọc được tổng số kết quả WordPress và chưa xác nhận "
                "đã đi hết các trang phân trang."
            )
        advertised_count = len(product_urls)

    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    return {
        "urls": product_urls,
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": (
            advertised_count > max_products
            or (not has_advertised_count and len(product_urls) >= max_products)
        ),
        "discovery_mode": "wordpress_search",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "waf_blocked": False,
    }


def _sfcc_search_start_url(search_url: str) -> str:
    """Normalise an SFCC search to its first deterministic 48-item batch."""
    parsed = urlparse(search_url)
    replaced = COLLECTION_PAGINATION_KEYS | {"start", "sz"}
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in replaced
    ]
    query.extend([
        ("start", "0"),
        ("sz", str(SFCC_SEARCH_PAGE_SIZE)),
    ])
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _discover_sfcc_search_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Read an SFCC result grid with its real ``start``/``sz`` pagination.

    BoxLunch/Hot Topic protect catalogue requests with a WAF, so the batches
    are fetched concurrently inside one Firecrawl browser session.  Only links
    under ``.product-grid`` count; global navigation and recommendations also
    contain ``/product/`` links and must never be included in the advertised
    search total.
    """
    if not _is_sfcc_search_url(search_url):
        return None

    normalised_url = _sfcc_search_start_url(search_url)
    script = f"""
(async () => {{
  const maxProducts = {int(max_products)};
  function advertisedCount(doc) {{
    const matches = Array.from(
      (doc.body && doc.body.innerText || '').matchAll(/([\\d,]+)\\s+results?\\b/gi)
    ).map(match => Number(match[1].replace(/,/g, '')) || 0);
    return Math.max(0, ...matches);
  }}
  function productLinks(doc) {{
    const root = doc.querySelector('.product-grid') || doc;
    return Array.from(root.querySelectorAll(
      '.product-tile a[href*="/product/"], a.product-tile[href*="/product/"]'
    ))
      .map(anchor => {{
        try {{ return new URL(anchor.getAttribute('href'), location.origin).href; }}
        catch (_) {{ return ''; }}
      }})
      .filter(Boolean);
  }}
  function nextBatchUrl(doc) {{
    const node = doc.querySelector(
      '.show-more [data-url], [data-ref="show-more"] [data-url], '
      + '.grid-footer [data-url*="start="], a.show-more[href]'
    );
    const value = node && (
      node.getAttribute('data-url') || node.getAttribute('href')
    );
    if (!value) return '';
    try {{ return new URL(value, location.origin).href; }} catch (_) {{ return ''; }}
  }}
  async function loadBatch(targetUrl) {{
    const response = await fetch(targetUrl, {{credentials: 'same-origin'}});
    if (!response.ok) throw new Error(`SFCC search HTTP ${{response.status}}`);
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    return {{links: productLinks(doc), next_url: nextBatchUrl(doc)}};
  }}

  const total = advertisedCount(document);
  if (!total) throw new Error('SFCC advertised result count was not found');
  const required = Math.min(total, maxProducts);
  const uniqueLinks = new Set(productLinks(document));
  let pagesScanned = 1;
  let nextUrl = nextBatchUrl(document);
  const visitedBatchUrls = new Set();
  while (nextUrl && uniqueLinks.size < required && pagesScanned < 500) {{
    if (visitedBatchUrls.has(nextUrl)) break;
    visitedBatchUrls.add(nextUrl);
    const batch = await loadBatch(nextUrl);
    batch.links.forEach(link => uniqueLinks.add(link));
    nextUrl = batch.next_url;
    pagesScanned += 1;
  }}

  // A live relevance result can move while pagination is in progress. If one
  // boundary duplicated an item, restart the official Load-more chain once
  // and union both snapshots instead of exporting 484/485 rows.
  let recoveryPasses = 0;
  if (uniqueLinks.size < required) {{
    recoveryPasses = 1;
    const refreshResponse = await fetch(location.href, {{
      credentials: 'same-origin', cache: 'no-store'
    }});
    if (!refreshResponse.ok) {{
      throw new Error(`SFCC recovery HTTP ${{refreshResponse.status}}`);
    }}
    let recoveryDoc = new DOMParser().parseFromString(
      await refreshResponse.text(), 'text/html'
    );
    productLinks(recoveryDoc).forEach(link => uniqueLinks.add(link));
    let recoveryUrl = nextBatchUrl(recoveryDoc);
    const recoveryVisited = new Set();
    while (recoveryUrl && uniqueLinks.size < required && recoveryVisited.size < 500) {{
      if (recoveryVisited.has(recoveryUrl)) break;
      recoveryVisited.add(recoveryUrl);
      const batch = await loadBatch(recoveryUrl);
      batch.links.forEach(link => uniqueLinks.add(link));
      recoveryUrl = batch.next_url;
    }}
  }}
  return {{
    advertised_count: total,
    pages_scanned: pagesScanned,
    recovery_passes: recoveryPasses,
    links: Array.from(uniqueLinks).slice(0, required)
  }};
}})()
"""
    response = _post_scrape_with_retry(
        client,
        {
            "url": normalised_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": [
                {"type": "wait", "milliseconds": 1200},
                {"type": "executeJavascript", "script": script},
            ],
        },
        timeout=150,
    )
    if response.is_error:
        raise RuntimeError(f"Firecrawl SFCC {response.status_code}: {response.text[:500]}")

    data = response.json().get("data") or {}
    action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
    action_value = None
    for item in reversed(action_returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and isinstance(value.get("links"), list):
            action_value = value
            break
    if action_value is None:
        raise RuntimeError("Firecrawl không trả về dữ liệu product-grid của SFCC.")

    advertised_count = action_value.get("advertised_count")
    pages_scanned = action_value.get("pages_scanned")
    recovery_passes = action_value.get("recovery_passes")
    if not isinstance(advertised_count, int) or advertised_count < 1:
        raise RuntimeError("Không đọc được tổng số sản phẩm mà trang SFCC công bố.")
    if not isinstance(pages_scanned, int) or pages_scanned < 1:
        raise RuntimeError("Số batch SFCC trả về không hợp lệ.")
    if not isinstance(recovery_passes, int) or recovery_passes < 0:
        recovery_passes = 0

    required_count = min(advertised_count, max_products)
    product_urls = []
    seen_urls = set()
    for link in action_value["links"]:
        if not isinstance(link, str):
            continue
        product_url = _canonical_product_link(
            link, normalised_url, domain, path_hint
        )
        if product_url and product_url not in seen_urls:
            seen_urls.add(product_url)
            product_urls.append(product_url)
            if len(product_urls) >= required_count:
                break

    if len(product_urls) != required_count:
        raise RuntimeError(
            f"Trang SFCC công bố {advertised_count} sản phẩm nhưng product-grid chỉ "
            f"trả về {len(product_urls)} URL duy nhất. Hệ thống đã dừng để tránh "
            "xuất CSV thiếu dữ liệu."
        )

    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    return {
        "urls": product_urls,
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": advertised_count > max_products,
        "discovery_mode": "sfcc_search",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "recovery_passes": recovery_passes,
        "waf_blocked": True,
    }


def _sitebase_search_params(search_url: str, page: int) -> list[tuple[str, str]]:
    """Translate a SiteBase search URL to its stable catalogue API params."""
    replaced = {
        "page",
        "limit",
        "minimal",
        "infinite",
        "sort",
        "sort_field",
        "sort_direction",
    }
    params = [
        (key, value)
        for key, value in parse_qsl(urlparse(search_url).query, keep_blank_values=True)
        if key.lower() not in replaced
    ]
    params.extend(
        [
            ("page", str(page)),
            ("limit", str(SEARCH_API_PAGE_SIZE)),
            ("minimal", "true"),
            ("infinite", "true" if page > 1 else "false"),
            ("sort", ""),
            # ``most_related`` changes order between requests and caused the
            # same product to appear on multiple pages. SiteBase's supported
            # deterministic field is ``created`` (not ``created_at``).
            ("sort_field", "created"),
            ("sort_direction", "desc"),
        ]
    )
    return params


def _sitebase_search_response(
    client: httpx.Client,
    api_url: str,
    search_url: str,
    page: int,
    *,
    attempts: int = 3,
) -> httpx.Response:
    """GET one SiteBase search page with short transient-error retries."""
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            response = client.get(
                api_url,
                params=_sitebase_search_params(search_url, page),
                headers={
                    "Accept": "application/json",
                    "Referer": search_url,
                    "User-Agent": "Mozilla/5.0 (compatible; CollectionCSV/1.0)",
                },
                # Catalogue responses normally arrive in under a second. A
                # shorter bound prevents one straggling page from holding a
                # 100+ page scan open for several minutes across retries.
                timeout=12,
                follow_redirects=True,
            )
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                return response
            last_error = RuntimeError(
                f"Storefront API {response.status_code}: {response.text[:300]}"
            )
        except httpx.HTTPError as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.35 * (2**attempt))
    raise RuntimeError(f"Không đọc ổn định được storefront API: {last_error}")


def _sitebase_search_result(response: httpx.Response) -> tuple[list[dict], int] | None:
    """Parse SiteBase's ``result.items`` payload, or decline non-SiteBase data."""
    if response.is_error:
        return None
    try:
        body = response.json()
    except (TypeError, ValueError):
        return None
    result = body.get("result") if isinstance(body, dict) else None
    if body.get("code") != 0 or not isinstance(result, dict):
        return None
    items = result.get("items")
    count = result.get("count")
    if not isinstance(items, list) or not isinstance(count, int) or count < 0:
        return None
    return items, count


def _sitebase_collection_request(
    client: httpx.Client,
    api_url: str,
    collection_url: str,
    params: list[tuple[str, str]],
    *,
    attempts: int = 3,
) -> httpx.Response:
    """Read one public ShopBase catalogue endpoint with short retries."""
    last_error = None
    for attempt in range(max(1, attempts)):
        try:
            response = client.get(
                api_url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "Referer": collection_url,
                    "User-Agent": "Mozilla/5.0 (compatible; CollectionCSV/1.0)",
                },
                timeout=45,
                follow_redirects=True,
            )
            if response.status_code not in {408, 429, 500, 502, 503, 504}:
                return response
            last_error = RuntimeError(
                f"ShopBase API {response.status_code}: {response.text[:300]}"
            )
        except httpx.HTTPError as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.35 * (2**attempt))
    raise RuntimeError(f"Không đọc ổn định được ShopBase API: {last_error}")


def _sitebase_collection_product(item: dict, collection_url: str) -> dict | None:
    """Normalise one ShopBase catalogue row, including image-less products.

    A SiteBase catalogue count can legitimately include products whose card
    has no image (resend-fee products are a common example).  Keeping those
    rows lets the fast collection export remain complete; their CSV
    ``image_count`` is simply zero instead of disabling the mode for every
    other product in a large search.
    """
    if not isinstance(item, dict):
        return None
    handle = str(item.get("handle") or "").strip().strip("/")
    title = html.unescape(str(item.get("title") or "")).strip()
    variants = [row for row in item.get("variants") or [] if isinstance(row, dict)]
    selected = variants[0] if variants else {}
    price = item.get("price")
    if price in (None, ""):
        price = item.get("min_price")
    if price in (None, ""):
        price = selected.get("price")
    compare_at_price = item.get("min_price_compare")
    if compare_at_price in (None, "", 0, 0.0):
        compare_at_price = selected.get("compare_at_price")
    images = []
    for image in item.get("images") or []:
        raw_url = image.get("src") if isinstance(image, dict) else image
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        image_url = urljoin(collection_url, raw_url.strip())
        if (
            urlparse(image_url).scheme in ("http", "https")
            and not re.search(r"\.svg(?:$|[?#])", image_url, re.I)
            and image_url not in images
        ):
            images.append(image_url)
    if not handle or not title or price in (None, ""):
        return None
    return {
        "title": title,
        "price": price,
        "compare_at_price": compare_at_price,
        "currency": str(item.get("currency") or "").upper(),
        "product_url": urljoin(collection_url, f"/products/{handle}"),
        "images": images,
    }


def _discover_sitebase_collection_products(
    client: httpx.Client,
    collection_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
    strict: bool = False,
) -> dict | None:
    """Fetch a ShopBase collection through its public catalogue API.

    ShopBase collection pages commonly render 12 or 24 cards even when the
    collection contains hundreds of products. The catalogue API exposes the
    authoritative count and card image arrays. Product pages can contain more
    gallery images, so full-gallery mode must still open each product page.
    """
    parsed = urlparse(collection_url)
    handle_match = re.match(
        r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?collections/([^/]+)/?$",
        parsed.path,
        re.I,
    )
    if not handle_match:
        return None
    handle = unquote(handle_match.group(1)).strip()
    origin = urlunparse(parsed._replace(path="", params="", query="", fragment=""))
    collections_url = urljoin(origin, SITEBASE_COLLECTION_API_PATH)
    try:
        collection_response = _sitebase_collection_request(
            client,
            collections_url,
            collection_url,
            [("handles", handle), ("limit", "1")],
            attempts=3 if strict else 1,
        )
    except RuntimeError:
        # This is a platform probe. A non-ShopBase store must continue to its
        # own adapter instead of failing the entire collection scan.
        if strict:
            raise
        return None
    collection_result = _sitebase_search_result(collection_response)
    if collection_result is None:
        if strict:
            raise RuntimeError(
                "URL Ä‘Ã£ chá»n ShopBase nhÆ°ng Catalogue API khÃ´ng tráº£ vá» dá»¯ liá»‡u "
                "ShopBase há»£p lá»‡. HÃ£y kiá»ƒm tra URL collection hoáº·c thá»­ láº¡i sau."
            )
        return None
    collection_items, _ = collection_result
    collection = next(
        (
            item
            for item in collection_items
            if isinstance(item, dict)
            and str(item.get("handle") or "").strip().lower() == handle.lower()
        ),
        None,
    )
    if collection is None or collection.get("id") in (None, ""):
        if strict:
            raise RuntimeError(
                f"ShopBase khÃ´ng tÃ¬m tháº¥y collection handle '{handle}' trong Catalogue API."
            )
        return None

    sort_order = str(collection.get("sort_order") or "created:desc")
    sort_field, _, sort_direction = sort_order.partition(":")
    if not re.fullmatch(r"[a-z_]+", sort_field, re.I):
        sort_field = "created"
    if sort_direction.lower() not in {"asc", "desc"}:
        sort_direction = "desc"
    products_url = urljoin(origin, SITEBASE_SEARCH_API_PATH)
    collection_id = str(collection["id"])

    def load_page(page: int) -> tuple[int, list[dict], int]:
        response = _sitebase_collection_request(
            client,
            products_url,
            collection_url,
            [
                ("page", str(page)),
                ("limit", str(SEARCH_API_PAGE_SIZE)),
                ("sort_direction", sort_direction.lower()),
                ("sort_field", sort_field),
                ("minimal", "true"),
                ("infinite", "true" if page > 1 else "false"),
                ("collection_ids", collection_id),
            ],
        )
        parsed_result = _sitebase_search_result(response)
        if parsed_result is None:
            raise RuntimeError(
                f"ShopBase API trả dữ liệu không hợp lệ ở trang {page}."
            )
        items, count = parsed_result
        return page, items, count

    first_page, first_items, advertised_count = load_page(1)
    required_count = min(advertised_count, max_products)
    page_count = math.ceil(required_count / SEARCH_API_PAGE_SIZE) if required_count else 1
    page_results = [(first_page, first_items, advertised_count)]
    remaining_pages = list(range(2, page_count + 1))
    if remaining_pages:
        worker_count = min(SEARCH_API_WORKERS, len(remaining_pages))
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            page_results.extend(pool.map(load_page, remaining_pages))
    page_results.sort(key=lambda result: result[0])

    products_by_url = {}
    for _, items, count in page_results:
        if count != advertised_count:
            raise RuntimeError(
                "Số sản phẩm ShopBase thay đổi trong lúc quét; hãy chạy lại để "
                "tránh CSV bị lệch dữ liệu."
            )
        for item in items:
            product = _sitebase_collection_product(item, collection_url)
            if product is None:
                continue
            canonical_url = _canonical_product_link(
                product["product_url"], collection_url, domain, path_hint
            )
            if not canonical_url:
                continue
            product["product_url"] = canonical_url
            products_by_url.setdefault(canonical_url, product)
            if len(products_by_url) >= required_count:
                break
        if len(products_by_url) >= required_count:
            break

    if len(products_by_url) != required_count:
        raise RuntimeError(
            f"Collection ShopBase công bố {advertised_count} sản phẩm nhưng API chỉ "
            f"trả về {len(products_by_url)} sản phẩm đầy đủ. Hệ thống đã dừng để "
            "tránh xuất CSV thiếu dữ liệu."
        )
    return {
        "urls": list(products_by_url),
        "products": list(products_by_url.values()),
        "pages_scanned": page_count,
        "credits_used": 0,
        "truncated": advertised_count > max_products,
        "discovery_mode": "shopbase_collection_api",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        # ShopBase's collection API commonly exposes only the first one or two
        # card images. It is not authoritative for the product detail gallery.
        "full_gallery_available": False,
        "waf_blocked": False,
        "card_diagnostics": {
            "returned": len(products_by_url),
            "with_title": len(products_by_url),
            "with_price": len(products_by_url),
            "with_images": len(products_by_url),
            "missing_sample": [],
        },
    }


def _discover_sitebase_collection_via_browser(
    client: httpx.Client,
    collection_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Read a ShopBase collection API inside Firecrawl's browser session.

    This is the platform-specific fallback when ShopBase blocks the server's
    direct catalogue request. It never probes Shopify endpoints.
    """
    parsed = urlparse(collection_url)
    handle_match = re.match(
        r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?collections/([^/]+)/?$",
        parsed.path,
        re.I,
    )
    if not handle_match:
        return None
    handle = unquote(handle_match.group(1)).strip()
    script = f"""
(async () => {{
  const handle = {json.dumps(handle)};
  const pageSize = {SEARCH_API_PAGE_SIZE};
  const maxProducts = {int(max_products)};
  async function getJson(target) {{
    const response = await fetch(target.href, {{
      credentials: 'include', headers: {{'Accept': 'application/json'}}
    }});
    if (!response.ok) throw new Error(`ShopBase API HTTP ${{response.status}}`);
    const body = await response.json();
    if (!body || body.code !== 0 || !body.result) {{
      throw new Error('Invalid ShopBase API response');
    }}
    return body.result;
  }}
  const collectionsUrl = new URL('{SITEBASE_COLLECTION_API_PATH}', location.origin);
  collectionsUrl.searchParams.set('handles', handle);
  collectionsUrl.searchParams.set('limit', '1');
  const collections = await getJson(collectionsUrl);
  const collection = (collections.items || []).find(item =>
    item && String(item.handle || '').toLowerCase() === handle.toLowerCase()
  );
  if (!collection || collection.id == null) {{
    throw new Error('ShopBase collection handle not found');
  }}
  const sortParts = String(collection.sort_order || 'created:desc').split(':');
  const sortField = /^[a-z_]+$/i.test(sortParts[0] || '') ? sortParts[0] : 'created';
  const sortDirection = /^(asc|desc)$/i.test(sortParts[1] || '')
    ? sortParts[1].toLowerCase() : 'desc';
  async function loadPage(page) {{
    const target = new URL('{SITEBASE_SEARCH_API_PATH}', location.origin);
    target.searchParams.set('page', String(page));
    target.searchParams.set('limit', String(pageSize));
    target.searchParams.set('minimal', 'true');
    target.searchParams.set('infinite', page > 1 ? 'true' : 'false');
    target.searchParams.set('sort_field', sortField);
    target.searchParams.set('sort_direction', sortDirection);
    target.searchParams.set('collection_ids', String(collection.id));
    return await getJson(target);
  }}
  const first = await loadPage(1);
  const advertisedCount = Number(first.count || 0);
  const requiredCount = Math.min(advertisedCount, maxProducts);
  const pagesScanned = Math.max(1, Math.ceil(requiredCount / pageSize));
  const items = Array.isArray(first.items) ? first.items.slice() : [];
  const pages = Array.from(
    {{length: Math.max(0, pagesScanned - 1)}}, (_, index) => index + 2
  );
  for (let offset = 0; offset < pages.length; offset += {SEARCH_API_BROWSER_CONCURRENCY}) {{
    const chunk = pages.slice(offset, offset + {SEARCH_API_BROWSER_CONCURRENCY});
    const results = await Promise.all(chunk.map(loadPage));
    results.forEach(result => {{
      if (Number(result.count || 0) !== advertisedCount) {{
        throw new Error('ShopBase collection count changed while fetching');
      }}
      if (Array.isArray(result.items)) items.push(...result.items);
    }});
  }}
  return {{
    advertised_count: advertisedCount,
    pages_scanned: pagesScanned,
    products: items.slice(0, requiredCount).map(item => ({{
      handle: item && item.handle,
      title: item && item.title,
      price: item && item.price,
      min_price: item && item.min_price,
      min_price_compare: item && item.min_price_compare,
      currency: item && item.currency,
      images: Array.isArray(item && item.images) ? item.images.slice(0, 2) : []
    }}))
  }};
}})()
"""
    response = _post_scrape_with_retry(
        client,
        {
            "url": collection_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": [
                {"type": "wait", "milliseconds": 1000},
                {"type": "executeJavascript", "script": script},
            ],
        },
        timeout=150,
    )
    if response.is_error:
        return None
    data = response.json().get("data") or {}
    action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
    action_value = None
    for item in reversed(action_returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and isinstance(value.get("products"), list):
            action_value = value
            break
    if action_value is None:
        return None

    advertised_count = action_value.get("advertised_count")
    pages_scanned = action_value.get("pages_scanned")
    if not isinstance(advertised_count, int) or advertised_count < 0:
        return None
    if not isinstance(pages_scanned, int) or pages_scanned < 1:
        return None
    required_count = min(advertised_count, max_products)
    products_by_url = {}
    for item in action_value["products"]:
        product = _sitebase_collection_product(item, collection_url)
        if product is None:
            continue
        canonical_url = _canonical_product_link(
            product["product_url"], collection_url, domain, path_hint
        )
        if not canonical_url:
            continue
        product["product_url"] = canonical_url
        products_by_url.setdefault(canonical_url, product)

    if len(products_by_url) != required_count:
        raise RuntimeError(
            f"Collection ShopBase cÃ´ng bá»‘ {advertised_count} sáº£n pháº©m nhÆ°ng Catalogue API "
            f"trong trÃ¬nh duyá»‡t chá»‰ tráº£ vá» {len(products_by_url)} sáº£n pháº©m Ä‘áº§y Ä‘á»§."
        )
    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    products = list(products_by_url.values())
    return {
        "urls": list(products_by_url),
        "products": products,
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": advertised_count > max_products,
        "discovery_mode": "shopbase_collection_api",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "full_gallery_available": False,
        "waf_blocked": False,
        "card_diagnostics": {
            "returned": len(products),
            "with_title": len(products),
            "with_price": len(products),
            "with_images": len(products),
            "missing_sample": [],
        },
    }


def _discover_sitebase_search_via_browser(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Call SiteBase's API inside Firecrawl's browser when its WAF blocks HTTP.

    The page's browser context has the storefront cookies required by security
    middleware. All catalogue pages are still fetched by stable API pagination;
    no visual scrolling or DOM-card guessing is involved.
    """
    script = f"""
(async () => {{
  const pageSize = {SEARCH_API_PAGE_SIZE};
  const maxProducts = {int(max_products)};
  const replaced = new Set([
    'page', 'limit', 'minimal', 'infinite', 'sort',
    'sort_field', 'sort_direction'
  ]);
  // Security challenge redirects may drop the original search query. Keep
  // the caller's exact URL so q/filter parameters survive the browser flow.
  const source = new URL({json.dumps(search_url)});
  const api = new URL('{SITEBASE_SEARCH_API_PATH}', location.origin);
  for (const [key, value] of source.searchParams.entries()) {{
    if (!replaced.has(key.toLowerCase())) api.searchParams.append(key, value);
  }}
  async function loadPage(page) {{
    const target = new URL(api.href);
    target.searchParams.set('page', String(page));
    target.searchParams.set('limit', String(pageSize));
    target.searchParams.set('minimal', 'true');
    target.searchParams.set('infinite', page > 1 ? 'true' : 'false');
    target.searchParams.set('sort', '');
    target.searchParams.set('sort_field', 'created');
    target.searchParams.set('sort_direction', 'desc');
    let lastStatus = 0;
    for (let attempt = 0; attempt < 4; attempt += 1) {{
      try {{
        const response = await fetch(
          target.href, {{headers: {{'Accept': 'application/json'}}}}
        );
        lastStatus = response.status;
        if (response.ok) {{
          const body = await response.json();
          if (body && body.code === 0 && body.result) return body.result;
        }}
      }} catch (_) {{}}
      if (attempt < 3) {{
        await new Promise(resolve => setTimeout(resolve, 1500 * (2 ** attempt)));
      }}
    }}
    throw new Error('Search API HTTP ' + lastStatus);
  }}
  const first = await loadPage(1);
  const advertisedCount = Number(first.count || 0);
  const requiredCount = Math.min(advertisedCount, maxProducts);
  const pagesScanned = Math.max(1, Math.ceil(requiredCount / pageSize));
  const pages = Array.from(
    {{length: Math.max(0, pagesScanned - 1)}}, (_, index) => index + 2
  );
  async function loadAllPages(firstResult) {{
    const items = Array.isArray(firstResult.items) ? firstResult.items.slice() : [];
    for (let offset = 0; offset < pages.length; offset += {SEARCH_API_BROWSER_CONCURRENCY}) {{
      const chunk = pages.slice(offset, offset + {SEARCH_API_BROWSER_CONCURRENCY});
      const results = await Promise.all(chunk.map(loadPage));
      results.forEach(result => {{
        if (Number(result.count || 0) !== advertisedCount) {{
          throw new Error('Search count changed while fetching');
        }}
        if (Array.isArray(result.items)) items.push(...result.items);
      }});
    }}
    return items.slice(0, requiredCount);
  }}
  let items = await loadAllPages(first);
  const canValidateIds = items.length === requiredCount
    && items.every(item => item && item.id !== undefined && item.id !== null);
  const recordsById = new Map();
  if (canValidateIds) {{
    items.forEach(item => recordsById.set(String(item.id), item));
  }}
  let recoveryPasses = 0;
  while (canValidateIds && recordsById.size < requiredCount && recoveryPasses < 2) {{
    recoveryPasses += 1;
    const recovered = await loadAllPages(await loadPage(1));
    recovered.forEach(item => {{
      if (item && item.id !== undefined && item.id !== null) {{
        recordsById.set(String(item.id), item);
      }}
    }});
  }}
  if (canValidateIds && recordsById.size < requiredCount) {{
    throw new Error(`Only ${{recordsById.size}} unique product IDs after retries`);
  }}
  if (canValidateIds) {{
    items = Array.from(recordsById.values()).slice(0, requiredCount);
  }}
  return {{
    advertised_count: advertisedCount,
    pages_scanned: pagesScanned,
    recovery_passes: recoveryPasses,
    // Preserve only the fields needed for the 1–2 image card export. Returning
    // just id/handle discarded useful data, while returning the entire 8k-row
    // payload (variants, descriptions, metadata) can exceed action limits.
    records: items.map(item => ({{
      id: item && item.id,
      handle: item && item.handle,
      title: item && item.title,
      price: item && item.price,
      min_price: item && item.min_price,
      min_price_compare: item && item.min_price_compare,
      currency: item && item.currency,
      variants: Array.isArray(item && item.variants) && item.variants.length
        ? [{{
            price: item.variants[0] && item.variants[0].price,
            compare_at_price: item.variants[0] && item.variants[0].compare_at_price
          }}]
        : [],
      images: Array.isArray(item && item.images) ? item.images.slice(0, 2) : []
    }})),
    handles: items.slice(0, requiredCount)
      .map(item => item && item.handle).filter(Boolean)
  }};
}})()
"""
    response = _post_scrape_with_retry(
        client,
        {
            "url": search_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": [
                {"type": "wait", "milliseconds": 5000},
                {"type": "executeJavascript", "script": script},
            ],
        },
        timeout=150,
    )
    if response.is_error:
        return None
    data = response.json().get("data") or {}
    action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
    action_value = None
    for item in reversed(action_returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and isinstance(value.get("handles"), list):
            action_value = value
            break
    if action_value is None:
        return None

    advertised_count = action_value.get("advertised_count")
    pages_scanned = action_value.get("pages_scanned")
    if not isinstance(advertised_count, int) or advertised_count < 0:
        return None
    if not isinstance(pages_scanned, int) or pages_scanned < 1:
        return None
    required_count = min(advertised_count, max_products)
    records = action_value.get("records")
    record_ids = [
        record.get("id") if isinstance(record, dict) else None
        for record in (records or [])
    ]
    can_validate_ids = (
        isinstance(records, list)
        and len(records) == required_count
        and all(item_id not in (None, "") for item_id in record_ids)
        and len({str(item_id) for item_id in record_ids}) == required_count
    )
    handles = (
        [record.get("handle") for record in records]
        if can_validate_ids
        else action_value["handles"]
    )
    product_urls = []
    seen_urls = set()
    invalid_handle_count = 0
    for handle in handles:
        if not isinstance(handle, str) or not handle.strip():
            invalid_handle_count += 1
            continue
        candidate = urljoin(search_url, f"/products/{handle.strip().strip('/')}")
        product_url = _canonical_product_link(candidate, search_url, domain, path_hint)
        if not product_url:
            invalid_handle_count += 1
            continue
        if product_url not in seen_urls:
            seen_urls.add(product_url)
            product_urls.append(product_url)
            if len(product_urls) >= required_count:
                break

    if invalid_handle_count:
        raise RuntimeError(
            f"Storefront API trong trình duyệt trả về {invalid_handle_count} bản ghi "
            "không có product handle hợp lệ."
        )
    if not can_validate_ids and len(product_urls) != required_count:
        raise RuntimeError(
            f"Trang search công bố {advertised_count} sản phẩm nhưng API trong "
            f"trình duyệt chỉ trả về {len(product_urls)} URL duy nhất. Hệ thống "
            "đã dừng để tránh xuất CSV thiếu dữ liệu."
        )
    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    products_by_url = {}
    if isinstance(records, list):
        for item in records:
            product = _sitebase_collection_product(item, search_url)
            if product is None:
                continue
            canonical_product_url = _canonical_product_link(
                product["product_url"], search_url, domain, path_hint
            )
            if canonical_product_url:
                product["product_url"] = canonical_product_url
                products_by_url.setdefault(canonical_product_url, product)
    products = [
        products_by_url[url]
        for url in product_urls
        if url in products_by_url
    ]
    products_with_images = sum(
        1 for product in products if product.get("images")
    )
    return {
        "urls": product_urls,
        "products": products,
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": advertised_count > max_products,
        "discovery_mode": "search_api",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "recovery_passes": int(action_value.get("recovery_passes") or 0),
        "duplicate_url_count": required_count - len(product_urls),
        "waf_blocked": True,
        "fallback_product_count": len(product_urls) - products_with_images,
        "full_gallery_available": False,
        "card_diagnostics": {
            "returned": len(products),
            "with_title": sum(1 for product in products if product.get("title")),
            "with_price": sum(
                1 for product in products
                if product.get("price") not in (None, "")
            ),
            "with_images": products_with_images,
            "missing_sample": [
                url for url in product_urls if url not in products_by_url
            ][:10],
        },
    }


def _discover_sitebase_search_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
    strict: bool = False,
    progress_callback=None,
) -> dict | None:
    """Use SiteBase's real infinite-scroll API and validate its advertised total.

    Pages after the first are fetched concurrently. This avoids browser scrolling,
    consumes no Firecrawl discovery credits, and preserves a stable result set.
    """
    parsed = urlparse(search_url)
    api_url = urlunparse(parsed._replace(path=SITEBASE_SEARCH_API_PATH, query="", fragment=""))

    def report_progress(**values) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(values)
        except Exception:
            # Progress reporting must never abort a valid storefront scan.
            pass

    report_progress(
        stage="connecting",
        completed=0,
        total=0,
        pages_completed=0,
        pages_total=0,
        message="Đang kết nối Catalogue API ShopBase...",
    )
    try:
        first_response = _sitebase_search_response(client, api_url, search_url, 1)
    except RuntimeError:
        if not strict:
            return None
        report_progress(
            stage="browser_fallback",
            message="API trực tiếp bị chặn; đang chuyển sang phiên trình duyệt Firecrawl...",
        )
        return _discover_sitebase_search_via_browser(
            client,
            search_url,
            domain=domain,
            path_hint=path_hint,
            max_products=max_products,
        )
    first_result = _sitebase_search_result(first_response)
    if first_result is None:
        response_text = str(first_response.text or "").lower()
        if strict or "security checking" in response_text:
            report_progress(
                stage="browser_fallback",
                message="Storefront yêu cầu xác minh; đang đọc API trong trình duyệt...",
            )
            return _discover_sitebase_search_via_browser(
                client,
                search_url,
                domain=domain,
                path_hint=path_hint,
                max_products=max_products,
            )
        return None

    first_items, advertised_count = first_result
    required_count = min(advertised_count, max_products)
    page_count = math.ceil(required_count / SEARCH_API_PAGE_SIZE) if required_count else 1
    report_progress(
        stage="fetching_pages",
        completed=min(len(first_items), required_count),
        total=required_count,
        pages_completed=1,
        pages_total=page_count,
        message=f"Đã đọc trang 1/{page_count} từ Catalogue API.",
    )

    def load_page(page: int) -> tuple[int, list[dict], int]:
        response = _sitebase_search_response(client, api_url, search_url, page)
        parsed_result = _sitebase_search_result(response)
        if parsed_result is None:
            raise RuntimeError(f"Storefront API trả dữ liệu không hợp lệ ở trang {page}.")
        items, count = parsed_result
        return page, items, count

    page_results = [(1, first_items, advertised_count)]
    remaining_pages = list(range(2, page_count + 1))
    if remaining_pages:
        worker_count = min(SEARCH_API_WORKERS, len(remaining_pages))
        try:
            with ThreadPoolExecutor(max_workers=worker_count) as pool:
                futures = {
                    pool.submit(load_page, page): page for page in remaining_pages
                }
                completed_records = min(len(first_items), required_count)
                for future in as_completed(futures):
                    page_result = future.result()
                    page_results.append(page_result)
                    completed_records = min(
                        required_count,
                        completed_records + len(page_result[1]),
                    )
                    report_progress(
                        stage="fetching_pages",
                        completed=completed_records,
                        total=required_count,
                        pages_completed=len(page_results),
                        pages_total=page_count,
                        message=(
                            f"Đã đọc {len(page_results)}/{page_count} trang "
                            f"({completed_records}/{required_count} sản phẩm)."
                        ),
                    )
        except RuntimeError:
            report_progress(
                stage="browser_fallback",
                message="Một trang API bị chặn; đang chuyển sang phiên trình duyệt...",
            )
            browser_result = _discover_sitebase_search_via_browser(
                client,
                search_url,
                domain=domain,
                path_hint=path_hint,
                max_products=max_products,
            )
            if browser_result is not None:
                return browser_result
            raise
    def validated_items(results: list[tuple[int, list[dict], int]]) -> list[dict]:
        results.sort(key=lambda result: result[0])
        items_in_order = []
        for _page, items, count in results:
            if count != advertised_count:
                raise RuntimeError(
                    "Số sản phẩm của storefront API thay đổi trong lúc quét; "
                    "hãy chạy lại để tránh CSV bị lệch dữ liệu."
                )
            items_in_order.extend(items)
        return items_in_order[:required_count]

    record_items = validated_items(page_results)
    recovery_passes = 0

    # SiteBase's count is a record count, not a unique-handle count. Validate
    # pagination with product IDs first: two distinct records may legitimately
    # share one handle/URL. Tied created_at values can also make one record
    # repeat at a page boundary, so retry the complete page set sequentially.
    record_ids = [
        item.get("id") if isinstance(item, dict) else None for item in record_items
    ]
    can_validate_ids = (
        len(record_items) == required_count
        and all(item_id not in (None, "") for item_id in record_ids)
    )
    records_by_id = {}
    if can_validate_ids:
        for item, item_id in zip(record_items, record_ids):
            records_by_id.setdefault(str(item_id), item)
    while (
        can_validate_ids
        and len(records_by_id) < required_count
        and recovery_passes < 2
    ):
        recovery_passes += 1
        report_progress(
            stage="validating",
            completed=len(records_by_id),
            total=required_count,
            pages_completed=page_count,
            pages_total=page_count,
            message=(
                "Phát hiện ID lặp ở ranh giới trang; đang đối chiếu lại "
                f"song song (lần {recovery_passes}/2)..."
            ),
        )
        with ThreadPoolExecutor(
            max_workers=min(SEARCH_API_WORKERS, page_count)
        ) as pool:
            page_results = list(pool.map(load_page, range(1, page_count + 1)))
        recovered_items = validated_items(page_results)
        recovered_ids = [
            item.get("id") if isinstance(item, dict) else None
            for item in recovered_items
        ]
        can_validate_ids = (
            len(recovered_items) == required_count
            and all(item_id not in (None, "") for item_id in recovered_ids)
        )
        if can_validate_ids:
            for item, item_id in zip(recovered_items, recovered_ids):
                records_by_id.setdefault(str(item_id), item)

    if can_validate_ids and len(records_by_id) < required_count:
        raise RuntimeError(
            f"Storefront API công bố {advertised_count} sản phẩm nhưng phân trang vẫn "
            f"chỉ trả về {len(records_by_id)} product ID duy nhất sau khi đã tự thử lại."
        )
    if can_validate_ids:
        # A truncated scan only promises at most max_products records. For a
        # full scan the stable count guarantees this map has exactly that many.
        record_items = list(records_by_id.values())[:required_count]

    product_urls = []
    seen_urls = set()
    products_by_url = {}
    invalid_handle_count = 0
    for item in record_items:
        handle = item.get("handle") if isinstance(item, dict) else None
        if not isinstance(handle, str) or not handle.strip():
            invalid_handle_count += 1
            continue
        candidate = urljoin(search_url, f"/products/{handle.strip().strip('/')}")
        product_url = _canonical_product_link(candidate, search_url, domain, path_hint)
        if not product_url:
            invalid_handle_count += 1
            continue
        if product_url not in seen_urls:
            seen_urls.add(product_url)
            product_urls.append(product_url)
        product = _sitebase_collection_product(item, search_url)
        if product is not None:
            canonical_product_url = _canonical_product_link(
                product["product_url"], search_url, domain, path_hint
            )
            if canonical_product_url:
                product["product_url"] = canonical_product_url
                products_by_url.setdefault(canonical_product_url, product)

    if invalid_handle_count:
        raise RuntimeError(
            f"Storefront API trả về {invalid_handle_count} bản ghi không có product handle hợp lệ."
        )

    duplicate_url_count = required_count - len(product_urls)
    if not can_validate_ids and len(product_urls) != required_count:
        # Older/alternate SiteBase payloads without IDs cannot prove that two
        # equal handles are distinct records, so retain strict mode.
        raise RuntimeError(
            f"Trang search công bố {advertised_count} sản phẩm nhưng storefront API "
            f"chỉ trả về {len(product_urls)} URL duy nhất. Hệ thống đã dừng để tránh "
            "xuất CSV thiếu dữ liệu."
        )

    products = [
        products_by_url[url]
        for url in product_urls
        if url in products_by_url
    ]
    report_progress(
        stage="validating",
        completed=required_count,
        total=required_count,
        pages_completed=page_count,
        pages_total=page_count,
        message=f"Đã đối chiếu xong {len(product_urls)} URL sản phẩm duy nhất.",
    )
    return {
        "urls": product_urls,
        "products": products,
        "pages_scanned": page_count,
        "credits_used": 0,
        "truncated": advertised_count > max_products,
        "discovery_mode": "search_api",
        "advertised_count": advertised_count,
        "scroll_rounds": 0,
        "recovery_passes": recovery_passes,
        "duplicate_url_count": duplicate_url_count,
        "waf_blocked": False,
        "fallback_product_count": sum(
            1 for product in products if not product.get("images")
        ),
        # Search cards commonly expose one or two images, not the complete
        # detail-page gallery. They are nevertheless sufficient for the fast
        # collection-image CSV mode.
        "full_gallery_available": False,
        "card_diagnostics": {
            "returned": len(products),
            "with_title": len(products),
            "with_price": len(products),
            "with_images": sum(
                1 for product in products if product.get("images")
            ),
            "missing_sample": [
                url for url in product_urls if url not in products_by_url
            ][:10],
        },
    }


def _sitebase_gallery_browser_script(product_urls: list[str]) -> str:
    """Build one same-origin browser action for complete ShopBase galleries."""
    script = r"""
(async () => {
  const urls = __PRODUCT_URLS__;
  const concurrency = __CONCURRENCY__;
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

  function jsonObjectAt(text, start) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let index = start; index < text.length; index += 1) {
      const character = text[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (character === '\\') escaped = true;
        else if (character === '"') inString = false;
        continue;
      }
      if (character === '"') inString = true;
      else if (character === '{') depth += 1;
      else if (character === '}') {
        depth -= 1;
        if (depth === 0) {
          try { return JSON.parse(text.slice(start, index + 1)); }
          catch (_) { return null; }
        }
      }
    }
    return null;
  }

  function hydratedProduct(pageHtml, productUrl) {
    const source = new URL(productUrl);
    const handle = source.pathname.replace(/\/$/, '').split('/').pop();
    if (!handle) return null;
    const needle = '"handle":' + JSON.stringify(handle);
    let searchFrom = 0;
    while (searchFrom < pageHtml.length) {
      const handlePosition = pageHtml.indexOf(needle, searchFrom);
      if (handlePosition < 0) break;
      const markerPosition = pageHtml.lastIndexOf(
        '"product":', handlePosition
      );
      if (markerPosition >= Math.max(0, handlePosition - 120000)) {
        const objectStart = pageHtml.indexOf('{', markerPosition + 10);
        const product = objectStart >= 0 ? jsonObjectAt(pageHtml, objectStart) : null;
        if (product && product.handle === handle) return product;
      }
      searchFrom = handlePosition + needle.length;
    }
    return null;
  }

  function schemaProduct(pageHtml) {
    const documentCopy = new DOMParser().parseFromString(pageHtml, 'text/html');
    for (const node of documentCopy.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const value = JSON.parse(node.textContent || '{}');
        const roots = Array.isArray(value) ? value : [value];
        const candidates = [];
        roots.forEach(root => {
          if (root && typeof root === 'object') {
            candidates.push(root);
            if (Array.isArray(root['@graph'])) candidates.push(...root['@graph']);
          }
        });
        const product = candidates.find(item => {
          const type = item && item['@type'];
          return type === 'Product' || (Array.isArray(type) && type.includes('Product'));
        });
        if (product) return product;
      } catch (_) {}
    }
    return null;
  }

  function cleanImage(rawValue, productUrl) {
    const raw = typeof rawValue === 'string'
      ? rawValue : (rawValue && (rawValue.src || rawValue.url || rawValue.contentUrl));
    if (!raw) return '';
    try {
      const image = new URL(raw, productUrl);
      ['w', 'h', 'width', 'height', 'min_width', 'min_height', 'max_width', 'max_height']
        .forEach(key => image.searchParams.delete(key));
      image.hash = '';
      const value = image.href;
      if (/\.svg(?:$|[?#])/i.test(value)) return '';
      if (/(?:woocommerce[-_]?placeholder|(?:^|[/_.-])placeholder(?:[/_.-]|$)|awaiting[-_]?product[-_]?image)/i.test(value)) return '';
      return value;
    } catch (_) {
      return '';
    }
  }

  function productFromHtml(pageHtml, productUrl) {
    const hydrated = hydratedProduct(pageHtml, productUrl);
    const schema = schemaProduct(pageHtml) || {};
    const schemaImages = (Array.isArray(schema.image) ? schema.image : [schema.image])
      .map(item => cleanImage(item, productUrl)).filter(Boolean);
    let pumpBearSchema = false;
    try {
      pumpBearSchema = new URL(productUrl).hostname.replace(/^www\./i, '').toLowerCase() === 'pumpbear.com'
        && schemaImages.length > 0
        && schemaImages.every(image => {
          const parsed = new URL(image);
          return parsed.hostname.toLowerCase() === 'cdn.pumpbear.com'
            && /^\/products\//i.test(parsed.pathname);
        });
    } catch (_) {}
    if (!hydrated && !pumpBearSchema) return null;
    const variants = Array.isArray(hydrated && hydrated.variants)
      ? hydrated.variants.filter(item => item && typeof item === 'object') : [];
    const selected = variants.find(item => item.available !== false) || variants[0] || {};
    const offers = Array.isArray(schema.offers) ? (schema.offers[0] || {}) : (schema.offers || {});
    const title = String((hydrated && hydrated.title) || schema.name || '').trim();
    const price = selected.price != null
      ? selected.price : (offers.price != null ? offers.price : offers.lowPrice);
    const compareAtPrice = selected.compare_at_price != null
      ? selected.compare_at_price : null;
    const allImages = [];
    ((hydrated && hydrated.images) || schemaImages).forEach(item => {
      const image = cleanImage(item, productUrl);
      if (image && !allImages.includes(image)) allImages.push(image);
    });
    const versioned = allImages.filter(image => {
      try {
        const parsed = new URL(image);
        return parsed.hostname.toLowerCase().endsWith('thesitebase.net')
          && /\/products\/ver_[^/]+\//i.test(parsed.pathname);
      } catch (_) { return false; }
    });
    const images = versioned.length ? versioned : allImages;
    if (!title || price == null || !images.length) return null;
    const canonical = new URL(productUrl);
    canonical.search = '';
    canonical.hash = '';
    return {
      title,
      price,
      compare_at_price: compareAtPrice,
      currency: String(offers.priceCurrency || '').toUpperCase(),
      product_url: canonical.href.replace(/\/$/, ''),
      images
    };
  }

  async function loadProduct(productUrl) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await fetch(productUrl, {
          credentials: 'same-origin',
          headers: {'Accept': 'text/html,application/xhtml+xml'}
        });
        if (response.ok) {
          const product = productFromHtml(await response.text(), productUrl);
          if (product) return product;
        }
      } catch (_) {}
      if (attempt < 2) await sleep(750 * (2 ** attempt));
    }
    return null;
  }

  const products = [];
  const failedUrls = [];
  for (let offset = 0; offset < urls.length; offset += concurrency) {
    const chunk = urls.slice(offset, offset + concurrency);
    const results = await Promise.all(chunk.map(loadProduct));
    results.forEach((product, index) => {
      if (product) products.push(product);
      else failedUrls.push(chunk[index]);
    });
  }
  return {products, failed_urls: failedUrls};
})()
"""
    return (
        script.replace("__PRODUCT_URLS__", json.dumps(product_urls, ensure_ascii=True))
        .replace("__CONCURRENCY__", str(SITEBASE_GALLERY_BROWSER_CONCURRENCY))
    )


def scrape_sitebase_product_galleries_via_browser(
    page_url: str, product_urls: list[str], progress_callback=None
) -> dict:
    """Recover many ShopBase galleries in a few same-origin browser sessions.

    Direct product GETs are still the free first choice. This grouped browser
    fallback is used only for the rate-limited remainder and replaces a costly
    one-Firecrawl-credit-per-product batch.
    """
    _check_key()
    parsed_page = urlparse(page_url)
    domain = parsed_page.hostname or ""
    unique_urls = []
    for raw_url in product_urls:
        canonical = _canonical_product_link(
            raw_url, page_url, domain, "/products/"
        )
        if canonical and canonical not in unique_urls:
            unique_urls.append(canonical)
    if not unique_urls:
        return {
            "products": [],
            "products_by_url": {},
            "failed_urls": [],
            "credits_used": 0,
            "batches": 0,
        }

    products_by_url = {}
    credits_used = 0
    batches = 0

    def report_browser_progress(offset: int, chunk: list[str]) -> None:
        if not progress_callback:
            return
        try:
            progress_callback({
                "stage": "browser_recovery",
                "completed": min(offset + len(chunk), len(unique_urls)),
                "total": len(unique_urls),
                "succeeded": len(products_by_url),
                "batches_completed": batches,
                "batches_total": math.ceil(
                    len(unique_urls) / SITEBASE_GALLERY_BROWSER_BATCH_SIZE
                ),
            })
        except Exception:
            pass

    with httpx.Client(timeout=210) as client:
        for offset in range(0, len(unique_urls), SITEBASE_GALLERY_BROWSER_BATCH_SIZE):
            chunk = unique_urls[offset:offset + SITEBASE_GALLERY_BROWSER_BATCH_SIZE]
            batches += 1
            try:
                response = _post_scrape_with_retry(
                    client,
                    {
                        "url": page_url,
                        "formats": ["links"],
                        "onlyMainContent": False,
                        "maxAge": 0,
                        "timeout": 180_000,
                        "actions": [
                            {"type": "wait", "milliseconds": 3000},
                            {
                                "type": "executeJavascript",
                                "script": _sitebase_gallery_browser_script(chunk),
                            },
                        ],
                    },
                    timeout=210,
                )
            except RuntimeError:
                report_browser_progress(offset, chunk)
                continue
            if response.is_error:
                error_text = str(response.text or "")
                if "credit" in error_text.lower():
                    raise RuntimeError(
                        f"Firecrawl không đủ credit: {error_text[:500]}"
                    )
                report_browser_progress(offset, chunk)
                continue
            data = response.json().get("data") or {}
            metadata = data.get("metadata") or {}
            batch_credits = metadata.get("creditsUsed")
            credits_used += batch_credits if isinstance(batch_credits, int) else 1
            action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
            action_value = None
            for item in reversed(action_returns):
                value = item.get("value") if isinstance(item, dict) else item
                if isinstance(value, dict) and isinstance(value.get("products"), list):
                    action_value = value
                    break
            if action_value is None:
                report_browser_progress(offset, chunk)
                continue
            for product in action_value["products"]:
                if not isinstance(product, dict):
                    continue
                product_url = _canonical_product_link(
                    str(product.get("product_url") or ""),
                    page_url,
                    domain,
                    "/products/",
                )
                images = [
                    image
                    for image in dict.fromkeys(product.get("images") or [])
                    if isinstance(image, str) and image.startswith(("http://", "https://"))
                ]
                if (
                    product_url in chunk
                    and product.get("title")
                    and product.get("price") not in (None, "")
                    and images
                ):
                    products_by_url[product_url] = {
                        **product,
                        "product_url": product_url,
                        "images": images,
                    }
            report_browser_progress(offset, chunk)

    return {
        "products": list(products_by_url.values()),
        "products_by_url": products_by_url,
        "failed_urls": [url for url in unique_urls if url not in products_by_url],
        "credits_used": credits_used,
        "batches": batches,
    }


def _discover_infinite_scroll_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict:
    """Collect a JS infinite-scroll search in one rendered browser session.

    Product URLs are accumulated in a Set on the page after every scroll. This
    also works when the storefront virtualises old cards out of the DOM.
    """
    initial = _post_scrape_with_retry(
        client,
        {
            "url": search_url,
            "formats": ["links", "markdown"],
            "onlyMainContent": False,
            "maxAge": 0,
        },
    )
    if initial.is_error:
        raise RuntimeError(f"Firecrawl {initial.status_code}: {initial.text[:500]}")
    initial_data = initial.json().get("data") or {}
    expected_count = _advertised_product_count(initial_data.get("markdown") or "")
    initial_products = []
    for link in initial_data.get("links") or []:
        product_url = _canonical_product_link(link, search_url, domain, path_hint)
        if product_url and product_url not in initial_products:
            initial_products.append(product_url)

    if not initial_products:
        raise RuntimeError(
            "Trang search không trả về link sản phẩm nào trước khi cuộn."
        )

    target_count = min(expected_count or max_products, max_products)
    estimated_batches = math.ceil(target_count / len(initial_products))
    scroll_rounds = min(
        MAX_INFINITE_SCROLL_ROUNDS,
        max(3, estimated_batches + 2),
    )
    collect_and_scroll = (
        "(() => {"
        "window.__firecrawlProductLinks = window.__firecrawlProductLinks || new Set();"
        "document.querySelectorAll('a[href]').forEach(a => {"
        "try { const u = new URL(a.href, location.href); const p = u.pathname;"
        "const productPath = /\\/products?\\//i.test(p) || /\\/p\\//i.test(p)"
        "|| /\\/(?:dp|gp\\/product)\\//i.test(p) || /\\/(?:itm|ip|item)\\//i.test(p)"
        "|| (/printerval\\.com$/i.test(u.hostname) && /-p\\d+(?:\\.html)?\\/?$/i.test(p));"
        "if (u.hostname === location.hostname && productPath)"
        "window.__firecrawlProductLinks.add(u.href); } catch (_) {} });"
        "window.scrollTo(0, document.body.scrollHeight);"
        "return window.__firecrawlProductLinks.size;"
        "})()"
    )
    collect_and_return = (
        "(() => {"
        "window.__firecrawlProductLinks = window.__firecrawlProductLinks || new Set();"
        "document.querySelectorAll('a[href]').forEach(a => {"
        "try { const u = new URL(a.href, location.href); const p = u.pathname;"
        "const productPath = /\\/products?\\//i.test(p) || /\\/p\\//i.test(p)"
        "|| /\\/(?:dp|gp\\/product)\\//i.test(p) || /\\/(?:itm|ip|item)\\//i.test(p)"
        "|| (/printerval\\.com$/i.test(u.hostname) && /-p\\d+(?:\\.html)?\\/?$/i.test(p));"
        "if (u.hostname === location.hostname && productPath)"
        "window.__firecrawlProductLinks.add(u.href); } catch (_) {} });"
        "return Array.from(window.__firecrawlProductLinks);"
        "})()"
    )
    actions = [{"type": "wait", "milliseconds": 1000}]
    for _ in range(scroll_rounds):
        actions.extend([
            {"type": "executeJavascript", "script": collect_and_scroll},
            {"type": "wait", "milliseconds": 900},
        ])
    actions.extend([
        {"type": "executeJavascript", "script": collect_and_return},
        {"type": "wait", "milliseconds": 500},
    ])

    scrolled = _post_scrape_with_retry(
        client,
        {
            "url": search_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": actions,
        },
        timeout=150,
    )
    if scrolled.is_error:
        raise RuntimeError(f"Firecrawl {scrolled.status_code}: {scrolled.text[:500]}")
    scrolled_data = scrolled.json().get("data") or {}
    candidates = (
        initial_products
        + _product_links_from_action_results(scrolled_data)
        + (scrolled_data.get("links") or [])
    )

    product_urls = []
    for link in candidates:
        product_url = _canonical_product_link(link, search_url, domain, path_hint)
        if product_url and product_url not in product_urls:
            product_urls.append(product_url)
            if len(product_urls) >= max_products:
                break

    required_count = min(expected_count, max_products) if expected_count else None
    if required_count and len(product_urls) < required_count:
        raise RuntimeError(
            f"Trang search công bố {expected_count} sản phẩm nhưng sau "
            f"{scroll_rounds} lượt cuộn chỉ thu được {len(product_urls)} URL duy nhất. "
            "Hệ thống đã dừng để tránh xuất CSV thiếu dữ liệu."
        )

    virtual_pages = math.ceil(len(product_urls) / len(initial_products))
    return {
        "urls": product_urls,
        "pages_scanned": virtual_pages,
        "credits_used": 2,
        "truncated": bool(expected_count and expected_count > max_products),
        "discovery_mode": "infinite_scroll",
        "advertised_count": expected_count,
        "scroll_rounds": scroll_rounds,
    }


def _woocommerce_store_product(item: dict) -> tuple[int, dict] | None:
    """Convert one Woo Store API product into the compact export shape."""
    if not isinstance(item, dict):
        return None
    try:
        product_id = int(item["id"])
    except (KeyError, TypeError, ValueError):
        return None

    prices = item.get("prices") or {}
    try:
        minor_unit = max(0, int(prices.get("currency_minor_unit") or 0))
        divisor = 10 ** minor_unit
        price = int(prices["price"]) / divisor
        regular_price = int(prices.get("regular_price") or prices["price"]) / divisor
    except (KeyError, TypeError, ValueError):
        return None

    image_urls = []
    for image in item.get("images") or []:
        image_url = (
            image.get("src")
            if isinstance(image, dict)
            else str(image or "")
        )
        image_url = str(image_url or "").strip()
        if (
            not image_url
            or re.search(r"\.svg(?:$|[?#])", image_url, re.I)
            or re.search(
                r"(?:woocommerce[-_]?placeholder|"
                r"(?:^|[/_.-])placeholder(?:[/_.-]|$)|"
                r"awaiting[-_]?product[-_]?image)",
                html.unescape(image_url),
                re.I,
            )
            or image_url in image_urls
        ):
            continue
        image_urls.append(image_url)

    product_url = str(item.get("permalink") or "").strip()
    title = html.unescape(str(item.get("name") or "")).strip()
    if not title or not product_url:
        return None
    return product_id, {
        "title": title,
        "price": price,
        "compare_at_price": regular_price if regular_price > price else None,
        "currency": str(prices.get("currency_code") or "").upper(),
        "product_url": product_url,
        "images": image_urls,
    }


def _discover_woocommerce_search_products(
    client: httpx.Client,
    *,
    origin: str,
    search_term: str,
    max_products: int,
    strict: bool,
) -> dict | None:
    """Join WordPress search IDs to Woo Store API full-gallery records."""
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; CollectionCSV/1.0)",
    }
    search_url = f"{origin}/wp-json/wp/v2/search"
    search_params = {
        "search": search_term,
        "subtype": "product",
        "per_page": WOOCOMMERCE_STORE_PAGE_SIZE,
        "page": 1,
        "_fields": "id,title,url,subtype",
    }
    try:
        first_response = client.get(
            search_url,
            params=search_params,
            headers=headers,
            timeout=45,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        if strict:
            raise RuntimeError(f"Không đọc được WordPress product search API: {exc}") from exc
        return None
    if first_response.is_error:
        if strict:
            raise RuntimeError(
                f"WordPress product search API HTTP {first_response.status_code}."
            )
        return None
    try:
        first_items = first_response.json()
        advertised_count = int(
            first_response.headers.get("x-wp-total") or len(first_items)
        )
    except (TypeError, ValueError) as exc:
        if strict:
            raise RuntimeError(
                "WordPress product search API trả dữ liệu không hợp lệ."
            ) from exc
        return None
    if not isinstance(first_items, list) or advertised_count < 1:
        return None

    required_count = min(advertised_count, max_products)
    # A few WooCommerce search customisations ignore ``per_page`` and return
    # the complete result set on every page (Konfox currently does this).  In
    # that case, trusting X-WP-Total to schedule ceil(total / 100) requests
    # downloads the same multi-megabyte payload dozens of times.  Count valid,
    # unique product IDs from the first response and stop paginating when that
    # response already covers the requested result set.
    first_page_ids = set()
    for item in first_items:
        if not isinstance(item, dict):
            continue
        try:
            first_page_ids.add(int(item["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    first_page_is_complete = len(first_page_ids) >= required_count
    page_count = (
        1
        if first_page_is_complete
        else max(
            1,
            math.ceil(required_count / WOOCOMMERCE_STORE_PAGE_SIZE),
        )
    )

    def load_search_page(page: int) -> tuple[int, list[dict]]:
        response = client.get(
            search_url,
            params={**search_params, "page": page},
            headers=headers,
            timeout=45,
            follow_redirects=True,
        )
        if response.is_error:
            raise RuntimeError(
                f"WordPress product search API HTTP {response.status_code} "
                f"ở trang {page}."
            )
        items = response.json()
        if not isinstance(items, list):
            raise RuntimeError(
                f"WordPress product search API trả dữ liệu không hợp lệ ở trang {page}."
            )
        return page, items

    search_pages = [(1, first_items)]
    remaining_pages = list(range(2, page_count + 1))
    try:
        if remaining_pages:
            with ThreadPoolExecutor(
                max_workers=min(WOOCOMMERCE_STORE_WORKERS, len(remaining_pages))
            ) as pool:
                search_pages.extend(pool.map(load_search_page, remaining_pages))
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError):
        if strict:
            raise
        return None
    search_pages.sort(key=lambda value: value[0])

    product_refs: list[tuple[int, str]] = []
    seen_ids = set()
    for _, items in search_pages:
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                product_id = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            product_url = str(item.get("url") or "").strip()
            if not product_url or product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            product_refs.append((product_id, product_url))
            if len(product_refs) >= required_count:
                break
        if len(product_refs) >= required_count:
            break
    if len(product_refs) != required_count:
        if strict:
            raise RuntimeError(
                f"WordPress search công bố {advertised_count} sản phẩm nhưng REST API "
                f"chỉ trả {len(product_refs)} product ID duy nhất."
            )
        return None

    api_url = f"{origin}/wp-json/wc/store/v1/products"
    id_chunks = [
        product_refs[index:index + WOOCOMMERCE_STORE_PAGE_SIZE]
        for index in range(0, len(product_refs), WOOCOMMERCE_STORE_PAGE_SIZE)
    ]

    def load_store_chunk(
        indexed_chunk: tuple[int, list[tuple[int, str]]],
    ) -> tuple[int, list[dict]]:
        index, chunk = indexed_chunk
        request_params = {
            "include": ",".join(str(product_id) for product_id, _ in chunk),
            "per_page": len(chunk),
            "_fields": "id,name,permalink,prices,images",
        }
        response = None
        last_request_error = None
        for attempt in range(WOOCOMMERCE_STORE_MAX_ATTEMPTS):
            try:
                response = client.get(
                    api_url,
                    params=request_params,
                    headers=headers,
                    timeout=45,
                    follow_redirects=True,
                )
            except httpx.HTTPError as exc:
                last_request_error = exc
                if attempt + 1 >= WOOCOMMERCE_STORE_MAX_ATTEMPTS:
                    raise RuntimeError(
                        f"WooCommerce Store API request failed at batch "
                        f"{index + 1}: {exc}"
                    ) from exc
            else:
                if not response.is_error:
                    break
                if (
                    response.status_code not in {429, 500, 502, 503, 504}
                    or attempt + 1 >= WOOCOMMERCE_STORE_MAX_ATTEMPTS
                ):
                    break
                last_request_error = RuntimeError(
                    f"WooCommerce Store API HTTP {response.status_code}"
                )
            time.sleep(WOOCOMMERCE_STORE_RETRY_BASE_SEC * (2**attempt))
        if response is None:
            raise RuntimeError(
                f"WooCommerce Store API did not respond at batch {index + 1}."
            ) from last_request_error
        if response.is_error and response.status_code in {500, 502, 503, 504}:
            # One malformed or exceptionally large product can make an entire
            # include= batch fail.  Bisect the batch so healthy products are
            # retained; a persistently failing singleton is left for the
            # normal product-page fallback instead of aborting the whole scan.
            if len(chunk) > 1:
                midpoint = len(chunk) // 2
                _, left_items = load_store_chunk((index, chunk[:midpoint]))
                _, right_items = load_store_chunk((index, chunk[midpoint:]))
                return index, left_items + right_items
            return index, []
        if response.is_error:
            raise RuntimeError(
                f"WooCommerce Store API HTTP {response.status_code} "
                f"ở batch ID {index + 1}."
            )
        items = response.json()
        if not isinstance(items, list):
            raise RuntimeError(
                f"WooCommerce Store API trả dữ liệu không hợp lệ ở batch ID {index + 1}."
            )
        return index, items

    try:
        with ThreadPoolExecutor(
            max_workers=min(WOOCOMMERCE_STORE_WORKERS, len(id_chunks))
        ) as pool:
            store_chunks = list(pool.map(load_store_chunk, enumerate(id_chunks)))
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError):
        if strict:
            raise
        return None
    store_chunks.sort(key=lambda value: value[0])

    products_by_id = {}
    for _, items in store_chunks:
        for item in items:
            parsed_product = _woocommerce_store_product(item)
            if parsed_product is None:
                continue
            product_id, product = parsed_product
            products_by_id.setdefault(product_id, product)

    product_urls = [url for _, url in product_refs]
    products = []
    missing_count = 0
    for product_id, _ in product_refs:
        product = products_by_id.get(product_id)
        if product and product["images"]:
            products.append(product)
        else:
            missing_count += 1
    if not products:
        return None

    return {
        "urls": product_urls,
        "products": products,
        "pages_scanned": page_count,
        "credits_used": 0,
        "truncated": advertised_count > max_products,
        "discovery_mode": "woocommerce_store_api",
        "advertised_count": advertised_count,
        "fallback_product_count": missing_count,
        "skipped_no_image_count": 0,
        "scroll_rounds": 0,
        "full_gallery_available": missing_count == 0,
        "waf_blocked": False,
        "card_diagnostics": {
            "returned": len(products),
            "with_title": len(products),
            "with_price": len(products),
            "with_images": len(products),
            "skipped_no_image": 0,
            "missing_sample": [
                url
                for product_id, url in product_refs
                if product_id not in products_by_id
                or not products_by_id[product_id]["images"]
            ][:5],
        },
    }


def _discover_woocommerce_collection_products(
    client: httpx.Client,
    collection_url: str,
    *,
    max_products: int,
    strict: bool = False,
) -> dict | None:
    """Fetch products and their complete galleries from Woo Store API."""
    parsed = urlparse(collection_url)
    query = {
        key.lower(): value
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
    }
    path_parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    category_slug = ""
    for marker in ("collections", "product-category"):
        if marker in path_parts:
            index = path_parts.index(marker)
            if index + 1 < len(path_parts):
                category_slug = path_parts[index + 1].strip().lower()
                break
    if not category_slug:
        for key in ("filter_category", "product_cat", "category"):
            value = unquote(str(query.get(key) or "")).strip().lower()
            if value:
                category_slug = value.split(",", 1)[0].strip()
                break
    search_term = unquote(str(query.get("s") or "")).strip()
    post_type = str(query.get("post_type") or "").strip().lower()
    is_shop_page = bool(path_parts and path_parts[-1].lower() == "shop")
    is_product_search = bool(
        search_term and post_type in {"", "product"}
    )
    if not category_slug and not is_shop_page and not is_product_search:
        return None

    origin = urlunparse(parsed._replace(path="", query="", fragment="")).rstrip("/")
    if is_product_search:
        return _discover_woocommerce_search_products(
            client,
            origin=origin,
            search_term=search_term,
            max_products=max_products,
            strict=strict,
        )

    api_base = f"{origin}/wp-json/wc/store/v1"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (compatible; CollectionCSV/1.0)",
    }
    category = None
    if category_slug:
        try:
            category_response = client.get(
                f"{api_base}/products/categories",
                params={
                    "search": category_slug.replace("-", " "),
                    "per_page": WOOCOMMERCE_STORE_PAGE_SIZE,
                    "_fields": "id,name,slug,count",
                },
                headers=headers,
                timeout=30,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            if strict:
                raise RuntimeError(f"KhÃ´ng Ä‘á»c Ä‘Æ°á»£c WooCommerce category API: {exc}") from exc
            return None
        if category_response.is_error:
            if strict:
                raise RuntimeError(
                    f"WooCommerce category API HTTP {category_response.status_code}."
                )
            return None
        try:
            categories = category_response.json()
        except (TypeError, ValueError) as exc:
            if strict:
                raise RuntimeError("WooCommerce category API tráº£ JSON khÃ´ng há»£p lá»‡.") from exc
            return None
        if not isinstance(categories, list):
            if strict:
                raise RuntimeError("WooCommerce category API khÃ´ng tráº£ danh sÃ¡ch category.")
            return None
        category = next(
            (
                item for item in categories
                if isinstance(item, dict)
                and str(item.get("slug") or "").strip().lower() == category_slug
            ),
            None,
        )
        if not category or not isinstance(category.get("id"), int):
            if strict:
                raise RuntimeError(
                    f"WooCommerce khÃ´ng tÃ¬m tháº¥y category slug '{category_slug}'."
                )
            return None

    params = {
        "per_page": WOOCOMMERCE_STORE_PAGE_SIZE,
        "page": 1,
        "orderby": "id",
        "order": "asc",
        "_fields": "id,name,permalink,prices,images",
    }
    if category is not None:
        params["category"] = category["id"]
    try:
        first_response = client.get(
            f"{api_base}/products",
            params=params,
            headers=headers,
            timeout=45,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        if strict:
            raise RuntimeError(f"KhÃ´ng Ä‘á»c Ä‘Æ°á»£c WooCommerce products API: {exc}") from exc
        return None
    if first_response.is_error:
        if strict:
            raise RuntimeError(
                f"WooCommerce products API HTTP {first_response.status_code}."
            )
        return None
    try:
        first_items = first_response.json()
        advertised_count = int(
            first_response.headers.get("x-wp-total")
            or (category or {}).get("count")
            or len(first_items)
        )
    except (TypeError, ValueError) as exc:
        if strict:
            raise RuntimeError("WooCommerce products API tráº£ dá»¯ liá»‡u khÃ´ng há»£p lá»‡.") from exc
        return None
    if not isinstance(first_items, list) or advertised_count < 1:
        return None

    required_count = min(advertised_count, max_products)
    page_count = max(1, math.ceil(required_count / WOOCOMMERCE_STORE_PAGE_SIZE))

    def load_page(page: int) -> tuple[int, list[dict]]:
        page_params = {**params, "page": page}
        response = client.get(
            f"{api_base}/products",
            params=page_params,
            headers=headers,
            timeout=45,
            follow_redirects=True,
        )
        if response.is_error:
            raise RuntimeError(
                f"WooCommerce Store API HTTP {response.status_code} ở trang {page}."
            )
        try:
            items = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"WooCommerce Store API trả JSON không hợp lệ ở trang {page}."
            ) from exc
        if not isinstance(items, list):
            raise RuntimeError(
                f"WooCommerce Store API trả dữ liệu không hợp lệ ở trang {page}."
            )
        return page, items

    page_results = [(1, first_items)]
    remaining_pages = list(range(2, page_count + 1))
    if remaining_pages:
        with ThreadPoolExecutor(
            max_workers=min(WOOCOMMERCE_STORE_WORKERS, len(remaining_pages))
        ) as pool:
            page_results.extend(pool.map(load_page, remaining_pages))
    page_results.sort(key=lambda value: value[0])

    products_by_id = {}
    for _, items in page_results:
        for item in items:
            if not isinstance(item, dict) or item.get("id") in products_by_id:
                continue
            prices = item.get("prices") or {}
            try:
                minor_unit = max(0, int(prices.get("currency_minor_unit") or 0))
                divisor = 10 ** minor_unit
                price = int(prices["price"]) / divisor
                regular_price = int(prices.get("regular_price") or prices["price"]) / divisor
            except (KeyError, TypeError, ValueError):
                continue
            image_urls = []
            for image in item.get("images") or []:
                image_url = (
                    image.get("src")
                    if isinstance(image, dict)
                    else str(image or "")
                )
                image_url = str(image_url or "").strip()
                if (
                    not image_url
                    or re.search(r"\.svg(?:$|[?#])", image_url, re.I)
                    or re.search(
                        r"(?:woocommerce[-_]?placeholder|"
                        r"(?:^|[/_.-])placeholder(?:[/_.-]|$)|"
                        r"awaiting[-_]?product[-_]?image)",
                        html.unescape(image_url),
                        re.I,
                    )
                    or image_url in image_urls
                ):
                    continue
                image_urls.append(image_url)
            product_url = str(item.get("permalink") or "").strip()
            title = html.unescape(str(item.get("name") or "")).strip()
            if not title or not product_url:
                continue
            products_by_id[item["id"]] = {
                "title": title,
                "price": price,
                "compare_at_price": regular_price if regular_price > price else None,
                "currency": str(prices.get("currency_code") or "").upper(),
                "product_url": product_url,
                "images": image_urls,
            }
            if len(products_by_id) >= required_count:
                break
        if len(products_by_id) >= required_count:
            break

    if len(products_by_id) != required_count:
        raise RuntimeError(
            f"WooCommerce công bố {advertised_count} sản phẩm nhưng Store API chỉ "
            f"trả về {len(products_by_id)} sản phẩm đầy đủ. Hệ thống đã dừng để "
            "tránh xuất CSV thiếu dữ liệu."
        )
    api_products = list(products_by_id.values())
    products = [product for product in api_products if product["images"]]
    skipped_no_image_count = len(api_products) - len(products)
    if not products:
        raise RuntimeError(
            "WooCommerce Store API không trả sản phẩm nào có ảnh hợp lệ."
        )
    return {
        "urls": [product["product_url"] for product in products],
        "products": products,
        "pages_scanned": page_count,
        "credits_used": 0,
        "truncated": advertised_count > max_products,
        "discovery_mode": "woocommerce_store_api",
        "advertised_count": advertised_count,
        "fallback_product_count": 0,
        "skipped_no_image_count": skipped_no_image_count,
        "scroll_rounds": 0,
        # Rows whose image field is empty are intentionally omitted. Every
        # returned row therefore already owns its complete Store API gallery
        # and never needs a product-page/Firecrawl fallback.
        "full_gallery_available": True,
        "waf_blocked": False,
        "card_diagnostics": {
            "returned": len(products),
            "with_title": len(products),
            "with_price": len(products),
            "with_images": len(products),
            "skipped_no_image": skipped_no_image_count,
            "missing_sample": [],
        },
    }


_SHOPIFY_STOREFRONT_TOKEN_RE = re.compile(
    r"(?:storefrontAccessToken|storeFrontAccessToken)[\"']?\s*(?::|=)\s*"
    r"[\"']([A-Za-z0-9_-]{16,})[\"']",
    re.IGNORECASE,
)
_SHOPIFY_CHECKOUT_TOKEN_RE = re.compile(
    r"<meta\b(?=[^>]*\bname\s*=\s*([\"'])shopify-checkout-api-token\1)"
    r"(?=[^>]*\bcontent\s*=\s*([\"'])([^\"']+)\2)[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
_SHOPIFY_SEARCH_HREF_RE = re.compile(
    r"<a\b[^>]*?\bhref\s*=\s*([\"'])(.*?)\1",
    re.IGNORECASE | re.DOTALL,
)


def _shopify_storefront_token_from_html(page_text: str) -> str:
    """Return a public Storefront token embedded by the active theme."""
    match = _SHOPIFY_STOREFRONT_TOKEN_RE.search(page_text or "")
    if match:
        return html.unescape(match.group(1)).strip()
    match = _SHOPIFY_CHECKOUT_TOKEN_RE.search(page_text or "")
    return html.unescape(match.group(3)).strip() if match else ""


def _shopify_money_amount(value) -> float | None:
    try:
        amount = float(str(value))
    except (TypeError, ValueError):
        return None
    return amount if math.isfinite(amount) else None


def _discover_shopify_search_via_storefront_graphql(
    client: httpx.Client,
    search_url: str,
    page_text: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
    html_advertised_count: int | None = None,
) -> dict | None:
    """Use the theme's public Storefront token for authoritative search data.

    Some themes advertise the raw search-index count but render only a subset
    of each page. Storefront GraphQL applies Shopify's actual product search
    and returns each product's complete gallery, so it both avoids the broken
    HTML pagination and removes the later per-product crawl.
    """
    token = _shopify_storefront_token_from_html(page_text)
    query_value = next((
        value.strip()
        for key, value in parse_qsl(
            urlparse(search_url).query,
            keep_blank_values=True,
        )
        if key.lower() == "q" and value.strip()
    ), "")
    if not token or not query_value or max_products < 1:
        return None

    parsed = urlparse(search_url)
    endpoint = urlunparse(parsed._replace(
        path=f"/api/{SHOPIFY_STOREFRONT_API_VERSION}/graphql.json",
        query="",
        fragment="",
    ))
    graphql_query = """
query SearchProducts($query: String!, $first: Int!, $after: String) {
  products(query: $query, first: $first, after: $after, sortKey: RELEVANCE) {
    nodes {
      id
      handle
      title
      onlineStoreUrl
      priceRange { minVariantPrice { amount currencyCode } }
      compareAtPriceRange { minVariantPrice { amount currencyCode } }
      images(first: 250) { nodes { url } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Shopify-Storefront-Access-Token": token,
    }
    raw_products_by_key = {}
    after = None
    pages_scanned = 0
    has_next_page = True

    while has_next_page and len(raw_products_by_key) < max_products:
        first = min(
            SHOPIFY_STOREFRONT_PAGE_SIZE,
            max_products - len(raw_products_by_key),
        )
        payload = {
            "query": graphql_query,
            "variables": {
                "query": query_value,
                "first": first,
                "after": after,
            },
        }
        body = None
        for attempt in range(SHOPIFY_SEARCH_MAX_ATTEMPTS):
            try:
                response = client.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=60,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                if attempt + 1 >= SHOPIFY_SEARCH_MAX_ATTEMPTS:
                    return None
                time.sleep(min(
                    15.0,
                    SHOPIFY_SEARCH_RETRY_BASE_SEC * (2 ** attempt),
                ))
                continue

            status_code = getattr(response, "status_code", 0)
            if status_code == 429 or status_code in {500, 502, 503, 504}:
                if attempt + 1 >= SHOPIFY_SEARCH_MAX_ATTEMPTS:
                    return None
                retry_after = str(response.headers.get("Retry-After") or "").strip()
                try:
                    retry_delay = float(retry_after)
                except (TypeError, ValueError):
                    retry_delay = SHOPIFY_SEARCH_RETRY_BASE_SEC * (2 ** attempt)
                time.sleep(min(15.0, max(0.5, retry_delay)))
                continue
            if response.is_error:
                return None
            try:
                body = response.json()
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
            break

        if not isinstance(body, dict) or body.get("errors"):
            return None
        connection = ((body.get("data") or {}).get("products")) or {}
        nodes = connection.get("nodes") or []
        if not isinstance(nodes, list):
            return None
        pages_scanned += 1
        added = 0
        for node in nodes:
            if not isinstance(node, dict):
                continue
            key = str(node.get("id") or node.get("handle") or "").strip()
            if not key or key in raw_products_by_key:
                continue
            raw_products_by_key[key] = node
            added += 1

        page_info = connection.get("pageInfo") or {}
        has_next_page = bool(page_info.get("hasNextPage"))
        next_cursor = str(page_info.get("endCursor") or "").strip()
        if has_next_page and (not next_cursor or not added or next_cursor == after):
            return None
        after = next_cursor or None

    if not raw_products_by_key:
        return None

    products = []
    skipped_no_image_count = 0
    for node in raw_products_by_key.values():
        handle = str(node.get("handle") or "").strip()
        product_url = _canonical_product_link(
            str(node.get("onlineStoreUrl") or f"/products/{handle}"),
            search_url,
            domain,
            path_hint,
        )
        title = html.unescape(str(node.get("title") or "")).strip()
        price_money = ((node.get("priceRange") or {}).get("minVariantPrice")) or {}
        compare_money = (
            (node.get("compareAtPriceRange") or {}).get("minVariantPrice")
        ) or {}
        price = _shopify_money_amount(price_money.get("amount"))
        compare_at_price = _shopify_money_amount(compare_money.get("amount"))
        if (
            compare_at_price is None
            or price is None
            or compare_at_price <= price
        ):
            compare_at_price = None
        images = []
        for image_node in ((node.get("images") or {}).get("nodes") or []):
            image_url = str(
                image_node.get("url") if isinstance(image_node, dict) else ""
            ).strip()
            if (
                image_url
                and urlparse(image_url).scheme in ("http", "https")
                and not re.search(r"\.svg(?:$|[?#])", image_url, re.I)
                and image_url not in images
            ):
                images.append(image_url)
        if not images:
            skipped_no_image_count += 1
            continue
        if not product_url or not title or price is None:
            return None
        products.append({
            "title": title,
            "price": price,
            "compare_at_price": compare_at_price,
            "currency": str(price_money.get("currencyCode") or "").upper(),
            "product_url": product_url,
            "images": images,
        })

    if not products:
        return None
    truncated = has_next_page and len(raw_products_by_key) >= max_products
    api_result_count = len(raw_products_by_key)
    advertised_count = (
        max(int(html_advertised_count or 0), api_result_count + 1)
        if truncated else api_result_count
    )
    return {
        "urls": [product["product_url"] for product in products],
        "products": products,
        "pages_scanned": pages_scanned,
        "credits_used": 0,
        "truncated": truncated,
        "discovery_mode": "shopify_storefront_graphql",
        "advertised_count": advertised_count,
        "html_advertised_count": html_advertised_count,
        "fallback_product_count": 0,
        "skipped_no_image_count": skipped_no_image_count,
        "scroll_rounds": 0,
        "full_gallery_available": True,
        "waf_blocked": False,
        "card_diagnostics": {
            "returned": len(products),
            "with_title": len(products),
            "with_price": len(products),
            "with_images": len(products),
            "skipped_no_image": skipped_no_image_count,
            "missing_sample": [],
        },
    }


def _shopify_search_page_url(search_url: str, page: int) -> str:
    parsed = urlparse(search_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in COLLECTION_PAGINATION_KEYS
    ]
    if not any(key.lower() == "type" for key, _ in query):
        query.append(("type", "product"))
    query.append(("page", str(page)))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _shopify_search_links_from_html(
    page_text: str,
    page_url: str,
    *,
    domain: str,
    path_hint: str,
) -> list[str]:
    hrefs = [
        html.unescape(match[1])
        for match in _SHOPIFY_SEARCH_HREF_RE.findall(page_text)
    ]
    tracked = []
    for href in hrefs:
        query = {
            key.lower(): value.lower()
            for key, value in parse_qsl(urlparse(urljoin(page_url, href)).query)
        }
        if query.get("_ss") == "r" or "_pos" in query:
            tracked.append(href)
    candidates = tracked or hrefs
    product_urls = []
    for href in candidates:
        product_url = _canonical_product_link(href, page_url, domain, path_hint)
        if product_url and product_url not in product_urls:
            product_urls.append(product_url)
    return product_urls


def _discover_shopify_search_product_links(
    client: httpx.Client,
    search_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
    strict: bool = False,
) -> dict | None:
    """Read a server-rendered Shopify search through its real HTML pages."""
    parsed = urlparse(search_url)
    query = {
        key.lower(): value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    }
    if (
        parsed.path.rstrip("/").lower() != "/search"
        or not str(query.get("q") or "").strip()
    ):
        return None

    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
        ),
    }

    def load_page(page: int) -> tuple[int, list[str], int | None, str]:
        page_url = _shopify_search_page_url(search_url, page)
        response = None
        for attempt in range(SHOPIFY_SEARCH_MAX_ATTEMPTS):
            try:
                response = client.get(
                    page_url,
                    headers=headers,
                    timeout=45,
                    follow_redirects=True,
                )
            except httpx.HTTPError:
                if attempt + 1 >= SHOPIFY_SEARCH_MAX_ATTEMPTS:
                    raise
                time.sleep(min(
                    15.0,
                    SHOPIFY_SEARCH_RETRY_BASE_SEC * (2 ** attempt),
                ))
                continue

            status_code = getattr(response, "status_code", 0)
            retryable = status_code == 429 or status_code in {500, 502, 503, 504}
            if not retryable or attempt + 1 >= SHOPIFY_SEARCH_MAX_ATTEMPTS:
                break
            retry_after = str(response.headers.get("Retry-After") or "").strip()
            try:
                retry_delay = float(retry_after)
            except (TypeError, ValueError):
                retry_delay = SHOPIFY_SEARCH_RETRY_BASE_SEC * (2 ** attempt)
            time.sleep(min(15.0, max(0.5, retry_delay)))

        if response is None:
            raise RuntimeError(f"Không tải được Shopify search ở trang {page}.")
        if response.is_error:
            raise RuntimeError(
                f"Shopify search HTTP {response.status_code} ở trang {page}."
            )
        page_text = str(response.text or "")
        lower_text = page_text.lower()
        if not any(marker in lower_text for marker in (
            "cdn.shopify.com",
            "shopify-section",
            "window.shopify",
            "powered by shopify",
        )):
            raise RuntimeError("Trang search không có dấu hiệu storefront Shopify.")
        plain_text = html.unescape(re.sub(r"<[^>]+>", " ", page_text))
        advertised_count = _advertised_product_count(plain_text)
        product_urls = _shopify_search_links_from_html(
            page_text,
            page_url,
            domain=domain,
            path_hint=path_hint,
        )
        return page, product_urls, advertised_count, page_text

    all_product_urls = []
    seen_product_urls = set()
    max_advertised_count = 0
    highest_nonempty_page = 0
    last_error = None
    for recovery_pass in range(2):
        try:
            first_page = load_page(1)
            first_urls = first_page[1]
            first_advertised_count = first_page[2]
            storefront_result = _discover_shopify_search_via_storefront_graphql(
                client,
                search_url,
                first_page[3],
                domain=domain,
                path_hint=path_hint,
                max_products=max_products,
                html_advertised_count=first_advertised_count,
            )
            if storefront_result is not None:
                storefront_result["recovery_passes"] = recovery_pass
                return storefront_result
            if (
                not first_urls
                or not isinstance(first_advertised_count, int)
                or first_advertised_count < 1
            ):
                raise RuntimeError(
                    "Không đọc được tổng số hoặc link sản phẩm Shopify search."
                )
            page_size = len(first_urls)
            page_results = [first_page]
            max_advertised_count = max(
                max_advertised_count,
                first_advertised_count,
            )
            highest_nonempty_page = max(highest_nonempty_page, 1)
            for product_url in first_urls:
                if product_url not in seen_product_urls:
                    seen_product_urls.add(product_url)
                    all_product_urls.append(product_url)

            target_page = min(
                MAX_COLLECTION_PAGES,
                max(
                    2,
                    math.ceil(
                        min(max_advertised_count, max_products) / page_size
                    ) + 1,
                ),
            )
            next_page = 2
            saw_empty_page = False
            while next_page <= target_page:
                batch_end = min(
                    target_page,
                    next_page + SHOPIFY_SEARCH_WORKERS - 1,
                )
                page_numbers = list(range(next_page, batch_end + 1))
                worker_count = min(SHOPIFY_SEARCH_WORKERS, len(page_numbers))
                with ThreadPoolExecutor(max_workers=worker_count) as pool:
                    batch_results = list(pool.map(load_page, page_numbers))
                for (
                    page_number,
                    page_urls,
                    page_advertised_count,
                    _page_text,
                ) in sorted(batch_results):
                    if isinstance(page_advertised_count, int):
                        max_advertised_count = max(
                            max_advertised_count,
                            page_advertised_count,
                        )
                    if not page_urls:
                        saw_empty_page = True
                        break
                    highest_nonempty_page = max(
                        highest_nonempty_page,
                        page_number,
                    )
                    for product_url in page_urls:
                        if product_url not in seen_product_urls:
                            seen_product_urls.add(product_url)
                            all_product_urls.append(product_url)
                if saw_empty_page:
                    break
                if (
                    max_advertised_count > max_products
                    and len(all_product_urls) >= max_products
                ):
                    break

                advertised_sentinel = math.ceil(
                    min(max_advertised_count, max_products) / page_size
                ) + 1
                if advertised_sentinel > target_page:
                    target_page = min(
                        MAX_COLLECTION_PAGES,
                        advertised_sentinel,
                    )
                elif batch_end >= target_page:
                    target_page = min(
                        MAX_COLLECTION_PAGES,
                        target_page + SHOPIFY_SEARCH_WORKERS,
                    )
                next_page = batch_end + 1

            required_count = min(max_advertised_count, max_products)
            if (
                len(all_product_urls) >= required_count
                and (
                    saw_empty_page
                    or max_advertised_count > max_products
                )
            ):
                return {
                    "urls": all_product_urls[:required_count],
                    "pages_scanned": highest_nonempty_page,
                    "credits_used": 0,
                    "truncated": max_advertised_count > max_products,
                    "discovery_mode": "shopify_search_html",
                    "advertised_count": max_advertised_count,
                    "scroll_rounds": 0,
                    "recovery_passes": recovery_pass,
                    "waf_blocked": False,
                }
            last_error = RuntimeError(
                f"Shopify search công bố {max_advertised_count} sản phẩm nhưng chỉ "
                f"đọc được {len(all_product_urls)} URL duy nhất qua "
                f"{highest_nonempty_page} trang."
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            last_error = exc
        if recovery_pass == 0:
            continue

    if strict:
        raise RuntimeError(str(last_error or "Không đọc được Shopify search."))
    return None


def _discover_shopify_collection_via_browser(
    client: httpx.Client,
    collection_url: str,
    *,
    domain: str,
    path_hint: str,
    max_products: int,
) -> dict | None:
    """Read Shopify collection JSON inside Firecrawl's browser session.

    Some Shopify storefronts protect direct HTTP with a WAF and initially
    render only twelve cards plus a Load more button. The same-origin browser
    session can still read /collections/<handle>/products.json in batches.
    """
    parsed = urlparse(collection_url)
    if "/collections/" not in parsed.path.lower():
        return None

    script = f"""
(async () => {{
  const maxProducts = {int(max_products)};
  const pageSize = {SHOPIFY_COLLECTION_PAGE_SIZE};
  const countPatterns = [
    /showing\\s+[\\d,]+\\s+of\\s+([\\d,]+)\\s+(?:results?|products?|items?)/i,
    /([\\d,]+)\\s+(?:results?|products?|items?)/i
  ];
  const pageText = document.body && document.body.innerText || '';
  let advertisedCount = 0;
  for (const pattern of countPatterns) {{
    const match = pageText.match(pattern);
    if (match) {{
      advertisedCount = Number(match[1].replace(/,/g, ''));
      if (advertisedCount) break;
    }}
  }}
  const required = Math.min(advertisedCount || maxProducts, maxProducts);
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  function productCard(anchor) {{
    let node = anchor.parentElement;
    let best = node || anchor;
    for (let depth = 0; node && depth < 12; depth += 1, node = node.parentElement) {{
      const links = Array.from(node.querySelectorAll(
        'a[href*="/products/"], a[href*="/product/"]'
      ));
      const handles = new Set(links.map(link => {{
        try {{
          const path = new URL(link.href, location.href).pathname;
          const match = path.match(/\\/products?\\/([^/]+)/i);
          return match ? match[1].toLowerCase() : '';
        }} catch (_) {{ return ''; }}
      }}).filter(Boolean));
      if (handles.size > 1) break;
      best = node;
      if (
        node.querySelector('img')
        && /(US\\$|CA\\$|AU\\$|\\$|€|£|¥|₫)\\s*[\\d,.]+/.test(node.innerText || '')
      ) return node;
    }}
    return best;
  }}
  function cardImage(card) {{
    for (const img of card.querySelectorAll('img')) {{
      const raw = img.getAttribute('data-src') || img.getAttribute('data-original')
        || img.getAttribute('data-lazy-src') || img.getAttribute('src')
        || img.currentSrc || '';
      const semantic = [
        img.getAttribute('class'), img.getAttribute('aria-label'),
        img.parentElement && img.parentElement.getAttribute('class'),
        img.parentElement && img.parentElement.getAttribute('aria-label')
      ].filter(Boolean).join(' ');
      if (
        !raw || /\\.svg(?:$|[?#])/i.test(raw)
        || /(wishlist|favorite|favourite|heart|avatar|logo|icon|rating|badge)/i.test(semantic)
      ) continue;
      try {{ return new URL(raw, location.href).href; }} catch (_) {{}}
    }}
    return '';
  }}
  function cardMoney(card) {{
    const matches = Array.from((card.innerText || '').matchAll(
      /(US\\$|CA\\$|AU\\$|\\$|€|£|¥|₫)\\s*([\\d,.]+)/g
    ));
    const values = matches.map(match => Number(
      match[2].replace(/,(?=\\d{{3}}(?:\\D|$))/g, '').replace(',', '.')
    )).filter(Number.isFinite);
    const symbols = {{'$': 'USD', 'US$': 'USD', 'CA$': 'CAD', 'AU$': 'AUD',
      '€': 'EUR', '£': 'GBP', '¥': 'JPY', '₫': 'VND'}};
    const price = values.length ? values[0] : null;
    return {{
      price,
      compare_at_price: values.slice(1).find(value => price != null && value > price) || null,
      currency: symbols[matches[0] && matches[0][1] || ''] || ''
    }};
  }}
  function collectCards(found) {{
    for (const anchor of document.querySelectorAll(
      'a[href*="/products/"], a[href*="/product/"]'
    )) {{
      try {{
        const url = new URL(anchor.href, location.href);
        const handleMatch = url.pathname.match(/\\/(products?)\\/([^/]+)/i);
        if (url.hostname !== location.hostname || !handleMatch) {{
          continue;
        }}
        const productUrl = new URL(
          '/' + handleMatch[1].toLowerCase() + '/' + handleMatch[2],
          location.origin
        );
        if (found.has(productUrl.href)) continue;
        const card = productCard(anchor);
        const money = cardMoney(card);
        const image = cardImage(card);
        const cardHeading = card.querySelector('h2, h3, h4, [class*="title"], [class*="name"]');
        const anchorImage = anchor.querySelector('img');
        const candidates = [
          anchor.getAttribute('title'), (anchor.textContent || '').trim(),
          anchorImage && anchorImage.getAttribute('alt'),
          cardHeading && cardHeading.textContent
        ].map(value => (value || '').trim()).filter(value => value.length > 3);
        const title = candidates.sort((a, b) => b.length - a.length)[0] || '';
        if (title && money.price != null && image) {{
          found.set(productUrl.href, {{
            title,
            price: money.price,
            compare_at_price: money.compare_at_price,
            currency: money.currency,
            product_url: productUrl.href,
            images: [image]
          }});
        }}
      }} catch (_) {{}}
    }}
  }}
  async function loadMoreCards() {{
    const found = new Map();
    collectCards(found);
    const debugSample = Array.from(
      document.querySelectorAll('a[href*="/products/"], a[href*="/product/"]')
    ).slice(0, 3).map(anchor => {{
      const card = productCard(anchor);
      const money = cardMoney(card);
      return {{
        href: anchor.href,
        anchor_text: (anchor.textContent || '').trim().slice(0, 120),
        card_text: (card.innerText || '').trim().slice(0, 240),
        image: cardImage(card),
        price: money.price,
        image_count: card.querySelectorAll('img').length
      }};
    }});
    let rounds = 0;
    while (found.size < required && rounds < 100) {{
      const button = Array.from(document.querySelectorAll('button, a')).find(node =>
        /^(load|show|view)\\s+more$/i.test((node.textContent || '').trim())
        && !node.disabled && node.getAttribute('aria-disabled') !== 'true'
      );
      if (!button) break;
      const before = found.size;
      const beforeLinkCount = document.querySelectorAll(
        'a[href*="/products/"], a[href*="/product/"]'
      ).length;
      button.scrollIntoView({{block: 'center'}});
      button.click();
      let linkCountGrew = false;
      for (let attempt = 0; attempt < 50; attempt += 1) {{
        await sleep(100);
        const currentLinkCount = document.querySelectorAll(
          'a[href*="/products/"], a[href*="/product/"]'
        ).length;
        if (currentLinkCount > beforeLinkCount) {{
          linkCountGrew = true;
          break;
        }}
      }}
      if (linkCountGrew) collectCards(found);
      if (found.size <= before) break;
      rounds += 1;
    }}
    return {{
      supported: found.size > 0,
      source: 'load_more',
      advertised_count: advertisedCount || found.size,
      pages_scanned: rounds + 1,
      products: Array.from(found.values()).slice(0, required),
      debug_sample: found.size ? [] : debugSample
    }};
  }}
  const endpoint = location.pathname.replace(/\\/$/, '') + '/products.json';
  // A Shopify theme/proxy may silently cap this endpoint to 12/24/50 rows
  // even when limit=250 was requested. Deduplicate by stable product identity
  // and keep paging until the advertised total is reached. Never use
  // `batch.length < pageSize` as an end-of-list signal.
  const productsByKey = new Map();
  let pagesScanned = 0;
  for (let page = 1; productsByKey.size < required && page <= 500; page += 1) {{
    const url = new URL(endpoint, location.origin);
    url.searchParams.set('limit', String(pageSize));
    url.searchParams.set('page', String(page));
    const response = await fetch(url.href, {{
      credentials: 'include',
      headers: {{'Accept': 'application/json'}}
    }});
    if (!response.ok) return await loadMoreCards();
    const body = await response.json();
    const batch = Array.isArray(body && body.products) ? body.products : [];
    pagesScanned += 1;
    if (!batch.length) break;
    let added = 0;
    for (const product of batch) {{
      if (!product || !product.handle) continue;
      const key = String(product.id || product.handle);
      if (productsByKey.has(key)) continue;
      productsByKey.set(key, product);
      added += 1;
    }}
    // Protect against storefront proxies that ignore the page parameter and
    // return the same first batch forever.
    if (!added) break;
  }}
  const products = Array.from(productsByKey.values());
  const currency = window.Shopify && window.Shopify.currency
    && window.Shopify.currency.active || '';
  return {{
    supported: products.length > 0,
    source: 'shopify_api',
    advertised_count: advertisedCount || products.length,
    pages_scanned: pagesScanned,
    products: products.slice(0, required).map(product => {{
      const variants = Array.isArray(product.variants) ? product.variants : [];
      const selected = variants.find(variant => variant && variant.available !== false)
        || variants[0] || {{}};
      const images = (Array.isArray(product.images) ? product.images : [])
        .map(item => typeof item === 'string' ? item : item && item.src)
        .filter(Boolean);
      if (!images.length && product.image) {{
        images.push(product.image.src || product.image);
      }}
      return {{
        title: product.title || '',
        price: selected.price == null ? null : Number(selected.price),
        compare_at_price: selected.compare_at_price == null
          ? null : Number(selected.compare_at_price),
        currency,
        product_url: new URL('/products/' + product.handle, location.origin).href,
        images: Array.from(new Set(images.map(image => {{
          try {{ return new URL(image, location.href).href; }}
          catch (_) {{ return ''; }}
        }}).filter(Boolean)))
      }};
    }})
  }};
}})()
"""
    response = _post_scrape_with_retry(
        client,
        {
            "url": collection_url,
            "formats": ["links"],
            "onlyMainContent": False,
            "maxAge": 0,
            "timeout": 120_000,
            "actions": [
                {"type": "wait", "milliseconds": 4000},
                {"type": "executeJavascript", "script": script},
            ],
        },
        timeout=150,
    )
    if response.is_error:
        return None
    data = response.json().get("data") or {}
    action_returns = ((data.get("actions") or {}).get("javascriptReturns")) or []
    action_value = None
    for item in reversed(action_returns):
        value = item.get("value") if isinstance(item, dict) else item
        if isinstance(value, dict) and "supported" in value:
            action_value = value
            break
    if action_value is None:
        return None
    if not action_value.get("supported"):
        return None

    advertised_count = action_value.get("advertised_count")
    pages_scanned = action_value.get("pages_scanned")
    if not isinstance(advertised_count, int) or advertised_count < 1:
        return None
    if not isinstance(pages_scanned, int) or pages_scanned < 1:
        return None
    required_count = min(advertised_count, max_products)
    products_by_url = {}
    for raw_product in action_value.get("products") or []:
        if not isinstance(raw_product, dict):
            continue
        product_url = _canonical_product_link(
            str(raw_product.get("product_url") or ""),
            collection_url,
            domain,
            path_hint,
        )
        title = html.unescape(str(raw_product.get("title") or "")).strip()
        price = raw_product.get("price")
        images = []
        for image in raw_product.get("images") or []:
            if not isinstance(image, str) or not image.strip():
                continue
            absolute = urljoin(collection_url, image.strip())
            if (
                urlparse(absolute).scheme in ("http", "https")
                and not re.search(r"\\.svg(?:$|[?#])", absolute, re.I)
                and absolute not in images
            ):
                images.append(absolute)
        if not product_url or not title or price in (None, "") or not images:
            continue
        products_by_url.setdefault(product_url, {
            "title": title,
            "price": price,
            "compare_at_price": raw_product.get("compare_at_price"),
            "currency": str(raw_product.get("currency") or "").upper(),
            "product_url": product_url,
            "images": images,
        })

    if len(products_by_url) != required_count:
        raise RuntimeError(
            f"Collection công bố {advertised_count} sản phẩm nhưng Shopify API trong "
            f"trình duyệt chỉ trả về {len(products_by_url)} sản phẩm đầy đủ. Hệ thống "
            "đã dừng để tránh xuất CSV thiếu dữ liệu."
        )
    metadata = data.get("metadata") or {}
    credits_used = metadata.get("creditsUsed")
    if not isinstance(credits_used, int):
        credits_used = 1
    return {
        "urls": list(products_by_url),
        "products": list(products_by_url.values()),
        "pages_scanned": pages_scanned,
        "credits_used": credits_used,
        "truncated": advertised_count > max_products,
        "discovery_mode": (
            "collection_load_more"
            if action_value.get("source") == "load_more"
            else "shopify_collection_api"
        ),
        "advertised_count": advertised_count,
        "scroll_rounds": (
            max(0, pages_scanned - 1)
            if action_value.get("source") == "load_more"
            else 0
        ),
        # Only Shopify's own collection products.json payload is authoritative
        # for a complete gallery.  The load-more DOM fallback intentionally
        # contains card images only and must not be advertised as full-gallery.
        "full_gallery_available": action_value.get("source") == "shopify_api",
        "waf_blocked": False,
        "card_diagnostics": {
            "returned": len(products_by_url),
            "with_title": len(products_by_url),
            "with_price": len(products_by_url),
            "with_images": len(products_by_url),
            "missing_sample": [],
        },
    }


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
        links = _scrape_links(client, page_url)

    domain = urlparse(page_url).netloc
    seen, product_links = set(), []
    for link in links:
        if urlparse(link).netloc != domain or path_hint not in link:
            continue
        if link not in seen:
            seen.add(link)
            product_links.append(link)
    return product_links


def _collection_page_url(collection_url: str, page_number: int) -> str:
    """Build Shopify-style collection pagination while preserving filters."""
    parsed = urlparse(collection_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in COLLECTION_PAGINATION_KEYS
    ]
    if page_number > 1:
        query.append(("page", str(page_number)))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _search_start_url(search_url: str) -> str:
    """Reset search pagination while retaining an explicit first page."""
    parsed = urlparse(search_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in COLLECTION_PAGINATION_KEYS
    ]
    query.append(("page", "1"))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _stable_collection_url(collection_url: str) -> str:
    """Replace a dynamic recommended order with a repeatable collection sort.

    SHOPLINE's default ``recommend`` order can change between two independent
    page requests.  A product can therefore move from page 2 to page 3 while
    we scan, producing a duplicate URL and leaving another product unseen.
    ``shelved-descending`` is SHOPLINE's public "New arrivals" value and is
    ignored harmlessly by storefronts that do not support it.  A caller's
    explicit non-recommended sort is preserved.
    """
    parsed = urlparse(collection_url)
    if _is_search_results_url(collection_url):
        # Search relevance is kept stable by loading every result in one live
        # browser session, so do not inject a collection-only sort value.
        return urlunparse(parsed._replace(fragment=""))
    if "/collections/" not in parsed.path.lower():
        # ``sort_by=shelved-descending`` is a SHOPLINE collection option, not
        # a universal marketplace parameter. Do not inject it into Amazon,
        # WooCommerce, eBay or other catalogue/search URLs.
        return urlunparse(parsed._replace(fragment=""))
    query = parse_qsl(parsed.query, keep_blank_values=True)
    current_sort = next(
        (value.strip().lower() for key, value in query if key.lower() == "sort_by"),
        "",
    )
    if current_sort not in {"", "recommend", "recommended"}:
        return urlunparse(parsed._replace(fragment=""))

    query = [(key, value) for key, value in query if key.lower() != "sort_by"]
    query.append(("sort_by", STABLE_COLLECTION_SORT))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _canonical_product_link(link: str, page_url: str, domain: str, path_hint: str) -> str:
    absolute = urljoin(page_url, link)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return ""
    link_domain = (parsed.hostname or "").lower().removeprefix("www.")
    collection_domain = domain.lower().removeprefix("www.")
    if link_domain != collection_domain:
        return ""

    path = parsed.path
    lower_path = path.lower()
    if link_domain == "etsy.com":
        etsy_url, _ = _canonical_etsy_listing_link(absolute, page_url)
        return etsy_url

    if link_domain == "printerval.com" or link_domain.endswith(".printerval.com"):
        # Printerval does not use /product/. Its stable product identity is the
        # numeric suffix in /<slug>-p123 or /<locale>/<slug>-p123.
        match = re.search(r"-p(\d+)(?:\.html)?/?$", path, re.IGNORECASE)
        if not match:
            return ""
        return urlunparse(parsed._replace(query="", fragment=""))

    if re.search(r"(?:^|\.)amazon\.[a-z.]+$", link_domain):
        match = re.search(r"/(?:[^/]+/)?(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)", path, re.I)
        if not match:
            return ""
        return urlunparse(parsed._replace(path=f"/dp/{match.group(1).upper()}", query="", fragment=""))

    if re.search(r"(?:^|\.)ebay\.[a-z.]+$", link_domain):
        match = re.search(r"/itm/(?:[^/]+/)?(\d{9,15})(?:[/?]|$)", path, re.I)
        if not match:
            return ""
        return urlunparse(parsed._replace(path=f"/itm/{match.group(1)}", query="", fragment=""))

    if re.search(r"(?:^|\.)walmart\.[a-z.]+$", link_domain):
        match = re.search(r"/ip/(?:[^/]+/)?(\d+)(?:[/?]|$)", path, re.I)
        if not match:
            return ""
        return urlunparse(parsed._replace(path=f"/ip/{match.group(1)}", query="", fragment=""))

    if "aliexpress." in link_domain:
        match = re.search(r"/item/(\d+)\.html", path, re.I)
        if not match:
            return ""
        return urlunparse(parsed._replace(path=f"/item/{match.group(1)}.html", query="", fragment=""))

    generic_product_path = any(
        marker in lower_path
        for marker in {path_hint.lower(), "/product/", "/products/", "/p/"}
        if marker
    )
    if not generic_product_path:
        return ""
    # A collection can link to the same product with several ?variant= IDs.
    # The export needs one row per product, so queries/fragments are removed.
    return urlunparse(parsed._replace(query="", fragment=""))


def _canonical_etsy_listing_link(link: str, page_url: str) -> tuple[str, str]:
    """Return Etsy's stable listing URL and ID, without tracking params."""
    absolute = urljoin(page_url, link)
    parsed = urlparse(absolute)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    match = re.match(r"^/listing/(\d+)(?:/[^/?#]+)?/?$", parsed.path)
    if parsed.scheme not in ("http", "https") or hostname != "etsy.com" or not match:
        return "", ""
    path = parsed.path.rstrip("/")
    return urlunparse(parsed._replace(netloc="www.etsy.com", path=path, query="", fragment="")), match.group(1)


def _etsy_shop_page_url(shop_url: str, page_number: int) -> str:
    """Keep only Etsy's real collection/search filter and requested page."""
    parsed = urlparse(shop_url)
    supplied = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query = []
    if supplied.get("section_id"):
        query.append(("section_id", supplied["section_id"]))
    elif supplied.get("search_query"):
        query.append(("search_query", supplied["search_query"]))
    if page_number > 1:
        query.append(("page", str(page_number)))
    return urlunparse(parsed._replace(
        netloc="www.etsy.com",
        query=urlencode(query),
        fragment="",
    ))


def discover_etsy_listing_links(
    source_url: str,
    *,
    max_pages: int = MAX_COLLECTION_PAGES,
    max_products: int = MAX_COLLECTION_PRODUCTS,
) -> dict:
    """Discover one Etsy listing, a shop section, or a shop search.

    Etsy shop pages also contain links from reviews and recommendations. The
    ``ref`` value identifies the actual active collection/search grid, so only
    those links are accepted before canonicalising by listing ID.
    """
    _check_key()
    parsed = urlparse(source_url)
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if parsed.scheme not in ("http", "https") or hostname != "etsy.com":
        raise RuntimeError("URL phải thuộc etsy.com.")

    direct_url, direct_id = _canonical_etsy_listing_link(source_url, source_url)
    if direct_id:
        return {
            "urls": [direct_url],
            "pages_scanned": 0,
            "credits_used": 0,
            "truncated": False,
            "discovery_mode": "etsy_listing",
        }

    if not re.match(r"^/shop/[^/]+/?$", parsed.path):
        raise RuntimeError(
            "URL Etsy phải là /shop/<tên-shop> hoặc /listing/<listing-id>/..."
        )

    supplied = dict(parse_qsl(parsed.query, keep_blank_values=True))
    has_section = bool(supplied.get("section_id"))
    has_search = bool(supplied.get("search_query")) and not has_section
    expected_ref = "shop_home_active_" if not has_search else "shop_items_search_"
    max_pages = max(1, min(int(max_pages), MAX_COLLECTION_PAGES))
    max_products = max(1, min(int(max_products), MAX_COLLECTION_PRODUCTS))

    products_by_id: dict[str, str] = {}
    pages_scanned = 0
    truncated = False
    with httpx.Client(timeout=90) as client:
        for page_number in range(1, max_pages + 1):
            count_before_page = len(products_by_id)
            page_url = _etsy_shop_page_url(source_url, page_number)
            links = _scrape_links(client, page_url)
            pages_scanned += 1
            page_ids = set()
            for link in links:
                link_query = dict(parse_qsl(urlparse(link).query, keep_blank_values=True))
                if not str(link_query.get("ref") or "").startswith(expected_ref):
                    continue
                listing_url, listing_id = _canonical_etsy_listing_link(link, page_url)
                if not listing_id:
                    continue
                page_ids.add(listing_id)
                products_by_id.setdefault(listing_id, listing_url)
                if len(products_by_id) >= max_products:
                    truncated = True
                    break
            if truncated:
                break
            # Etsy currently renders 36 active shop items per full page. A
            # shorter page is the terminal page; repeated/no-new pages also
            # stop safely if Etsy changes its paging behaviour.
            if (
                len(page_ids) < ETSY_PAGE_SIZE
                or len(products_by_id) == count_before_page
            ):
                break
        else:
            truncated = True

    if not products_by_id:
        filter_name = "section_id" if has_section else "search_query" if has_search else "shop"
        raise RuntimeError(f"Không tìm thấy listing Etsy nào cho {filter_name} này.")

    return {
        "urls": list(products_by_id.values()),
        "pages_scanned": pages_scanned,
        "credits_used": pages_scanned,
        "truncated": truncated,
        "discovery_mode": (
            "etsy_section" if has_section else "etsy_search" if has_search else "etsy_shop"
        ),
    }


def _collection_pagination_link(link: str, page_url: str, collection_url: str) -> str:
    """Return a real pagination URL exposed by the collection page.

    Shopify commonly uses ``?page=2`` while SHOPLINE uses
    ``?page_num=2``. Some storefronts expose cursors or offsets instead.
    Following the rendered link avoids guessing the platform's convention.
    """
    absolute = urljoin(page_url, link)
    parsed = urlparse(absolute)
    collection = urlparse(collection_url)
    link_domain = (parsed.hostname or "").lower().removeprefix("www.")
    collection_domain = (collection.hostname or "").lower().removeprefix("www.")
    if link_domain != collection_domain:
        return ""
    if parsed.path.rstrip("/") != collection.path.rstrip("/"):
        return ""

    query = parse_qsl(parsed.query, keep_blank_values=True)
    pagination = [
        (key, value)
        for key, value in query
        if key.lower() in COLLECTION_PAGINATION_KEYS
    ]
    if not pagination:
        return ""
    # Do not requeue explicit links back to the first page.
    if all(value.strip().lower() in {"", "0", "1", "first"} for _, value in pagination):
        return ""

    # Some themes omit active filters/sort options from pagination anchors.
    # Carry them forward so every page belongs to the same stable result set.
    existing_keys = {key.lower() for key, _ in query}
    for key, value in parse_qsl(collection.query, keep_blank_values=True):
        lower_key = key.lower()
        if lower_key not in COLLECTION_PAGINATION_KEYS and lower_key not in existing_keys:
            query.append((key, value))
            existing_keys.add(lower_key)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def discover_collection_product_links(
    collection_url: str,
    *,
    max_pages: int = MAX_COLLECTION_PAGES,
    max_products: int = MAX_COLLECTION_PRODUCTS,
    path_hint: str = "/products/",
    platform: str = "auto",
    progress_callback=None,
) -> dict:
    """Follow the collection's real pagination links until exhausted.

    The returned product URLs are de-duplicated and stripped of variant query
    strings so a product appears exactly once in the compact CSV.
    """
    _check_key()
    parsed = urlparse(collection_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RuntimeError("URL collection không hợp lệ.")

    max_pages = max(1, min(int(max_pages), MAX_COLLECTION_PAGES))
    max_products = max(1, min(int(max_products), MAX_COLLECTION_PRODUCTS))
    platform = str(platform or "auto").strip().lower()
    if platform == "woo":
        platform = "woocommerce"
    if platform not in {"auto", "shopify", "shopbase", "woocommerce"}:
        raise RuntimeError("Ná»n táº£ng quÃ©t khÃ´ng há»£p lá»‡.")
    stable_collection_url = _stable_collection_url(collection_url)

    if platform == "woocommerce":
        with httpx.Client(timeout=150) as client:
            result = _discover_woocommerce_collection_products(
                client,
                collection_url,
                max_products=max_products,
                strict=True,
            )
        if result is None:
            raise RuntimeError(
                "URL Ä‘Ã£ chá»n WooCommerce nhÆ°ng khÃ´ng Ä‘á»c Ä‘Æ°á»£c Store API."
            )
        result["platform"] = "woocommerce"
        return result

    # Explicit platform routing prevents a transient ShopBase probe failure
    # from falling through to Shopify's similarly-shaped /products/ routes.
    if platform == "shopbase":
        with httpx.Client(timeout=150) as client:
            if _is_search_results_url(stable_collection_url):
                if _is_pumpbear_search_url(stable_collection_url):
                    result = _discover_pumpbear_search_product_links(
                        client,
                        stable_collection_url,
                        domain=parsed.hostname or "",
                        path_hint=path_hint,
                        max_products=max_products,
                    )
                else:
                    result = _discover_sitebase_search_product_links(
                        client,
                        _search_start_url(stable_collection_url),
                        domain=parsed.hostname or "",
                        path_hint=path_hint,
                        max_products=max_products,
                        strict=True,
                        progress_callback=progress_callback,
                    )
            else:
                try:
                    result = _discover_sitebase_collection_products(
                        client,
                        collection_url,
                        domain=parsed.hostname or "",
                        path_hint=path_hint,
                        max_products=max_products,
                        strict=True,
                    )
                except RuntimeError:
                    # Stay on ShopBase: use its Catalogue API from a browser
                    # session instead of incorrectly probing Shopify next.
                    result = _discover_sitebase_collection_via_browser(
                        client,
                        collection_url,
                        domain=parsed.hostname or "",
                        path_hint=path_hint,
                        max_products=max_products,
                    )
        if result is None:
            raise RuntimeError(
                "URL Ä‘Ã£ chá»n ShopBase nhÆ°ng khÃ´ng cÃ³ dá»¯ liá»‡u Catalogue API há»£p lá»‡."
            )
        result["platform"] = "shopbase"
        return result

    if platform == "shopify":
        with httpx.Client(timeout=150) as client:
            if _is_search_results_url(stable_collection_url):
                result = _discover_shopify_search_product_links(
                    client,
                    _search_start_url(stable_collection_url),
                    domain=parsed.hostname or "",
                    path_hint=path_hint,
                    max_products=max_products,
                    strict=True,
                )
            else:
                result = _discover_shopify_collection_via_browser(
                    client,
                    stable_collection_url,
                    domain=parsed.hostname or "",
                    path_hint=path_hint,
                    max_products=max_products,
                )
        if result is None:
            raise RuntimeError(
                "URL Ä‘Ã£ chá»n Shopify nhÆ°ng khÃ´ng Ä‘á»c Ä‘Æ°á»£c Shopify collection API."
            )
        result["platform"] = "shopify"
        return result

    with httpx.Client(timeout=90) as client:
        woocommerce_result = _discover_woocommerce_collection_products(
            client,
            collection_url,
            max_products=max_products,
        )
        if woocommerce_result is not None:
            return woocommerce_result
        shopbase_result = _discover_sitebase_collection_products(
            client,
            collection_url,
            domain=parsed.hostname or "",
            path_hint=path_hint,
            max_products=max_products,
        )
        if shopbase_result is not None:
            shopbase_result["platform"] = "shopbase"
            return shopbase_result

    if _is_search_results_url(stable_collection_url):
        with httpx.Client(timeout=150) as client:
            pumpbear_result = _discover_pumpbear_search_product_links(
                client,
                stable_collection_url,
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
            )
            if pumpbear_result is not None:
                pumpbear_result["platform"] = "shopbase"
                return pumpbear_result
            shopify_search_result = _discover_shopify_search_product_links(
                client,
                _search_start_url(stable_collection_url),
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
            )
            if shopify_search_result is not None:
                shopify_search_result["platform"] = "shopify"
                return shopify_search_result
            printerval_result = _discover_printerval_search_product_links(
                client,
                stable_collection_url,
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
            )
            if printerval_result is not None:
                return printerval_result
            wordpress_result = _discover_wordpress_search_product_links(
                client,
                stable_collection_url,
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
            )
            if wordpress_result is not None:
                return wordpress_result
            search_url = _search_start_url(stable_collection_url)
            sfcc_result = _discover_sfcc_search_product_links(
                client,
                search_url,
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
            )
            if sfcc_result is not None:
                return sfcc_result
            direct_result = _discover_sitebase_search_product_links(
                client,
                search_url,
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
                progress_callback=progress_callback,
            )
            if direct_result is not None:
                direct_result["platform"] = "shopbase"
                return direct_result
            return _discover_infinite_scroll_product_links(
                client,
                search_url,
                domain=parsed.hostname or "",
                path_hint=path_hint,
                max_products=max_products,
            )

    with httpx.Client(timeout=150) as client:
        shopify_result = _discover_shopify_collection_via_browser(
            client,
            stable_collection_url,
            domain=parsed.hostname or "",
            path_hint=path_hint,
            max_products=max_products,
        )
        if shopify_result is not None:
            return shopify_result

    seen: set[str] = set()
    product_urls: list[str] = []
    queued_pages = [_collection_page_url(stable_collection_url, 1)]
    seen_pages = {queued_pages[0]}
    pages_scanned = 0
    truncated = False

    with httpx.Client(timeout=90) as client:
        while queued_pages and pages_scanned < max_pages:
            page_url = queued_pages.pop(0)
            links = _scrape_links(client, page_url)
            pages_scanned += 1

            page_products = []
            for link in links:
                product_url = _canonical_product_link(link, page_url, parsed.hostname or "", path_hint)
                if product_url and product_url not in page_products:
                    page_products.append(product_url)

            for product_url in page_products:
                if product_url in seen:
                    continue
                seen.add(product_url)
                product_urls.append(product_url)
                if len(product_urls) >= max_products:
                    truncated = True
                    break
            if truncated:
                break

            for link in links:
                pagination_url = _collection_pagination_link(
                    link, page_url, stable_collection_url
                )
                if pagination_url and pagination_url not in seen_pages:
                    seen_pages.add(pagination_url)
                    queued_pages.append(pagination_url)

        if queued_pages and pages_scanned >= max_pages:
            truncated = True

    if not product_urls:
        raise RuntimeError(
            "Không tìm thấy link sản phẩm dạng /products/... trong collection này."
        )

    return {
        "urls": product_urls,
        "pages_scanned": pages_scanned,
        "credits_used": pages_scanned,
        "truncated": truncated,
    }


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


def start_collection_product_export(urls: list[str]) -> dict:
    """Start one asynchronous one-credit batch for a collection CSV.

    Unlike ``batch_scrape_products``, this deliberately does not cap the
    complete collection at ``MAX_BATCH_URLS`` and does not wait inside the
    Django request.  Firecrawl queues the work; the browser polls through a
    separate status endpoint so large collections do not hit a web timeout.
    Browser JavaScript returns only title, price and full gallery. This costs
    one base credit per page and avoids both AI extraction and huge raw HTML.
    """
    _check_key()
    unique_urls = list(dict.fromkeys(url for url in urls if url))[:MAX_COLLECTION_PRODUCTS]
    if not unique_urls:
        raise RuntimeError("Collection không có URL sản phẩm để crawl.")

    payload = {
        "urls": unique_urls,
        "formats": ["markdown"],
        "onlyMainContent": True,
        "ignoreInvalidURLs": True,
        "maxAge": COLLECTION_CACHE_MAX_AGE_MS,
        "actions": [
            {"type": "wait", "milliseconds": 500},
            {"type": "executeJavascript", "script": COLLECTION_PRODUCT_ACTION_SCRIPT},
        ],
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{API_BASE}/batch/scrape", headers=_headers(), json=payload)
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        body = r.json()

    job_id = body.get("id")
    if not job_id:
        raise RuntimeError("Firecrawl không trả về job id.")
    return {
        "job_id": job_id,
        "invalid_urls": body.get("invalidURLs") or [],
        "requested": len(unique_urls),
    }


def get_collection_product_export_status(job_id: str) -> dict:
    """Read a collection batch status without blocking or polling."""
    _check_key()
    with httpx.Client(timeout=60) as client:
        r = client.get(f"{API_BASE}/batch/scrape/{job_id}", headers=_headers())
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        body = r.json()
    status, completed, total = _collection_batch_progress(body)
    return {
        "status": status,
        "completed": completed,
        "total": total,
        "credits_used": body.get("creditsUsed") or 0,
    }


def _normalise_collection_products(pages: list[dict]) -> list[dict]:
    from .collection_fast_client import (
        _clean_image_url,
        _sitebase_gallery_images,
        parse_product_html,
    )

    products = []
    for page in pages:
        metadata = page.get("metadata") or {}
        source_url = metadata.get("sourceURL") or metadata.get("url") or ""
        if not _page_failed(metadata):
            returns = ((page.get("actions") or {}).get("javascriptReturns")) or []
            action_product = None
            for action_result in reversed(returns):
                value = (
                    action_result.get("value")
                    if isinstance(action_result, dict)
                    else action_result
                )
                if not isinstance(value, dict):
                    continue
                title = html.unescape(str(value.get("title") or "")).strip()
                price = value.get("price")
                action_url = str(source_url or value.get("product_url") or "").strip()
                images = []
                for image in value.get("images") or []:
                    if not isinstance(image, str) or not image.strip():
                        continue
                    absolute = _clean_image_url(urljoin(action_url, image.strip()))
                    if urlparse(absolute).scheme not in ("http", "https"):
                        continue
                    if absolute not in images:
                        images.append(absolute)
                images = _sitebase_gallery_images(images, action_url)
                if title and price not in (None, "") and action_url and images:
                    action_product = {
                        "title": title,
                        "price": price,
                        "compare_at_price": value.get("compare_at_price"),
                        "currency": str(value.get("currency") or "").upper(),
                        "product_url": action_url,
                        "images": images,
                    }
                    break
            if action_product:
                products.append(action_product)
                continue

        raw_html = page.get("rawHtml") or page.get("rawHTML") or ""
        if raw_html and source_url and not _page_failed(metadata):
            parsed = parse_product_html(raw_html, source_url)
            if parsed:
                products.append(parsed)
                continue

        # Backward compatibility for jobs started before raw-HTML mode.
        extracted = page.get("json") or {}
        if _page_failed(metadata) or not extracted.get("title"):
            continue

        markdown = page.get("markdown") or ""
        _verify_and_fix_price(extracted, markdown)
        images = []
        for image in extracted.get("images") or []:
            if not isinstance(image, str) or not image.strip():
                continue
            absolute = urljoin(source_url, image.strip()) if source_url else image.strip()
            if absolute not in images:
                images.append(absolute)

        products.append({
            "title": str(extracted.get("title") or "").strip(),
            "price": extracted.get("price"),
            "compare_at_price": extracted.get("compare_at_price"),
            "currency": str(extracted.get("currency") or "").upper(),
            "product_url": source_url,
            "images": images,
        })
    return products


def get_collection_product_export(job_id: str) -> dict:
    """Fetch every result page for a completed collection batch."""
    _check_key()
    with httpx.Client(timeout=60) as client:
        r = client.get(f"{API_BASE}/batch/scrape/{job_id}", headers=_headers())
        if r.is_error:
            raise RuntimeError(f"Firecrawl {r.status_code}: {r.text[:500]}")
        body = r.json()
        status, completed, total = _collection_batch_progress(body)
        if status != "completed":
            raise CollectionBatchIncomplete(
                f"Collection batch is not complete ({completed}/{total or '?'} pages)."
            )

        first_pages = list(body.get("data") or [])
        products = _normalise_collection_products(first_pages)
        total_pages = len(first_pages)
        next_url = body.get("next")
        # Firecrawl paginates large result payloads. Follow every signed
        # ``next`` URL, with a generous guard against a malformed loop.
        seen_next = set()
        while next_url and next_url not in seen_next and len(seen_next) < 1000:
            seen_next.add(next_url)
            nr = client.get(urljoin(f"{API_BASE}/", next_url), headers=_headers())
            if nr.is_error:
                raise RuntimeError(f"Firecrawl {nr.status_code}: {nr.text[:500]}")
            next_body = nr.json()
            next_pages = list(next_body.get("data") or [])
            products.extend(_normalise_collection_products(next_pages))
            total_pages += len(next_pages)
            next_url = next_body.get("next")

    return {
        "products": products,
        "credits_used": body.get("creditsUsed") or 0,
        "total_pages_crawled": body.get("total") or total_pages,
    }
