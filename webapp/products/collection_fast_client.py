"""Fast, deterministic product extraction from public JSON-LD markup.

Most Shopify/SHOPLINE-style product pages publish title, offers and gallery
images as schema.org Product JSON-LD. Reading that data directly is much
faster than asking an LLM to re-extract the same fields and costs no Firecrawl
JSON credits. Firecrawl remains the fallback for storefronts that hide or omit
the structured markup.
"""

import html
import ipaddress
import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx


# Ten workers are still fast for ordinary collection-sized batches.
MAX_WORKERS = 10
# ShopBase is more stable when one persistent client feeds a smaller pool.
# Recreating the client every 40 URLs caused PumpBear to throttle later groups.
SHOPBASE_MAX_WORKERS = 6
SHOPBASE_RECOVERY_DELAYS_SEC = (3.0, 10.0, 30.0)
# Large ShopBase searches can allow the first few dozen documents and then
# throttle every remaining request. Probe a bounded prefix before committing
# to thousands of detail-page GETs and never retry a massive blocked tail.
SHOPBASE_CIRCUIT_BREAKER_MIN_URLS = 500
SHOPBASE_PROBE_SIZE = 120
SHOPBASE_PROBE_MIN_SUCCESS_RATIO = 0.80
FETCH_ATTEMPTS = 3
MAX_REDIRECTS = 3
RECOVERY_WORKERS = 3
RECOVERY_DELAYS_SEC = (2.0, 5.0, 10.0)

_JSON_LD_RE = re.compile(
    r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_ORIGINAL_PRICE_RE = re.compile(
    r"<(?:div|span)\b[^>]*class=[\"'][^\"']*"
    r"(?:product-price__origin|price__compare|compare-at|compare_at)"
    r"[^\"']*[\"'][^>]*>(.*?)</(?:div|span)>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

_IMAGE_PLACEHOLDER_RE = re.compile(
    r"(?:woocommerce[-_]?placeholder|(?:^|[/_.-])placeholder(?:[/_.-]|$)|"
    r"awaiting[-_]?product[-_]?image)",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _has_type(node: dict, wanted: str) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return wanted in node_type
    return node_type == wanted


def _json_ld_nodes(data):
    if isinstance(data, dict):
        yield data
        graph = data.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                if isinstance(node, dict):
                    yield node
    elif isinstance(data, list):
        for node in data:
            if isinstance(node, dict):
                yield node


def _load_json_ld(raw: str):
    for candidate in (raw.strip(), html.unescape(raw.strip())):
        try:
            return json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def _image_url(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("url") or value.get("contentUrl") or "").strip()
    return ""


def _clean_image_url(url: str) -> str:
    """Drop display-resize params while retaining any CDN auth parameters."""
    parsed = urlparse(html.unescape(url))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {
            "w", "h", "width", "height",
            "min_width", "min_height", "max_width", "max_height",
        }
    ]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=""))


def _usable_product_image_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
        and not re.search(r"\.svg(?:$|[?#])", str(url), re.IGNORECASE)
        and not _IMAGE_PLACEHOLDER_RE.search(html.unescape(str(url)))
    )


def _pumpbear_schema_gallery_is_complete(
    source_url: str, image_urls: list[str]
) -> bool:
    """PumpBear's new storefront publishes its full gallery in Product JSON-LD."""
    source_host = (urlparse(source_url).hostname or "").lower().removeprefix("www.")
    if source_host != "pumpbear.com" or not image_urls:
        return False
    return all(
        (urlparse(image_url).hostname or "").lower() == "cdn.pumpbear.com"
        and urlparse(image_url).path.lower().startswith("/products/")
        for image_url in image_urls
    )


class _WooGalleryParser(HTMLParser):
    """Collect image candidates only from Woo's product gallery root."""

    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
    _IMAGE_ATTRS = (
        "data-large_image",
        "data-large-image",
        "data-zoom-image",
        "data-src-zoom-image",
        "data-src",
        "src",
    )

    def __init__(self, source_url: str):
        super().__init__(convert_charrefs=True)
        self.source_url = source_url
        self.gallery_depth = 0
        self.images: list[str] = []

    def _add(self, value: str) -> None:
        raw_url = html.unescape(str(value or "")).strip()
        if not raw_url or raw_url.startswith(("#", "data:", "javascript:")):
            return
        # srcset candidates include a trailing width/density descriptor.
        raw_url = raw_url.split()[0]
        image_url = _clean_image_url(urljoin(self.source_url, raw_url))
        if _usable_product_image_url(image_url) and image_url not in self.images:
            self.images.append(image_url)

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = dict(attrs)
        classes = set(str(attributes.get("class") or "").split())
        entering_gallery = (
            self.gallery_depth == 0
            and "woocommerce-product-gallery" in classes
        )
        if entering_gallery:
            self.gallery_depth = 1
        elif self.gallery_depth and tag not in self._VOID_TAGS:
            self.gallery_depth += 1
        if not self.gallery_depth:
            return

        if tag == "img":
            alt = str(attributes.get("alt") or "")
            if _IMAGE_PLACEHOLDER_RE.search(alt.replace(" ", "-")):
                return
            for key in self._IMAGE_ATTRS:
                if attributes.get(key):
                    self._add(attributes[key])
                    break
            return
        if tag == "a" and attributes.get("href"):
            self._add(attributes["href"])
            return
        if tag == "source":
            candidates = str(attributes.get("srcset") or "").split(",")
            if candidates:
                self._add(candidates[-1].strip())

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if self.gallery_depth and tag not in self._VOID_TAGS:
            self.gallery_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if self.gallery_depth and tag not in self._VOID_TAGS:
            self.gallery_depth -= 1


def _woocommerce_gallery_images(page_html: str, source_url: str) -> list[str]:
    """Read the authoritative Woo gallery without logos or recommendations."""
    parser = _WooGalleryParser(source_url)
    try:
        parser.feed(page_html or "")
        parser.close()
    except (TypeError, ValueError):
        return []
    return parser.images


def _sitebase_gallery_images(raw_images, source_url: str) -> list[str]:
    """Keep the real ShopBase gallery, not images embedded elsewhere in state.

    Some ShopBase product state objects mix the current gallery with country
    flags, theme assets, description badges and related-product thumbnails.
    The platform's original gallery files use its versioned product path.  If
    that authoritative path is present, discard every non-gallery candidate.
    Other storefront/CDN formats keep the previous behaviour unchanged.
    """
    images = []
    for item in raw_images or []:
        raw_url = (
            str(item.get("src") or _image_url(item))
            if isinstance(item, dict)
            else _image_url(item)
        )
        if not raw_url:
            continue
        image_url = _clean_image_url(urljoin(source_url, raw_url))
        if image_url and image_url not in images:
            images.append(image_url)

    versioned_gallery = [
        image_url
        for image_url in images
        if (urlparse(image_url).hostname or "").lower().endswith("thesitebase.net")
        and re.search(r"/products/ver_[^/]+/", urlparse(image_url).path, re.I)
    ]
    return versioned_gallery or images


@lru_cache(maxsize=256)
def _hostname_is_public(hostname: str) -> bool:
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except (OSError, ValueError):
        return False


def _is_public_http_url(url: str) -> bool:
    """Block local/private targets before the server performs a direct GET."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    return _hostname_is_public(parsed.hostname)


def _offer_price(product: dict) -> tuple[object, str]:
    offers = product.get("offers") or []
    if isinstance(offers, dict):
        offers = [offers]
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        price = offer.get("price")
        if price is None:
            price = offer.get("lowPrice")
        if price is not None:
            return price, str(offer.get("priceCurrency") or "").upper()
    return None, ""


def _original_price(page_html: str) -> str:
    match = _ORIGINAL_PRICE_RE.search(page_html)
    if not match:
        return ""
    text = _TAG_RE.sub(" ", match.group(1))
    return " ".join(html.unescape(text).split())


def _sitebase_hydrated_product(page_html: str, source_url: str) -> dict | None:
    """Read SiteBase's server-hydrated product object from rendered HTML.

    Its schema.org block contains only the lead image, while this state object
    contains the exact full gallery shown by the thumbnail carousel.
    """
    handle = urlparse(source_url).path.rstrip("/").rsplit("/", 1)[-1]
    if not handle:
        return None
    needle = f'"handle":{json.dumps(handle, ensure_ascii=False)}'
    decoder = json.JSONDecoder()
    search_from = 0
    while True:
        handle_pos = page_html.find(needle, search_from)
        if handle_pos < 0:
            return None
        marker_pos = page_html.rfind(
            '"product":', max(0, handle_pos - 120_000), handle_pos
        )
        if marker_pos >= 0:
            value_pos = marker_pos + len('"product":')
            while value_pos < len(page_html) and page_html[value_pos].isspace():
                value_pos += 1
            try:
                product, _ = decoder.raw_decode(page_html[value_pos:])
            except (TypeError, ValueError, json.JSONDecodeError):
                product = None
            if isinstance(product, dict) and product.get("handle") == handle:
                return product
        search_from = handle_pos + len(needle)


def _hydrated_price(product: dict) -> tuple[object, object]:
    variants = product.get("variants") or []
    selected = next(
        (
            variant
            for variant in variants
            if isinstance(variant, dict) and variant.get("available") is not False
        ),
        None,
    )
    if selected is None:
        selected = next((item for item in variants if isinstance(item, dict)), {})
    return selected.get("price"), selected.get("compare_at_price")


def _shopify_minor_units(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)) / Decimal("100")
    except (InvalidOperation, TypeError, ValueError):
        return None


def _shopify_product_from_json(data: dict, source_url: str) -> dict | None:
    """Read Shopify's public ``/products/<handle>.js`` product payload.

    Unlike many Shopify JSON-LD themes, this endpoint carries the complete
    gallery and expresses money in integer minor units, so 1990 becomes 19.90
    instead of the incorrect 1990.00 seen in some localized schema blocks.
    """
    if not isinstance(data, dict):
        return None
    variants = [item for item in data.get("variants") or [] if isinstance(item, dict)]
    selected = next((item for item in variants if item.get("available") is not False), None)
    if selected is None:
        selected = variants[0] if variants else {}
    price = _shopify_minor_units(selected.get("price", data.get("price")))
    compare_at_price = _shopify_minor_units(
        selected.get("compare_at_price", data.get("compare_at_price"))
    )
    images = []
    for item in data.get("images") or []:
        raw_url = item.get("src") if isinstance(item, dict) else item
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        image_url = _clean_image_url(urljoin(source_url, raw_url.strip()))
        if image_url not in images:
            images.append(image_url)
    title = html.unescape(str(data.get("title") or "")).strip()
    if not title or price is None or not images:
        return None
    parsed = urlparse(source_url)
    product_url = urlunparse(parsed._replace(query="", fragment=""))
    return {
        "title": title,
        "price": price,
        "currency": "",
        "compare_at_price": compare_at_price,
        "product_url": product_url,
        "images": images,
        "_gallery_complete": True,
    }


def _shopify_product_json_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    if not re.match(r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?products/[^/]+/?$", parsed.path, re.I):
        return ""
    path = parsed.path.rstrip("/") + ".js"
    return urlunparse(parsed._replace(path=path, query="", fragment=""))


def _fetch_shopify_product_json(
    client: httpx.Client, source_url: str
) -> dict | None:
    """Try Shopify's small public product payload before the heavier HTML.

    Some storefront WAFs rate-limit product documents while still allowing the
    platform-owned ``.js`` endpoint.  Trying it first also avoids downloading
    and parsing a full page when the complete gallery is already available.
    """
    current_url = _shopify_product_json_url(source_url)
    if not current_url:
        return None
    for _redirect in range(MAX_REDIRECTS + 1):
        if not _is_public_http_url(current_url):
            return None
        try:
            response = client.get(current_url, headers={"Accept": "application/json"})
        except httpx.HTTPError:
            return None
        if response.is_redirect and response.headers.get("location"):
            current_url = urljoin(str(response.url), response.headers["location"])
            continue
        if response.is_error:
            return None
        try:
            product = _shopify_product_from_json(response.json(), source_url)
        except (TypeError, ValueError):
            return None
        if product:
            product.pop("_gallery_complete", None)
        return product
    return None


def parse_product_html(page_html: str, source_url: str) -> dict | None:
    """Extract compact CSV fields, including SiteBase's full hydrated gallery."""
    schema_product = None
    for match in _JSON_LD_RE.finditer(page_html or ""):
        data = _load_json_ld(match.group(1))
        for node in _json_ld_nodes(data):
            if _has_type(node, "Product"):
                schema_product = node
                break
        if schema_product is not None:
            break
    hydrated_product = _sitebase_hydrated_product(page_html or "", source_url)
    woo_gallery = _woocommerce_gallery_images(page_html or "", source_url)
    if schema_product is None and hydrated_product is None:
        return None

    title = str((schema_product or {}).get("name") or "").strip()
    price, currency = _offer_price(schema_product or {})
    raw_images = (schema_product or {}).get("image") or []
    if not isinstance(raw_images, list):
        raw_images = [raw_images]
    images = []
    for item in raw_images:
        url = _clean_image_url(_image_url(item))
        if _usable_product_image_url(url) and url not in images:
            images.append(url)

    compare_at_price = _original_price(page_html)
    gallery_complete = False
    if _pumpbear_schema_gallery_is_complete(source_url, images):
        gallery_complete = True
    if woo_gallery:
        images = woo_gallery
        gallery_complete = True
    if hydrated_product is not None:
        title = str(hydrated_product.get("title") or title).strip()
        hydrated_price, hydrated_compare = _hydrated_price(hydrated_product)
        if hydrated_price is not None:
            price = hydrated_price
        if hydrated_compare not in (None, "", 0, 0.0):
            compare_at_price = hydrated_compare
        gallery = _sitebase_gallery_images(
            hydrated_product.get("images") or [],
            source_url,
        )
        if gallery:
            images = gallery
            gallery_complete = True

    if not title or price is None or not images:
        return None

    parsed_source = urlparse(source_url)
    product_url = urlunparse(parsed_source._replace(query="", fragment=""))
    return {
        "title": title,
        "price": price,
        "currency": currency,
        "compare_at_price": compare_at_price,
        "product_url": product_url,
        "images": images,
        # JSON-LD galleries vary wildly by theme and often contain only the
        # featured image. Only a platform-owned hydration payload is treated
        # as authoritative; ambiguous pages fall through to the 1-credit
        # rendered Firecrawl extractor instead of silently exporting 1 image.
        "_gallery_complete": gallery_complete,
    }


def _fetch_product(
    client: httpx.Client,
    url: str,
    *,
    try_shopify_json: bool = True,
    attempts: int = FETCH_ATTEMPTS,
) -> dict | None:
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            if try_shopify_json:
                shopify_product = _fetch_shopify_product_json(client, url)
                if shopify_product:
                    return shopify_product
            current_url = url
            for _redirect in range(MAX_REDIRECTS + 1):
                if not _is_public_http_url(current_url):
                    break
                response = client.get(current_url)
                if response.is_redirect and response.headers.get("location"):
                    current_url = urljoin(str(response.url), response.headers["location"])
                    continue
                if not response.is_error:
                    product = parse_product_html(response.text, str(response.url) or current_url)
                    response_url = str(response.url) or current_url
                    shopify_url = _shopify_product_json_url(response_url)
                    if try_shopify_json and shopify_url and response_url != url:
                        try:
                            shopify_product = _fetch_shopify_product_json(
                                client, response_url
                            )
                            if shopify_product:
                                if product:
                                    shopify_product["currency"] = product.get("currency") or ""
                                return shopify_product
                        except (httpx.HTTPError, TypeError, ValueError):
                            pass
                    if product and product.pop("_gallery_complete", False):
                        return product
                    if product:
                        # The page loaded successfully but exposed only an
                        # ambiguous JSON-LD gallery. Retrying the same HTTP
                        # response cannot improve completeness; hand it to the
                        # rendered fallback immediately.
                        return None
                    # A successful HTML response with no usable product data
                    # is equally deterministic (for Woo this is commonly a
                    # real no-image placeholder page). Do not download it two
                    # more times before handing the URL to rendered fallback.
                    return None
                break
        except (httpx.HTTPError, ValueError):
            pass
        if attempt + 1 < attempts:
            # Exponential backoff gives a rate-limited storefront time to
            # recover instead of repeating the same burst immediately.
            time.sleep(0.35 * (2**attempt))
    return None


def scrape_products_fast(
    urls: list[str],
    max_workers: int = MAX_WORKERS,
    *,
    try_shopify_json: bool = True,
    fetch_attempts: int = FETCH_ATTEMPTS,
    recovery_delays: tuple[float, ...] = RECOVERY_DELAYS_SEC,
    read_timeout: float = 30,
    progress_callback=None,
) -> dict:
    """Fetch product pages concurrently and preserve collection order."""
    unique_urls = list(dict.fromkeys(url for url in urls if url))
    if not unique_urls:
        return {
            "products": [],
            "products_by_url": {},
            "failed_urls": [],
            "elapsed_seconds": 0.0,
        }

    worker_count = max(1, min(int(max_workers), MAX_WORKERS, len(unique_urls)))
    started = time.monotonic()
    transport = httpx.HTTPTransport(retries=1)
    limits = httpx.Limits(
        max_connections=worker_count,
        max_keepalive_connections=worker_count,
    )
    timeout = httpx.Timeout(connect=10, read=read_timeout, write=10, pool=15)

    retry_rounds = 0
    recovered_count = 0
    with httpx.Client(
        headers=_HEADERS,
        follow_redirects=False,
        transport=transport,
        limits=limits,
        timeout=timeout,
    ) as client:
        def fetch_many(
            target_urls: list[str],
            workers: int,
            *,
            stage: str,
            retry_round: int = 0,
        ) -> list[dict | None]:
            page_results: list[dict | None] = [None] * len(target_urls)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(
                        _fetch_product,
                        client,
                        url,
                        try_shopify_json=try_shopify_json,
                        attempts=fetch_attempts,
                    ): index
                    for index, url in enumerate(target_urls)
                }
                succeeded = 0
                for completed, future in enumerate(as_completed(futures), start=1):
                    index = futures[future]
                    try:
                        product = future.result()
                    except Exception:
                        product = None
                    page_results[index] = product
                    if product is not None:
                        succeeded += 1
                    if progress_callback:
                        try:
                            progress_callback({
                                "stage": stage,
                                "completed": completed,
                                "total": len(target_urls),
                                "succeeded": succeeded,
                                "retry_round": retry_round,
                            })
                        except Exception:
                            pass
            return page_results

        results = fetch_many(
            unique_urls,
            worker_count,
            stage="fast_path",
        )
        results_by_url = dict(zip(unique_urls, results))

        # Large ShopBase/Woo batches can accept the first burst and then
        # temporarily throttle the same public product documents. Retrying the
        # failed subset with fewer workers after a short cooldown is much
        # cheaper than immediately spending one Firecrawl credit per URL.
        for delay in recovery_delays:
            failed = [url for url in unique_urls if results_by_url[url] is None]
            if not failed:
                break
            retry_rounds += 1
            time.sleep(delay)
            retry_workers = max(1, min(RECOVERY_WORKERS, len(failed)))
            recovered = fetch_many(
                failed,
                retry_workers,
                stage="retrying",
                retry_round=retry_rounds,
            )
            for url, product in zip(failed, recovered):
                if product is not None and results_by_url[url] is None:
                    results_by_url[url] = product
                    recovered_count += 1

        results = [results_by_url[url] for url in unique_urls]

    products = [product for product in results if product is not None]
    products_by_url = {
        url: product
        for url, product in zip(unique_urls, results)
        if product is not None
    }
    failed_urls = [url for url, product in zip(unique_urls, results) if product is None]
    return {
        "products": products,
        # Keep the requested URL association so a later Firecrawl fallback can
        # merge only failed rows back into the original collection order.
        "products_by_url": products_by_url,
        "failed_urls": failed_urls,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "retry_rounds": retry_rounds,
        "retry_recovered_count": recovered_count,
    }


def scrape_shopbase_products_fast(urls: list[str], progress_callback=None) -> dict:
    """Read the complete ShopBase list through one persistent HTTP session.

    One client/connection pool handles the entire URL set and retries only the
    transient tail after cooldowns. This is one logical fast-path run even
    though a full gallery still requires one public product document per URL.
    """
    unique_urls = list(dict.fromkeys(url for url in urls if url))
    scrape_kwargs = {
        "max_workers": SHOPBASE_MAX_WORKERS,
        "try_shopify_json": False,
        "fetch_attempts": 1,
        "recovery_delays": SHOPBASE_RECOVERY_DELAYS_SEC,
        "read_timeout": 20,
    }
    if progress_callback:
        scrape_kwargs["progress_callback"] = progress_callback
    if len(unique_urls) < SHOPBASE_CIRCUIT_BREAKER_MIN_URLS:
        result = scrape_products_fast(unique_urls, **scrape_kwargs)
        attempted_count = len(unique_urls)
        circuit_breaker = False
    else:
        # A bounded probe prevents the old 8,000-product failure mode where
        # almost every throttled URL was downloaded four times. Large runs do
        # not use recovery rounds; grouped browser fallback handles the tail.
        probe_urls = unique_urls[:SHOPBASE_PROBE_SIZE]
        probe_kwargs = {
            **scrape_kwargs,
            "recovery_delays": (),
        }
        result = scrape_products_fast(probe_urls, **probe_kwargs)
        attempted_count = len(probe_urls)
        probe_success = len(result.get("products_by_url") or {})
        success_ratio = probe_success / max(1, len(probe_urls))
        circuit_breaker = success_ratio < SHOPBASE_PROBE_MIN_SUCCESS_RATIO
        if circuit_breaker:
            result["failed_urls"] = list(dict.fromkeys(
                list(result.get("failed_urls") or [])
                + unique_urls[len(probe_urls):]
            ))
        else:
            remaining_urls = unique_urls[len(probe_urls):]
            remaining_kwargs = {
                **scrape_kwargs,
                "recovery_delays": (),
            }
            if progress_callback:
                def remaining_progress(event: dict) -> None:
                    adjusted = dict(event)
                    adjusted["completed"] = len(probe_urls) + int(
                        event.get("completed") or 0
                    )
                    adjusted["total"] = len(unique_urls)
                    adjusted["succeeded"] = probe_success + int(
                        event.get("succeeded") or 0
                    )
                    progress_callback(adjusted)
                remaining_kwargs["progress_callback"] = remaining_progress
            remaining = scrape_products_fast(remaining_urls, **remaining_kwargs)
            attempted_count = len(unique_urls)
            combined_by_url = {
                **(result.get("products_by_url") or {}),
                **(remaining.get("products_by_url") or {}),
            }
            result = {
                **remaining,
                "products": [
                    combined_by_url[url]
                    for url in unique_urls
                    if url in combined_by_url
                ],
                "products_by_url": combined_by_url,
                "failed_urls": [
                    url for url in unique_urls if url not in combined_by_url
                ],
                "elapsed_seconds": round(
                    float(result.get("elapsed_seconds") or 0)
                    + float(remaining.get("elapsed_seconds") or 0),
                    2,
                ),
                "retry_rounds": 0,
                "retry_recovered_count": 0,
            }
    failed_count = len(result.get("failed_urls") or [])
    recovered_count = int(result.get("retry_recovered_count") or 0)
    result.update({
        "attempted_count": attempted_count,
        "recovery_attempted": failed_count + recovered_count,
        "batches": 1 if unique_urls else 0,
        "stopped_by_waf": bool(
            unique_urls
            and (circuit_breaker or not result.get("products_by_url"))
        ),
        "circuit_breaker": circuit_breaker,
    })
    return result
