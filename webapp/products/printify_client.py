"""
Thin synchronous client for the Printify API, used by Django views.

Auth: set env var PRINTIFY_TOKEN. Optional: PRINTIFY_SHOP_ID as default shop.
Independent of ../server.py by design (see templates_catalog.py).
"""

import os
import time
import httpx

API_BASE = "https://api.printify.com/v1"


def _token() -> str:
    token = os.environ.get("PRINTIFY_TOKEN", "")
    if not token:
        raise RuntimeError("PRINTIFY_TOKEN env var is not set.")
    return token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
        "User-Agent": "SunfadeWeb/1.0",
    }


def default_shop_id() -> str:
    shop = os.environ.get("PRINTIFY_SHOP_ID", "")
    if not shop:
        raise RuntimeError("PRINTIFY_SHOP_ID env var is not set.")
    return shop


def list_shops() -> list[dict]:
    """All Printify-connected shops on this account (Shopify, Etsy,
    Amazon, etc.), used to let the user switch which one the app acts on."""
    return _request("GET", "/shops.json")


def _request(method: str, path: str, json=None, retries: int = 4):
    url = f"{API_BASE}{path}"
    delay = 2.0
    with httpx.Client(timeout=60) as client:
        for _ in range(retries):
            r = client.request(method, url, headers=_headers(), json=json)
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", delay))
                time.sleep(wait)
                delay *= 2
                continue
            if r.status_code >= 500:
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code >= 400:
                try:
                    detail = r.json()
                except ValueError:
                    detail = r.text
                raise RuntimeError(
                    f"Printify API {r.status_code} for {method} {path}: {detail}"
                )
            return r.json() if r.text else {}
    raise RuntimeError(f"Printify API failed after {retries} retries: {method} {path}")


def upload_artwork_file(file_bytes: bytes, file_name: str) -> dict:
    import base64
    b64 = base64.b64encode(file_bytes).decode()
    payload = {"file_name": file_name, "contents": b64}
    res = _request("POST", "/uploads/images.json", json=payload)
    return {"id": res.get("id"), "file_name": res.get("file_name"),
            "width": res.get("width"), "height": res.get("height")}


def create_from_template(tpl: dict, image_id: str, title: str, description: str,
                          tags: list[str], shop_id: str) -> dict:
    variants = []
    variant_ids = []
    for v in tpl["variants"]:
        variants.append({"id": v["id"], "price": v["price"], "is_enabled": True})
        variant_ids.append(v["id"])

    ph = tpl["placeholder"]
    positions = tpl.get("positions", ["front"])
    if tpl.get("image_id_fills_all"):
        pos_images = {pos: image_id for pos in positions}
    else:
        pos_images = {"front": image_id}

    placeholders = []
    for pos in positions:
        img = pos_images.get(pos)
        if not img:
            continue
        p = {
            "position": pos,
            "images": [{
                "id": img,
                "x": ph["x"], "y": ph["y"], "scale": ph["scale"],
                "angle": ph["angle"], "flipX": ph["flipX"], "flipY": ph["flipY"],
            }],
        }
        if tpl.get("decoration_method"):
            p["decoration_method"] = tpl["decoration_method"]
        placeholders.append(p)

    payload = {
        "title": title,
        "description": description,
        "tags": tags or [],
        "blueprint_id": tpl["blueprint_id"],
        "print_provider_id": tpl["print_provider_id"],
        "variants": variants,
        "print_areas": [{"variant_ids": variant_ids, "placeholders": placeholders}],
    }
    return _request("POST", f"/shops/{shop_id}/products.json", json=payload)


def get_blueprint_images(blueprint_id: int) -> list[str]:
    """Generic sample mockup image URLs for a blueprint, from Printify's public
    catalog (blank product photos — not tied to any product we've created)."""
    blueprint = _request("GET", f"/catalog/blueprints/{blueprint_id}.json")
    return list(blueprint.get("images") or [])


def get_product(product_id: str, shop_id: str) -> dict:
    return _request("GET", f"/shops/{shop_id}/products/{product_id}.json")


def list_products(shop_id: str, limit: int = 50, page: int = 1) -> list[dict]:
    result = _request("GET", f"/shops/{shop_id}/products.json?limit={limit}&page={page}")
    return result.get("data", [])


def list_all_products(shop_id: str, page_size: int = 50, max_pages: int = 40) -> list[dict]:
    """Fetch every page of the shop's products (Printify caps each page at
    `page_size`), stopping once a page comes back short/empty or
    `max_pages` is hit (safety cap — 40 * 50 = 2000 products)."""
    all_products = []
    for page in range(1, max_pages + 1):
        batch = list_products(shop_id, limit=page_size, page=page)
        all_products.extend(batch)
        if len(batch) < page_size:
            break
    return all_products


def publish_product(product_id: str, shop_id: str) -> dict:
    payload = {"title": True, "description": True, "images": True,
               "variants": True, "tags": True, "keyFeatures": True,
               "shipping_template": True}
    return _request("POST", f"/shops/{shop_id}/products/{product_id}/publish.json", json=payload)


def delete_product(product_id: str, shop_id: str) -> dict:
    return _request("DELETE", f"/shops/{shop_id}/products/{product_id}.json")
