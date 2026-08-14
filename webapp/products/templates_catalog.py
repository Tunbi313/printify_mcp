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
        "target_width": 7200,
        "target_height": 10800,
        "sample_prompt": "Retro national park travel poster of [địa danh outdoor], "
                          "bold sunburst sky, warm earthy palette (rust, mustard, "
                          "teal), flat vector illustration, vintage WPA poster "
                          "style, no text, no logos, full-bleed edge-to-edge "
                          "composition, 2:3 vertical portrait.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Materials: 210 gsm satin paper or 230 gsm archival matte paper</li>"
            "<li>Two paper finishes to choose from: satin or archival matte</li>"
            "<li>Available in 3 sizes, vertical 2:3 format</li>"
            "<li>Assembled in the USA from globally sourced parts</li>"
            "</ul>"
            "<p>Bring your artwork to life in high-end detail with a quality "
            "poster print. Available in 210gsm satin paper or 230gsm archival "
            "matte paper, these prints look stunning up close and hold up "
            "for years. Choose from three vertical sizes — 12x18, 20x30, or "
            "24x36 — to fit your space.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>If the poster gathers any dust, wipe it gently with a clean, "
            "dry cloth.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>Width</th><th>Height</th></tr>"
            "<tr><td>12x18</td><td>12 in</td><td>18 in</td></tr>"
            "<tr><td>20x30</td><td>20 in</td><td>30 in</td></tr>"
            "<tr><td>24x36</td><td>24 in</td><td>36 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "poster", "wall art", "national park", "retro", "vintage",
            "home decor", "nature", "landscape", "travel poster", "outdoor",
            "gift", "art print", "mountain", "adventure",
        ],
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
        "target_width": 7200,
        "target_height": 10800,
        "sample_prompt": "Retro national park travel poster of [địa danh outdoor], "
                          "bold sunburst sky, warm earthy palette (rust, mustard, "
                          "teal), flat vector illustration, vintage WPA poster "
                          "style, no text, no logos, key details kept away from "
                          "outer edges, 2:3 vertical portrait.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: 100% polyester canvas on a pine wood frame</li>"
            "<li>Available in 4 sizes, vertical 2:3 format</li>"
            "<li>Hanging kit included (2 screws + sawtooth hanger)</li>"
            "</ul>"
            "<p>Make your art feel at home on this polyester canvas. Each "
            "canvas comes stretched on a beautiful pine wood frame, ready to "
            "hang out of the box — a highly giftable, ready-to-display "
            "piece.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>If the canvas gathers any dust, wipe it off gently with a "
            "clean, damp cloth.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>Width</th><th>Height</th></tr>"
            "<tr><td>12x18</td><td>12 in</td><td>18 in</td></tr>"
            "<tr><td>16x24</td><td>16 in</td><td>24 in</td></tr>"
            "<tr><td>20x30</td><td>20 in</td><td>30 in</td></tr>"
            "<tr><td>24x36</td><td>24 in</td><td>36 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "canvas", "wall art", "national park", "retro", "vintage",
            "home decor", "nature", "landscape", "outdoor", "gift",
            "canvas print", "mountain", "framed art", "adventure",
        ],
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
        "target_width": 1800,
        "target_height": 1800,
        "transparent_bg": True,
        "sample_prompt": "Die-cut badge sticker illustration of [chủ đề outdoor "
                          "icon], bold clean outline, flat vector style, vibrant "
                          "retro color palette, centered composition with even "
                          "margin around edges, transparent background, no "
                          "gradients, no text.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: 100% vinyl with 3M glue backing</li>"
            "<li>Four sizes to choose from</li>"
            "<li>Glossy finish, grey adhesive backing</li>"
            "<li>For indoor use — not waterproof</li>"
            "</ul>"
            "<p>You cannot go wrong with a classic square-style sticker. "
            "Clean, crisp edges and a simple-but-elegant look make this the "
            "most straightforward and popular option.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Use a soft, clean, dry cloth to gently brush any dust or "
            "dirt off the sticker outwards from the center.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>Width</th><th>Height</th></tr>"
            "<tr><td>2x2</td><td>2 in</td><td>2 in</td></tr>"
            "<tr><td>3x3</td><td>3 in</td><td>3 in</td></tr>"
            "<tr><td>4x4</td><td>4 in</td><td>4 in</td></tr>"
            "<tr><td>6x6</td><td>6 in</td><td>6 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "sticker", "vinyl sticker", "national park", "outdoor", "retro",
            "badge", "laptop sticker", "water bottle sticker", "hiking",
            "adventure", "nature", "gift", "decal",
        ],
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
        "target_width": 2700,
        "target_height": 1119,
        "sample_prompt": "Wraparound mug graphic of [chủ đề outdoor scene], retro "
                          "travel poster illustration style, warm earthy palette, "
                          "wide horizontal panoramic composition, subject centered, "
                          "simple seamless edges, no text near the handle seam.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>White ceramic, 11oz (0.33l)</li>"
            "<li>Rounded corners, C-handle</li>"
            "<li>Microwave and dishwasher safe</li>"
            "</ul>"
            "<p>A classic bestseller — this custom mug is for those who "
            "truly believe there's nothing better than a cup of your "
            "favorite beverage in hand. Made of durable white ceramic with "
            "vivid, ORCA-coated print quality, it's the perfect gift for "
            "every true coffee or tea lover.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Clean in the dishwasher or wash by hand with warm water and "
            "dish soap.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Height</td><td>3.74 in</td></tr>"
            "<tr><td>Diameter</td><td>3.15 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "mug", "coffee mug", "national park", "outdoor", "retro",
            "gift", "travel mug", "camping", "hiking", "nature lover gift",
            "ceramic mug", "adventure",
        ],
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
        "target_width": 3852,
        "target_height": 4398,
        "transparent_bg": True,
        "sample_prompt": "Front chest graphic design of [chủ đề outdoor icon], "
                          "bold retro illustration, flat vector style with a "
                          "light outline for visibility on dark fabric, centered "
                          "composition, transparent background, no background "
                          "elements, no text.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>100% ring-spun cotton (4.5 oz/153 g/m²), semi-fitted classic shape</li>"
            "<li>Available in 5 colors: White, Sport Grey, Black, Maroon, Heather Indigo</li>"
            "<li>5 sizes: S, M, L, XL, 2XL</li>"
            "<li>Ribbed knit collar, shoulder tape for durability</li>"
            "</ul>"
            "<p>Soft, breathable, and true to size — the Gildan Softstyle "
            "64000 combines a modern comfort fit with everyday durability. "
            "Solid colors are 100% cotton, while heather colors are a "
            "cotton/polyester blend for extra softness.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Machine wash cold (max 30°C) with similar colors. Do not "
            "bleach. Tumble dry low. Iron low if needed. Do not dryclean.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>Width</th><th>Length</th></tr>"
            "<tr><td>S</td><td>18 in</td><td>28 in</td></tr>"
            "<tr><td>M</td><td>20 in</td><td>29 in</td></tr>"
            "<tr><td>L</td><td>22 in</td><td>30 in</td></tr>"
            "<tr><td>XL</td><td>24 in</td><td>31 in</td></tr>"
            "<tr><td>2XL</td><td>26 in</td><td>32 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "t-shirt", "graphic tee", "national park", "outdoor", "retro",
            "hiking shirt", "camping", "adventure", "nature lover",
            "unisex shirt", "gift", "vintage tee",
        ],
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
        "target_width": 1650,
        "target_height": 750,
        "transparent_bg": True,
        "sample_prompt": "Small badge-style patch design of [chủ đề outdoor icon], "
                          "bold flat vector illustration, thick clean outlines, "
                          "high contrast colors, compact centered composition, "
                          "transparent background, wide badge layout, no text.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: 100% polyester foam front with 100% nylon mesh back</li>"
            "<li>One size fits most (22.8&quot;/58cm)</li>"
            "<li>5 colorways: Black, Green, Pink, Dark Navy, Royal</li>"
            "<li>Adjustable plastic snap closure (20&quot;–24&quot;)</li>"
            "<li>Six rows of stitching on the visor</li>"
            "</ul>"
            "<p>Trucker caps are perfect for everyday wear — versatile and easy "
            "to dress up or down. The 100% polyester front and 100% nylon mesh "
            "back make this cap durable through regular wear and tear, and "
            "your design is printed with sharp, vivid, fade-resistant DTF "
            "print. Pick any colorway and make it your own.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Hand wash with warm water and mild soap. No need to soak the "
            "whole cap — use a soft-bristled brush on tough spots.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Circumference</td><td>22.8 in / 58 cm</td></tr>"
            "<tr><td>Crown height</td><td>3.5 in</td></tr>"
            "<tr><td>Bill length</td><td>3.03 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "trucker hat", "cap", "national park", "outdoor", "retro",
            "hiking", "camping", "adventure", "badge hat", "gift",
            "snapback",
        ],
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
        "sample_prompt": "Minimalist single-color embroidered lettering, slogan "
                          "'ADVENTURE AWAITS' (or similar outdoor slogan), thin "
                          "elegant serif or script typography, no icons or "
                          "images, transparent background, thread color cream "
                          "on dark cap / brown on light cap.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>100% preshrunk cotton twill</li>"
            "<li>Unstructured, six-panel, low-profile, soft crown</li>"
            "<li>6 sewn eyelets, 4 stitched rows on the brim</li>"
            "<li>Adjustable hook-and-loop closure — one size fits most</li>"
            "<li>3 colors: Charcoal Grey, Black, Khaki</li>"
            "</ul>"
            "<p>The Distressed Dad Hat brings effortless style and comfort "
            "to your casual look. Made from 100% preshrunk cotton twill, "
            "this relaxed, low-profile hat features a custom embroidered "
            "design with the adjustable hook-and-loop closure ensuring a "
            "perfect fit.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Use warm water and dish soap to clean spots off your hat. "
            "It's not necessary to soak the whole item — for hard-to-clean "
            "spots, use a soft-bristled brush.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Circumference</td><td>19.02–22.05 in</td></tr>"
            "<tr><td>Height</td><td>5.00 in</td></tr>"
            "<tr><td>Visor width</td><td>2.60 in</td></tr>"
            "<tr><td>Visor length</td><td>7.78 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "dad hat", "embroidered hat", "outdoor", "adventure", "hiking",
            "camping", "vintage cap", "gift", "unstructured hat",
            "nature lover",
        ],
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
        "sample_prompt": "Minimalist single-color embroidered lettering, slogan "
                          "'STAY WILD' (or similar outdoor slogan), thin elegant "
                          "vintage script typography, no icons or images, "
                          "transparent background, thread color cream on dark "
                          "cap / brown on pink cap.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>100% cotton twill, 6-panel unstructured cap, low profile</li>"
            "<li>6 sewn eyelets, black sweatband</li>"
            "<li>Metal snap buckle with antique brass finish</li>"
            "<li>Washed-out vintage effect — one size fits most</li>"
            "<li>4 colors: Black, Maroon, Charcoal Grey, Pink</li>"
            "</ul>"
            "<p>Add a touch of timeless style with the Vintage Cap. Crafted "
            "from 100% cotton twill, this 6-panel unstructured cap has a "
            "relaxed, laid-back look. The washed-out vintage effect gives "
            "each cap slight, one-of-a-kind color variation — that's the "
            "charm, not a defect.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Use warm water and dish soap to clean spots off your hat. "
            "It's not necessary to soak the whole item — for hard-to-clean "
            "spots, use a soft-bristled brush.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Circumference</td><td>18.50–23.23 in</td></tr>"
            "<tr><td>Height</td><td>4.33 in</td></tr>"
            "<tr><td>Width</td><td>7.09 in</td></tr>"
            "<tr><td>Panel height</td><td>2.83 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "vintage cap", "embroidered hat", "outdoor", "adventure",
            "hiking", "camping", "distressed cap", "gift", "nature lover",
            "stay wild",
        ],
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
        "target_width": 900,
        "target_height": 600,
        "sample_prompt": "Apothecary-style candle label design, minimalist "
                          "botanical illustration, [tên mùi hương] theme, muted "
                          "earthy palette (sage, cream, terracotta), elegant "
                          "serif typography, horizontal wraparound layout, no "
                          "outdoor/national park elements.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Materials: 100% natural soy wax, 100% cotton wick, glass jar</li>"
            "<li>One size: 2.8&quot; x 3.5&quot; (7.1cm x 8.9cm)</li>"
            "<li>Burn time: 30–40 hours</li>"
            "<li>Glossy permanent adhesive label</li>"
            "<li>3 scents: Apple Harvest, Cinnamon Vanilla, Christmas Warmth</li>"
            "</ul>"
            "<p>Packed with immersive aromas, these scented candles come in "
            "a 9oz glass jar that's reusable once the candle is gone. Made "
            "with 100% natural soy wax and a 100% cotton wick, each candle "
            "features a permanent adhesive label where your custom designs "
            "can come alive.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Keep the burning wick within sight. Keep away from children "
            "and pets. Never burn the candle near flammable items.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Diameter</td><td>2.80 in</td></tr>"
            "<tr><td>Height</td><td>3.10 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "scented candle", "soy candle", "apothecary", "gift",
            "home fragrance", "minimalist candle", "glass jar candle",
            "cozy home", "self care", "aromatherapy",
        ],
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
        "target_width": 2795,
        "target_height": 2100,
        "sample_prompt": "Wraparound tumbler graphic, minimalist coffee/drink "
                          "line art illustration, [chủ đề] theme, muted modern "
                          "palette (charcoal, cream, warm brown), horizontal "
                          "seamless wrap composition, no outdoor/national park "
                          "elements.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Materials: stainless steel body, plastic and rubber lid</li>"
            "<li>One size: 20oz (0.59l)</li>"
            "<li>Vacuum insulated double-wall steel body</li>"
            "<li>Clear push-on lid with rubber gasket, glossy finish</li>"
            "</ul>"
            "<p>This tumbler is an excellent choice for daily use to keep "
            "20oz of your favorite beverages hot or cold thanks to the "
            "double-wall insulation. The stainless steel construction "
            "provides excellent durability and it's dishwasher-safe for "
            "added convenience.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Clean in the dishwasher or wash by hand with warm water and "
            "dish soap.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Height</td><td>6.77 in</td></tr>"
            "<tr><td>Diameter</td><td>2.83–3.39 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "tumbler", "insulated tumbler", "coffee tumbler", "travel mug",
            "minimalist", "gift", "stainless steel tumbler",
            "hot cold drink", "everyday use", "drinkware",
        ],
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
        "target_width": 2250,
        "target_height": 900,
        "sample_prompt": "Vintage apothecary-style wraparound label design for a "
                          "stein mug, [chủ đề] motif, muted earthy palette, "
                          "elegant vintage typography, full-bleed opaque "
                          "background (no transparency), key art kept away from "
                          "outer edges near the handle wrap.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: durable white ceramic</li>"
            "<li>One size: 22oz</li>"
            "<li>Scratch-resistant, dishwasher and microwave safe</li>"
            "<li>Ribbed outlines along the mug's top, bottom and handle</li>"
            "</ul>"
            "<p>Packed with class and a timeless design, this stein mug "
            "awaits your custom designs to become a true work of art. Made "
            "with durable white ceramic, it's both scratch-resistant for "
            "long-lasting looks and dishwasher-safe for convenient "
            "cleaning.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Clean in the dishwasher or wash by hand with warm water and "
            "dish soap.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Height</td><td>6.50 in</td></tr>"
            "<tr><td>Diameter</td><td>3.70 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "stein mug", "ceramic mug", "apothecary", "vintage mug", "gift",
            "coffee mug", "beer mug", "unique mug", "home decor",
        ],
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
        "artwork_spec": "2625x2625 px square PNG, full-bleed, no transparency. "
                        "Same image used for both front and back. Bohemian eclectic "
                        "wallstoryco style — damask/jewel-tone or stripe "
                        "patterns, warm layered palette.",
        "target_width": 2625,
        "target_height": 2625,
        "sample_prompt": "Bohemian eclectic damask or jewel-tone pattern, warm "
                          "layered color palette (burgundy, gold, emerald, "
                          "terracotta), symmetrical repeating motif, square "
                          "full-bleed composition, no transparency, no photoreal "
                          "elements.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: 100% spun polyester</li>"
            "<li>Direct-to-fabric printing, double-sided print</li>"
            "<li>UV ray and water-resistant, sewn seam closure</li>"
            "<li>Available in 3 sizes: 16x16, 18x18, 20x20</li>"
            "</ul>"
            "<p>These custom outdoor pillows are the perfect aesthetic "
            "uplift for any patio or outdoor space. UV-resistant printed "
            "colors stay vibrant for a long time, and the water/mildew-"
            "resistant nature makes them perfect for carefree decorating.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Spot clean only, or dryclean.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>Width</th><th>Height</th></tr>"
            "<tr><td>16x16</td><td>16 in</td><td>16 in</td></tr>"
            "<tr><td>18x18</td><td>18 in</td><td>18 in</td></tr>"
            "<tr><td>20x20</td><td>20 in</td><td>20 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "outdoor pillow", "throw pillow", "bohemian decor", "home decor",
            "patio decor", "jewel tone", "damask pattern", "gift",
            "double sided pillow",
        ],
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
        "target_width": 1728,
        "target_height": 1824,
        "sample_prompt": "Bold geometric color-block pattern, kilim/Aztec-"
                          "inspired bohemian design, warm earthy palette, flat "
                          "solid color shapes only — no gradients, no fine "
                          "linework, no photoreal detail, balanced square-ish "
                          "full-bleed composition.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Woven from 58% cotton and 42% polyester</li>"
            "<li>One size: 17&quot; x 18&quot; (43.2cm x 45.7cm)</li>"
            "<li>Polyester fiber filling, blown and closed</li>"
            "<li>Double-sided design, duplicated on both sides automatically</li>"
            "</ul>"
            "<p>These woven pillows make a cozy addition to any living "
            "space. They feature a double-sided design, poly fill, and are "
            "blown and closed — the thread is woven tightly to preserve the "
            "details of the design, creating an heirloom-quality piece of "
            "home décor.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Spot clean or dry clean only.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Width</td><td>17 in</td></tr>"
            "<tr><td>Length</td><td>18 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "woven pillow", "throw pillow", "bohemian decor", "home decor",
            "kilim pattern", "geometric pillow", "gift", "aztec pattern",
        ],
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
        "target_width": 8325,
        "target_height": 3225,
        "sample_prompt": "Bohemian southwestern/Aztec pattern that repeats "
                          "seamlessly across a wide horizontal banner, warm "
                          "earthy palette, balanced symmetrical composition "
                          "spanning the full width, no empty side margins, no "
                          "photoreal elements.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: polyester fleece or microfiber cover</li>"
            "<li>One size: 20&quot; x 54&quot; (50.8cm x 137.16cm)</li>"
            "<li>Double-sided print, removable cover, hidden zipper</li>"
            "</ul>"
            "<p>Offer the comfort and support your customers deserve with "
            "this all-over print body pillow. Ideal for side sleepers, it "
            "also makes a great oversized lumbar bolster for a couch or "
            "living room.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Remove the pillow cover and pre-treat stains with a soft "
            "cloth or bristle brush soaked in warm, soapy water. Spot clean "
            "or dry clean. Machine wash cold (max 30°C), gentle cycle, mild "
            "detergent — cover only, not the filling. Do not bleach. Tumble "
            "dry low heat — cover only.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><td>Width</td><td>20 in</td></tr>"
            "<tr><td>Length</td><td>54 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "body pillow", "bohemian decor", "home decor",
            "southwestern pattern", "aztec pattern", "comfort pillow",
            "gift", "side sleeper pillow",
        ],
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
        "target_width": 1200,
        "target_height": 1800,
        "transparent_bg": True,
        "sample_prompt": "Black and white line art silhouette of [chủ đề], "
                          "modern industrial/metal aesthetic, bold clean "
                          "linework (no hairline detail), centered composition, "
                          "transparent background, no color, no gradients, no "
                          "photorealistic shading.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: clear glass</li>"
            "<li>One size: 16oz (0.473l)</li>"
            "<li>Secure lid with reusable straw</li>"
            "<li>Dishwasher safe</li>"
            "</ul>"
            "<p>A fun, contemporary can-shaped glass that fits comfortably "
            "in your hand. Your design is laser-etched into the glass for "
            "an elegant, fade-proof, personalized touch — great for iced "
            "coffees, smoothies, cocktails, and more.</p>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Clean in the dishwasher (top rack) or wash by hand with "
            "warm water and dish soap.</p>"
        ),
        "default_tags": [
            "etched glass", "engraved glass", "can glass", "industrial decor",
            "modern gift", "drinkware", "personalized gift", "line art",
            "minimalist glass",
        ],
    },
    "temp_tattoo": {
        "label": "Temporary Tattoo Flash Sheet (DTF)",
        "blueprint_id": 2036,
        "print_provider_id": 69,
        "variants": [
            {"id": 125280, "label": "S_2x3", "price": 503},
            {"id": 125279, "label": "M_3x4", "price": 610},
            {"id": 125278, "label": "L_4x6", "price": 828},
            {"id": 125281, "label": "XL_7.75x7.75", "price": 1330},
            {"id": 125282, "label": "XXL_11.75x11.75", "price": 2517},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 0.5779911111111111,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "dtf",
        "artwork_spec": "2048x2048 px PNG, FULLY TRANSPARENT background. "
                        "Multiple small motifs (4-6) scattered as a flash "
                        "sheet with clear space between each — printed as "
                        "ONE sheet covering all sizes (customer cuts apart "
                        "designs themselves). Fine-line black ink or "
                        "watercolor, Sunfade vintage outdoor/national park "
                        "style — mountains, compass, botanical sprigs, "
                        "moon/stars, tent/campfire.",
        "target_width": 2048,
        "target_height": 2048,
        "transparent_bg": True,
        "sample_prompt": "Temporary tattoo flash sheet with 4-6 small "
                          "scattered motifs — mountains, compass, botanical "
                          "sprigs, moon and stars, tent and campfire — "
                          "fine-line black ink or light watercolor, Sunfade "
                          "vintage outdoor/national park style, clear space "
                          "between each design, no color blocks, transparent "
                          "background, no text.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: water-transfer temporary tattoo paper</li>"
            "<li>Lasts 3-5 days with normal wear</li>"
            "<li>Available in 5 sizes, from 2x3in up to 11.75x11.75in</li>"
            "<li>Waterproof and smudge-resistant once applied</li>"
            "</ul>"
            "<p>A flash sheet of small vintage outdoor designs, ready to "
            "share the adventure on your skin. Apply with water in under a "
            "minute — no adhesive residue, no commitment. Great for "
            "festivals, hikes, or just trying out a design before it's "
            "permanent.</p>"
            "<p><strong>Application</strong></p>"
            "<p>Cut out the design, remove the clear film, press face-down "
            "onto clean skin, hold a wet cloth over the backing for "
            "30 seconds, then peel off the paper.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>Width</th><th>Height</th></tr>"
            "<tr><td>S</td><td>2 in</td><td>3 in</td></tr>"
            "<tr><td>M</td><td>3 in</td><td>4 in</td></tr>"
            "<tr><td>L</td><td>4 in</td><td>6 in</td></tr>"
            "<tr><td>XL</td><td>7.75 in</td><td>7.75 in</td></tr>"
            "<tr><td>XXL</td><td>11.75 in</td><td>11.75 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "temporary tattoo", "flash sheet", "national park", "outdoor",
            "vintage tattoo", "fine line tattoo", "festival", "hiking",
            "adventure", "gift", "fake tattoo", "nature lover",
        ],
    },
    "beach_towel": {
        "label": "Beach Towel (dye-sub)",
        "blueprint_id": 352,
        "print_provider_id": 99,
        "variants": [
            {"id": 44444, "label": "30x60", "price": 3882},
            {"id": 44445, "label": "36x72", "price": 4965},
        ],
        "placeholder": {"x": 0.5, "y": 0.5, "scale": 1.0,
                         "angle": 0, "flipX": False, "flipY": False},
        "decoration_method": "dye-sublimation",
        "artwork_spec": "18900x9900 px (30x60) or 22200x11400 px (36x72), "
                        "300dpi, ratio ~1.95:1 wide landscape, full-bleed, "
                        "keep key elements within ~85% safe zone. Vibrant "
                        "coastal/beach sunset style — metalsignhub. NOTE: "
                        "ChatGPT/DALL-E output (~1500-1800px) must be "
                        "upscaled significantly before upload — plan for "
                        "an upscale step (see upscale_image tool).",
        "target_width": 22200,
        "target_height": 11400,
        "sample_prompt": "Vibrant coastal beach sunset scene, [chủ đề bãi "
                          "biển/hoàng hôn], warm saturated palette (coral, "
                          "gold, turquoise), wide horizontal landscape "
                          "composition, full-bleed edge-to-edge, no text, "
                          "no logos.",
        "default_description": (
            "<p><strong>About</strong></p>"
            "<ul>"
            "<li>Material: 52% Polyester and 48% Cotton (SPOKE and Printify "
            "Choice US), 50% Cotton and 50% Polyester (Printify Choice EU)</li>"
            "<li>Two sizes available: 30&quot; x 60&quot; and 36&quot; x 72&quot;</li>"
            "<li>White cotton loop backing</li>"
            "<li>One-sided print</li>"
            "</ul>"
            "<p>Beach days are always a bliss — showing up with a "
            "luxuriously soft and customized beach towel can add a bit "
            "more sunshine to your day. This heavyweight towel feels extra "
            "lush to the touch, featuring a polyester facing where your "
            "print goes in vivid detail, and a cotton loop backing for "
            "durability.</p>"
            "<p>Please note: due to the thickness of the towel, print "
            "colors may not soak all the way through the fabric, and a "
            "white backing can appear when the towel fibers are stroked.</p>"
            "<p><strong>Key features</strong></p>"
            "<ul>"
            "<li>Polyester print side — strong, durable, retains shape and "
            "dries quickly</li>"
            "<li>Cotton loop backing — long, fine combed-cotton fibres for "
            "a soft, durable towel</li>"
            "<li>Hemmed edges — folded and sewn for durability</li>"
            "<li>Moisture-absorbent material</li>"
            "<li>Blank product sourced from China</li>"
            "<li>For adults</li>"
            "</ul>"
            "<p><strong>Care instructions</strong></p>"
            "<p>Cold machine wash only, with similar colors, gentle cycle. "
            "Tumble dry on low or hang dry. Do not bleach or dry clean.</p>"
            "<p><strong>Size guide</strong></p>"
            "<table><tbody>"
            "<tr><th></th><th>30x60</th><th>36x72</th></tr>"
            "<tr><td>Length</td><td>30 in</td><td>36 in</td></tr>"
            "<tr><td>Height</td><td>60 in</td><td>72 in</td></tr>"
            "</tbody></table>"
        ),
        "default_tags": [
            "beach towel", "pool towel", "summer", "vacation",
            "beach vacation", "custom towel", "gift", "coastal decor",
            "sunset", "vibrant print", "oversized towel",
        ],
    },
}
