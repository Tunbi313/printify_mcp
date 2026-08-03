"""
SKU templates for Printify products.

This is a standalone copy of the TEMPLATES dict from ../server.py, kept in
sync by hand. It is duplicated (not imported) so this Django app has zero
dependency on the MCP server or the `mcp` package.
"""

from typing import Any

TEMPLATES: dict[str, dict[str, Any]] = {
    "poster_matte_2x3": {
        "label": "Satin & Archival Matte Poster",
        "blueprint_id": 1309,
        "print_provider_id": 234,
        "variants": [
            {"id": 99118, "label": "12x18", "price": 2900},
            {"id": 99132, "label": "20x30", "price": 4200},
            {"id": 99138, "label": "24x36", "price": 5200},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 0.9956715564046932,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "7200x10800 px PNG, 2:3, sRGB, full-bleed",
    },
    "canvas_2x3": {
        "label": "Classic Stretched Canvas (1.5\")",
        "blueprint_id": 1238,
        "print_provider_id": 48,
        "variants": [
            {"id": 93932, "label": "12x18", "price": 6900},
            {"id": 93937, "label": "16x24", "price": 8900},
            {"id": 93942, "label": "20x30", "price": 10900},
            {"id": 93946, "label": "24x36", "price": 12900},
        ],
        "placeholder": {"x": 0.5, "y": 0.4999, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "artwork_spec": "7200x10800 px PNG, 2:3, sRGB, full-bleed "
                        "(same file as poster; keep key detail away from canvas fold edges)",
    },
    "sticker": {
        "label": "Square Sticker (die-cut, UV)",
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
    "mug": {
        "label": "Ceramic Mug 11oz (dye-sub)",
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
    "tshirt": {
        "label": "Unisex Softstyle Tee (Gildan 64000)",
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
    "hat": {
        "label": "Trucker Hat (DTF)",
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
    "hat_vintage_a": {
        "label": "Embroidery Dad Hat",
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
    "hat_vintage_b": {
        "label": "Embroidery Vintage Cap",
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
    "candle": {
        "label": "Scented Soy Candle 9oz",
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
    "tumbler": {
        "label": "SPOKE Tumbler 20oz",
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
    "stein_mug": {
        "label": "Ceramic Stein Mug 22oz (dye-sub)",
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
    "pillow_damask": {
        "label": "Outdoor Pillow, double-sided",
        "blueprint_id": 1007,
        "print_provider_id": 10,
        "positions": ["front", "back"],
        "image_id_fills_all": True,
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
    "pillow_woven": {
        "label": "Boho Woven Pillow",
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
    "pillow_body": {
        "label": "Body Pillow 20x54, double-sided",
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
    "can_glass_engraved": {
        "label": "Can Glass 16oz (laser engraving)",
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
