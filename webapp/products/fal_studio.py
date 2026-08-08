"""
AI Studio: image-to-image restyling (10 style presets) and outpainting
(canvas expand to match a target aspect ratio without cropping), both via
fal.ai. Background removal reuses fal_upscale.remove_background.
"""

import os

import fal_client
import httpx

RESTYLE_MODEL = "fal-ai/flux/dev/image-to-image"
OUTPAINT_MODEL = "fal-ai/bria/expand"

STYLE_PRESETS = {
    "watercolor": {
        "label": "Watercolor Illustration",
        "prompt": "Watercolor painting style, soft flowing washes, visible "
                   "paper texture, gentle color bleeds, delicate artistic "
                   "brushwork, no photorealism.",
    },
    "line_art": {
        "label": "Minimalist Line Art",
        "prompt": "Minimalist single-line art style, clean simple continuous "
                   "line drawing, black lines on white background, no "
                   "shading, no color, elegant simplicity.",
    },
    "retro_70s": {
        "label": "Retro 70s Groovy",
        "prompt": "Retro 1970s groovy illustration style, warm earthy "
                   "palette of orange, mustard, and brown, psychedelic "
                   "curves, vintage print texture.",
    },
    "flat_vector": {
        "label": "Flat Vector Illustration",
        "prompt": "Flat vector illustration style, clean bold shapes, "
                   "limited color palette, modern graphic design look, no "
                   "gradients, no photorealism.",
    },
    "pop_art": {
        "label": "Pop Art Comic",
        "prompt": "Pop art comic book style, bold black outlines, Ben-Day "
                   "halftone dots, bright primary colors, retro comic "
                   "aesthetic.",
    },
    "anime": {
        "label": "Anime / Manga",
        "prompt": "Anime manga illustration style, clean cel-shaded "
                   "coloring, expressive line art, Japanese animation "
                   "aesthetic.",
    },
    "chibi": {
        "label": "Cute Cartoon / Chibi",
        "prompt": "Cute chibi cartoon style, big expressive eyes, small "
                   "rounded proportions, soft pastel colors, adorable and "
                   "playful.",
    },
    "engraving": {
        "label": "Vintage Engraving",
        "prompt": "Vintage engraving woodcut style, fine crosshatch "
                   "linework, black and white etching, classic 19th-century "
                   "illustration look.",
    },
    "cyberpunk": {
        "label": "Cyberpunk Neon",
        "prompt": "Cyberpunk neon futuristic style, vibrant neon glow in "
                   "pink cyan and purple, dark moody background, sci-fi "
                   "aesthetic.",
    },
    "boho_botanical": {
        "label": "Boho Botanical",
        "prompt": "Bohemian botanical illustration style, delicate line art "
                   "of leaves and flowers, warm terracotta and sage color "
                   "palette, hand-drawn feel.",
    },
}


def _check_key() -> None:
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY env var is not set.")


def _download(url: str) -> bytes:
    with httpx.Client(timeout=180) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.content


def restyle_image(image_bytes: bytes, content_type: str, style_key: str) -> bytes:
    """Redraw `image_bytes` in one of the STYLE_PRESETS art styles, keeping
    the original composition/aspect ratio."""
    preset = STYLE_PRESETS.get(style_key)
    if not preset:
        raise ValueError(f"Unknown style: {style_key}")
    _check_key()

    image_url = fal_client.upload(image_bytes, content_type)
    result = fal_client.subscribe(
        RESTYLE_MODEL,
        arguments={
            "image_url": image_url,
            "prompt": preset["prompt"],
            "strength": 0.75,
        },
    )
    images = result.get("images") or []
    out_url = images[0]["url"] if images else (result.get("image") or {}).get("url")
    if not out_url:
        raise RuntimeError(f"Không lấy được ảnh kết quả từ model restyle: {result}")
    return _download(out_url)


def outpaint_to_canvas(image_bytes: bytes, content_type: str,
                        orig_w: int, orig_h: int,
                        canvas_w: int, canvas_h: int,
                        pad_x: int, pad_y: int) -> bytes:
    """Place `image_bytes` (orig_w x orig_h) onto a canvas_w x canvas_h
    canvas at offset (pad_x, pad_y) and have the model fill in the
    surrounding area — extends the image instead of cropping it."""
    _check_key()

    image_url = fal_client.upload(image_bytes, content_type)
    result = fal_client.subscribe(
        OUTPAINT_MODEL,
        arguments={
            "image_url": image_url,
            "canvas_size": [canvas_w, canvas_h],
            "original_image_size": [orig_w, orig_h],
            "original_image_location": [pad_x, pad_y],
        },
    )
    images = result.get("images") or []
    out_url = images[0]["url"] if images else (result.get("image") or {}).get("url")
    if not out_url:
        raise RuntimeError(f"Không lấy được ảnh kết quả từ model outpaint: {result}")
    return _download(out_url)
