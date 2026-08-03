#!/usr/bin/env python3
"""
Printify MCP Server for Sunfade Co
-----------------------------------
Exposes a small, purpose-built toolset so Claude can create & publish
print-on-demand products on Printify from reusable SKU templates.

Design principle: the caller never assembles raw Printify JSON.
They call `create_from_template` with a handful of fields and the
server expands it into the full product payload using a stored template.

Auth: set env var  PRINTIFY_TOKEN  (never hard-code the token).
Optional: set      PRINTIFY_SHOP_ID  to skip passing shop_id every call.
"""

import os
import asyncio
import httpx
from typing import Any
try:
    # Newer distribution
    from mcp.server import MCPServer as _Server
except ImportError:  # pragma: no cover
    # FastMCP-style distribution
    from mcp.server.fastmcp import FastMCP as _Server

API_BASE = "https://api.printify.com/v1"
TOKEN = os.environ.get("PRINTIFY_TOKEN", "")
DEFAULT_SHOP = os.environ.get("PRINTIFY_SHOP_ID", "")

mcp = _Server("printify-sunfade")

# ---------------------------------------------------------------------------
# TEMPLATES
# Each template captures everything provider-specific that we verified once
# by hand from a real Printify product. To add a new SKU, create one product
# manually, GET its JSON, and add an entry here.
# ---------------------------------------------------------------------------
TEMPLATES: dict[str, dict[str, Any]] = {
    # Satin & Archival Matte Posters — Today's Graphics (rating 9.4)
    # Vertical 2:3 set, MATTE finish (option 1932). Prices in cents.
    "poster_matte_2x3": {
        "blueprint_id": 1309,
        "print_provider_id": 234,
        "variants": [
            {"id": 99118, "label": "12x18", "price": 2900},
            {"id": 99132, "label": "20x30", "price": 4200},
            {"id": 99138, "label": "24x36", "price": 5200},
        ],
        # placeholder transform verified from a real product (centered, full-bleed)
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 0.9956715564046932,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "7200x10800 px PNG, 2:3, sRGB, full-bleed",
    },

    # Classic Stretched Canvas — Colorway (rating 8.6, ~1.1-day production, USA)
    # Vertical 2:3 set, depth 1.5" (option 4149). Prices in cents.
    # Canvas base cost is high (wood frame) so margins ~40-45% even at these prices.
    "canvas_2x3": {
        "blueprint_id": 1238,
        "print_provider_id": 48,
        "variants": [
            {"id": 93932, "label": "12x18", "price": 6900},
            {"id": 93937, "label": "16x24", "price": 8900},
            {"id": 93942, "label": "20x30", "price": 10900},
            {"id": 93946, "label": "24x36", "price": 12900},
        ],
        # placeholder verified from real canvas product (centered, full-bleed)
        "placeholder": {"x": 0.5, "y": 0.4999, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "7200x10800 px PNG, 2:3, sRGB, full-bleed "
                        "(same file as poster; keep key detail away from canvas fold edges)",
    },

    # Square Stickers — SPOKE Custom Products (rating 9.3, <1-day production, USA)
    # 4 square sizes, surface White (2114). UV decoration. Prices in cents.
    # NOTE: single-sticker margin is thin due to ~$4.29 ship. Best sold as
    # add-on to a poster (shared shipping) or in multi-packs.
    "sticker": {
        "blueprint_id": 384,
        "print_provider_id": 1,
        "variants": [
            {"id": 45740, "label": "2x2", "price": 599},
            {"id": 45742, "label": "3x3", "price": 699},
            {"id": 45744, "label": "4x4", "price": 799},
            {"id": 45746, "label": "6x6", "price": 999},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "uv",
        "artwork_spec": "1800x1800 px PNG, 1:1 square, transparent background "
                        "for die-cut (badge centered with margin). NOT the poster file.",
    },

    # Ceramic Mug 11oz — SPOKE Custom Products (rating 9.3, <1-day, USA)
    # Single variant (11oz white). Wraparound, dye-sublimation. Price in cents.
    # Margin ~42% (Free) / ~49% (Premium) — solid gift SKU.
    "mug": {
        "blueprint_id": 68,
        "print_provider_id": 1,
        "variants": [
            {"id": 33719, "label": "11oz", "price": 2199},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "dye-sublimation",
        "artwork_spec": "2700x1119 px PNG, 2.41:1 wide banner (wraparound). "
                        "Keep main subject centered; edges simple. NOT the poster file.",
    },

    # Unisex Softstyle T-Shirt (Gildan 64000) — provider 99
    # 5 colors x 5 sizes = 25 variants. Front + back print positions.
    # Artwork: 3852x4398 px PNG, transparent, design centered on chest/back.
    "tshirt": {
        "blueprint_id": 145,
        "print_provider_id": 99,
        "positions": ["front", "back"],
        "variants": [
            {"id": 38163, "label": "white_s", "price": 2999},
            {"id": 38177, "label": "white_m", "price": 2999},
            {"id": 38191, "label": "white_l", "price": 2999},
            {"id": 38205, "label": "white_xl", "price": 2999},
            {"id": 38219, "label": "white_2xl", "price": 3299},
            {"id": 38162, "label": "sportgrey_s", "price": 2999},
            {"id": 38176, "label": "sportgrey_m", "price": 2999},
            {"id": 38190, "label": "sportgrey_l", "price": 2999},
            {"id": 38204, "label": "sportgrey_xl", "price": 2999},
            {"id": 38218, "label": "sportgrey_2xl", "price": 3299},
            {"id": 38164, "label": "black_s", "price": 2999},
            {"id": 38178, "label": "black_m", "price": 2999},
            {"id": 38192, "label": "black_l", "price": 2999},
            {"id": 38206, "label": "black_xl", "price": 2999},
            {"id": 38220, "label": "black_2xl", "price": 3299},
            {"id": 63292, "label": "maroon_s", "price": 2999},
            {"id": 63297, "label": "maroon_m", "price": 2999},
            {"id": 63302, "label": "maroon_l", "price": 2999},
            {"id": 63307, "label": "maroon_xl", "price": 2999},
            {"id": 63312, "label": "maroon_2xl", "price": 3299},
            {"id": 64751, "label": "heatherindigo_s", "price": 2999},
            {"id": 64759, "label": "heatherindigo_m", "price": 2999},
            {"id": 64767, "label": "heatherindigo_l", "price": 2999},
            {"id": 64775, "label": "heatherindigo_xl", "price": 2999},
            {"id": 64783, "label": "heatherindigo_2xl", "price": 3299},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "3852x4398 px PNG, transparent background, design "
                        "centered. Front and/or back via images_by_position. "
                        "For dark colors (black/maroon/indigo) use a design with "
                        "a light outline so it stays visible.",
    },
    # Trucker Hat — Duplium (rating 9.1). DTF print, front panel. 1 size, 5 colors.
    # Base+ship high -> margin ~42% at $28.99 (Premium). Price in cents.
    "hat": {
        "blueprint_id": 1128,
        "print_provider_id": 41,
        "variants": [
            {"id": 84650, "label": "Black", "price": 2899},
            {"id": 84651, "label": "Green", "price": 2899},
            {"id": 84652, "label": "Pink", "price": 2899},
            {"id": 104276, "label": "Dark Navy", "price": 2899},
            {"id": 104277, "label": "Royal", "price": 2899},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "dtf",
        "artwork_spec": "1650x750 px PNG, 2.2:1 wide, transparent background, "
                        "badge centered. DTF has white underbase. NOT the poster file.",
    },
    # Embroidery Dad Hat (Adventure Awaits style) — provider 99. Front embroidery.
    # 4 positions available: front, back_hat_embroidery, left/right. Use front (+back optional).
    # TEXT-ONLY embroidery. Margin ~30% at $31.99.
    "hat_vintage_a": {
        "blueprint_id": 1729,
        "print_provider_id": 99,
        "variants": [
            {"id": 118240, "label": "Charcoal Grey", "price": 3199},
            {"id": 118238, "label": "Black", "price": 3199},
            {"id": 118239, "label": "Khaki", "price": 3199},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "positions": ["front", "back_hat_embroidery"],
        "decoration_method": "embroidery",
        "artwork_spec": "TEXT ONLY (no images). Front 1650x600 px, Back 600x300 px. "
                        "Transparent PNG, thread color matches cap (dark cap=cream, light cap=brown).",
    },

    # Embroidery Vintage Cap (Stay Wild style) — provider 99, blueprint 1736.
    # TEXT-ONLY embroidery. Margin ~35% at $35.99.
    "hat_vintage_b": {
        "blueprint_id": 1736,
        "print_provider_id": 99,
        "variants": [
            {"id": 118562, "label": "Black", "price": 3599},
            {"id": 118563, "label": "Maroon", "price": 3599},
            {"id": 118565, "label": "Charcoal Grey", "price": 3599},
            {"id": 118566, "label": "Pink", "price": 3599},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "positions": ["front", "back_hat_embroidery"],
        "decoration_method": "embroidery",
        "artwork_spec": "TEXT ONLY (no images). Front 1650x600 px, Back 600x300 px. "
                        "Transparent PNG, thread color matches cap (dark cap=cream, light/pink cap=brown).",
    },
    # Scented Soy Candle 9oz — provider 219, blueprint 1048. irreverentarts store.
    # Variants = SCENTS (not size). Label print area 900x600. Margin thin (mồi GMC).
    "candle": {
        "blueprint_id": 1048,
        "print_provider_id": 219,
        "variants": [
            {"id": 93541, "label": "Apple Harvest", "price": 2699},
            {"id": 93542, "label": "Cinnamon Vanilla", "price": 2699},
            {"id": 104680, "label": "Christmas Warmth", "price": 2699},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "900x600 px PNG, 3:2 horizontal label (wraps around jar). "
                        "Apothecary/minimalist candle aesthetic — NOT outdoor/Sunfade style.",
    },
    # SPOKE Tumbler 20oz — provider 1, blueprint 353. metalnestdecor store.
    # 1 variant 20oz. Wraparound label 2795x2100. Coffee/drink theme (NOT Sunfade).
    "tumbler": {
        "blueprint_id": 353,
        "print_provider_id": 1,
        "variants": [
            {"id": 44519, "label": "20oz", "price": 4083},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "2795x2100 px PNG, 1.33:1 wraparound. Coffee/drink/minimalist "
                        "aesthetic — NOT outdoor/Sunfade style.",
    },
     # Ceramic Stein Mug 22oz — Duplium (provider 41), blueprint 891.
    # irreverentarts store. Vintage apothecary label, dye-sublimation wrap.
    # Height 6.5in, Diameter 3.7in. Margin: cost 1530 / price 2550 (~40%).
    "stein_mug":{
        "blueprint_id": 891,
        "print_provider_id": 41,
        "variants": [
            {"id": 77101, "label": "22oz", "price": 2550},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "dye-sublimation",
        "artwork_spec": "2250x900 px PNG, 2.5:1 wraparound label, 300dpi, "
                        "full-bleed (opaque, no transparency). Keep text/key art "
                        "within center ~85% (avoid outer edges near handle wrap). "
                        "Vintage apothecary aesthetic — irreverentarts style, "
                        "NOT outdoor/Sunfade or coffee/minimalist style.",
    },
    # Outdoor Pillow (Damask) — provider 10, blueprint 1007. wallstoryco store.
    # Double-sided (front+back use same square image), direct-to-fabric.
    # Square sizes only. Margins: 16x16 ~53%, 18x18 ~48%, 20x20 ~43%.
    "pillow_damask": {
        "blueprint_id": 1007,
        "print_provider_id": 10,
        "positions": ["front", "back"],
        "image_id_fills_all": True,   # <-- thêm dòng này
        "variants": [
            {"id": 79553, "label": "16x16", "price": 3399},
            {"id": 79555, "label": "18x18", "price": 3399},
            {"id": 79556, "label": "20x20", "price": 3399},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 0.9554285714285713,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "direct-to-fabric",
        "artwork_spec": "1254x1254 px min (recommend up to ~3225x3225 px) "
                        "square PNG, full-bleed, no transparency. Same image "
                        "used for both front and back. Bohemian eclectic "
                        "wallstoryco style — damask/jewel-tone or stripe "
                        "patterns, warm layered palette.",
    },
    # Boho Woven Pillow — provider 10, blueprint 1650. wallstoryco store.
    # Single size 17x18, WEAVING decoration (not printed — bold color-block
    # patterns only, no fine detail/gradients). Design auto-duplicates on
    # both sides server-side — only need ONE placeholder ("front").
    # Only "Artwork" finish enabled (Photo finish variant disabled — weaving
    # can't reproduce photorealistic detail well). Margin ~44%.
    "pillow_woven": {
        "blueprint_id": 1650,
        "print_provider_id": 10,
        "variants": [
            {"id": 114341, "label": "17x18", "price": 4799},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 0.9998707008016551,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "weaving",
        "artwork_spec": "1728x1824 px (17x18in @ 96dpi) JPEG or PNG, "
                        "full-bleed, keep key elements within ~90% safe "
                        "zone (avoid outer edge near seam). BOLD GEOMETRIC "
                        "COLOR BLOCKS ONLY — no fine gradients, photoreal "
                        "detail, or thin linework (woven fabric can't "
                        "reproduce them). Bohemian kilim/Aztec-inspired, "
                        "warm earthy wallstoryco palette. Design is applied "
                        "once and auto-duplicated on both sides.",
    },
    # Body Pillow 20x54 (Southwestern Aztec) — provider 10, blueprint 2758.
    # wallstoryco store. Double-sided, SAME image on front+back (explicit
    # 2 placeholders, not auto-duplicated — needs image_id_fills_all).
    # 2 material variants: Fleece (higher price) / Microfiber (default).
    # Margin: Fleece ~40%, Microfiber ~40%.
    "pillow_body": {
        "blueprint_id": 2758,
        "print_provider_id": 10,
        "positions": ["front", "back"],
        "image_id_fills_all": True,
        "variants": [
            {"id": 149506, "label": "Microfiber", "price": 9543},
            {"id": 149505, "label": "Fleece", "price": 10823},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "dye-sublimation",
        "artwork_spec": "8325x3225 px JPEG, ratio ~2.58:1 wide horizontal "
                        "banner, full-bleed, keep key elements within ~90% "
                        "safe zone. Same image covers both front and back. "
                        "Bohemian southwestern/Aztec wallstoryco style — "
                        "pattern must repeat/balance across the FULL WIDTH, "
                        "not centered with empty sides.",
    },
    # Can Glass 16oz (Engraving) — provider 228, blueprint 2759.
    # metalnestdecor store. CO2 laser engraving — single-tone line art only,
    # transparent PNG required (transparent = not engraved). Margin ~40%.
    "can_glass_engraved": {
        "blueprint_id": 2759,
        "print_provider_id": 228,
        "variants": [
            {"id": 149511, "label": "16oz", "price": 2425},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "engraving",
        "artwork_spec": "1200x1800 px PNG with FULL TRANSPARENCY in "
                        "non-engraved areas (transparent = not etched, "
                        "NOT white). Pure black line art / silhouette only "
                        "— no color, no gradients, no photorealistic "
                        "shading. Keep linework moderately bold (avoid "
                        "hairline detail, blurs on glass). metalnestdecor "
                        "modern industrial/metal line-art style, centered "
                        "within ~80% of canvas.",
    },
    

}





# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def _headers() -> dict[str, str]:
    if not TOKEN:
        raise RuntimeError("PRINTIFY_TOKEN env var is not set.")
    return {"Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "SunfadeMCP/1.0"}


async def _request(method: str, path: str, json: Any = None,
                   retries: int = 4) -> Any:
    url = f"{API_BASE}{path}"
    delay = 2.0
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(retries):
            r = await client.request(method, url, headers=_headers(), json=json)
            if r.status_code == 429:
                # respect Retry-After, else exponential backoff
                wait = float(r.headers.get("Retry-After", delay))
                await asyncio.sleep(wait)
                delay *= 2
                continue
            if r.status_code >= 500:
                await asyncio.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json() if r.text else {}
    raise RuntimeError(f"Printify API failed after {retries} retries: {method} {path}")


def _shop(shop_id: str | int | None) -> str:
    s = str(shop_id) if shop_id else DEFAULT_SHOP
    if not s:
        raise RuntimeError("No shop_id given and PRINTIFY_SHOP_ID not set.")
    return s


# ---------------------------------------------------------------------------
# TOOLS
# ---------------------------------------------------------------------------
@mcp.tool()
async def list_shops() -> list[dict]:
    """List all shops connected to this Printify account (id, title, channel)."""
    return await _request("GET", "/shops.json")


@mcp.tool()
def list_templates() -> dict:
    """List available SKU templates and their expected artwork spec."""
    return {name: {"blueprint_id": t["blueprint_id"],
                   "print_provider_id": t["print_provider_id"],
                   "sizes": [v["label"] for v in t["variants"]],
                   "artwork_spec": t["artwork_spec"]}
            for name, t in TEMPLATES.items()}


@mcp.tool()
async def upload_artwork(image_url: str, file_name: str) -> dict:
    """
    Upload artwork to Printify from a public HTTPS URL.
    Returns the uploaded image object; keep the `id` for create_from_template.
    """
    payload = {"file_name": file_name, "url": image_url}
    res = await _request("POST", "/uploads/images.json", json=payload)
    return {"id": res.get("id"), "file_name": res.get("file_name"),
            "width": res.get("width"), "height": res.get("height")}


@mcp.tool()
async def upload_artwork_file(file_path: str, file_name: str) -> dict:
    """
    Upload artwork to Printify from a LOCAL file on this machine (base64).
    No public URL needed. Use an absolute path, e.g.
    C:\\Users\\ADMIN\\Desktop\\sunfade_designs\\yosemite.png
    Returns the uploaded image object; keep the `id` for create_from_template.
    """
    import base64
    with open(file_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    payload = {"file_name": file_name, "contents": b64}
    res = await _request("POST", "/uploads/images.json", json=payload)
    return {"id": res.get("id"), "file_name": res.get("file_name"),
            "width": res.get("width"), "height": res.get("height")}


@mcp.tool()
async def create_from_template(
    template_name: str,
    image_id: str | None = None,
    title: str = "",
    description: str = "",
    tags: list[str] | None = None,
    shop_id: str | int | None = None,
    price_overrides: dict[str, int] | None = None,
    images_by_position: dict[str, str] | None = None,
) -> dict:
    """
    Create a Printify product from a stored SKU template.

    template_name : key from list_templates (e.g. 'poster_matte_2x3', 'tshirt')
    image_id      : image id for the FRONT (single-position products:
                    poster, canvas, sticker, mug)
    images_by_position : for multi-position products (e.g. tshirt), a dict like
                    {"front": "<img_id>", "back": "<img_id>"}.
                    Positions not given are left blank. Overrides image_id.
    title/description/tags : product content (SEO)
    price_overrides: optional {size_label: price_cents}
    Returns the created product id (product is UNPUBLISHED / draft).
    """
    tpl = TEMPLATES.get(template_name)
    if not tpl:
        raise ValueError(f"Unknown template '{template_name}'. "
                         f"Available: {list(TEMPLATES)}")
    shop = _shop(shop_id)
    overrides = price_overrides or {}

    variants = []
    variant_ids = []
    for v in tpl["variants"]:
        price = overrides.get(v["label"], v["price"])
        variants.append({"id": v["id"], "price": price, "is_enabled": True})
        variant_ids.append(v["id"])

    ph = tpl["placeholder"]

    # Decide which positions to fill.
    # - multi-position template: use images_by_position
    # - single-position template: use image_id on "front"
    positions = tpl.get("positions", ["front"])
    if images_by_position:
        pos_images = images_by_position
    else:
        if not image_id:
            raise ValueError("Provide image_id (single) or images_by_position (multi).")
        # Templates can opt in to "1 image → all positions" via the
        # "image_id_fills_all" flag. Default (no flag, e.g. tshirt) keeps
        # old behavior: image_id only fills "front".
        if tpl.get("image_id_fills_all"):
            pos_images = {pos: image_id for pos in positions}
        else:
            pos_images = {"front": image_id}
    placeholders = []
    for pos in positions:
        img = pos_images.get(pos)
        if not img:
            continue  # leave this position blank
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

    if not placeholders:
        raise ValueError("No artwork provided for any print position.")

    print_areas = [{
        "variant_ids": variant_ids,
        "placeholders": placeholders,
    }]

    payload = {
        "title": title,
        "description": description,
        "tags": tags or [],
        "blueprint_id": tpl["blueprint_id"],
        "print_provider_id": tpl["print_provider_id"],
        "variants": variants,
        "print_areas": print_areas,
    }
    res = await _request("POST", f"/shops/{shop}/products.json", json=payload)
    return {"product_id": res.get("id"), "title": res.get("title"),
            "status": "draft (unpublished)"}


@mcp.tool()
async def batch_create(
    template_name: str,
    items: list[dict],
    shop_id: str | int | None = None,
) -> dict:
    """
    Create many products from one template in a single call.
    Each item = {image_id, title, description, tags?}.
    Includes pacing to stay well under rate limits. Returns per-item results.
    """
    results = []
    for i, it in enumerate(items):
        try:
            r = await create_from_template(
                template_name=template_name,
                image_id=it["image_id"],
                title=it["title"],
                description=it.get("description", ""),
                tags=it.get("tags", []),
                shop_id=shop_id,
            )
            results.append({"ok": True, **r})
        except Exception as e:
            results.append({"ok": False, "title": it.get("title"), "error": str(e)})
        # pace: ~1 product/sec — far under the 600 req/min global limit
        if i < len(items) - 1:
            await asyncio.sleep(1.0)
    ok = sum(1 for r in results if r.get("ok"))
    return {"created": ok, "failed": len(results) - ok, "results": results}


@mcp.tool()
async def scan_folder(folder_path: str) -> dict:
    """
    List all image files (.png/.jpg/.jpeg) in a local folder, WITHOUT uploading.
    Use this first so the caller can see what will be processed and can write
    a title/description/tags for each file before calling batch_from_folder.
    Returns file names + full paths. Names are meant to encode the subject
    (e.g. 'yosemite.png', 'zion.png') so titles/tags can be written per file.
    """
    import os
    exts = (".png", ".jpg", ".jpeg")
    if not os.path.isdir(folder_path):
        raise ValueError(f"Not a folder: {folder_path}")
    files = []
    for name in sorted(os.listdir(folder_path)):
        if name.lower().endswith(exts):
            files.append({"file_name": name,
                          "file_path": os.path.join(folder_path, name)})
    return {"folder": folder_path, "count": len(files), "files": files}


@mcp.tool()
async def batch_from_folder(
    template_name: str,
    items: list[dict],
    shop_id: str | int | None = None,
) -> dict:
    """
    Upload local image files AND create products from them in one call.
    Run scan_folder first, then pass one item per file:
      item = {file_path, file_name, title, description, tags?}
    For each item this uploads the local file (base64) then creates a draft
    product from the template. Paced to stay under rate limits.
    Returns per-item results with product_id or error.
    """
    results = []
    for i, it in enumerate(items):
        try:
            img = await upload_artwork_file(
                file_path=it["file_path"],
                file_name=it.get("file_name", "artwork.png"),
            )
            r = await create_from_template(
                template_name=template_name,
                image_id=img["id"],
                title=it["title"],
                description=it.get("description", ""),
                tags=it.get("tags", []),
                shop_id=shop_id,
            )
            results.append({"ok": True, "file": it.get("file_name"),
                            "image_id": img["id"], **r})
        except Exception as e:
            results.append({"ok": False, "file": it.get("file_name"),
                            "title": it.get("title"), "error": str(e)})
        if i < len(items) - 1:
            await asyncio.sleep(1.0)  # pacing
    ok = sum(1 for r in results if r.get("ok"))
    return {"created": ok, "failed": len(results) - ok, "results": results}


@mcp.tool()
async def publish_product(product_id: str, shop_id: str | int | None = None) -> dict:
    """
    Publish a product to its connected sales channel (e.g. Shopify).
    NOTE: the shop must be a real connected storefront, not the API
    'custom_integration' placeholder shop.
    """
    shop = _shop(shop_id)
    payload = {"title": True, "description": True, "images": True,
               "variants": True, "tags": True, "keyFeatures": True,
               "shipping_template": True}
    await _request("POST", f"/shops/{shop}/products/{product_id}/publish.json",
                   json=payload)
    return {"product_id": product_id, "status": "publish requested"}


@mcp.tool()
async def get_product(product_id: str, shop_id: str | int | None = None) -> dict:
    """Fetch a product to verify it (title, variants enabled, images)."""
    shop = _shop(shop_id)
    p = await _request("GET", f"/shops/{shop}/products/{product_id}.json")
    return {
        "id": p.get("id"),
        "title": p.get("title"),
        "visible": p.get("visible"),
        "enabled_variants": [v["title"] for v in p.get("variants", [])
                             if v.get("is_enabled")],
        "images": len(p.get("images", [])),
    }


@mcp.tool()
async def list_print_providers(blueprint_id: int) -> list[dict]:
    """List print providers for a blueprint (used when adding a new SKU template)."""
    return await _request("GET", f"/catalog/blueprints/{blueprint_id}/print_providers.json")


if __name__ == "__main__":
    mcp.run()