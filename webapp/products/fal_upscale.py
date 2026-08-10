"""
Thin client for fal.ai's hosted upscale model, used to blow up uploaded
artwork to the exact pixel size a Printify template requires.

Auth: set env var FAL_KEY (fal.ai dashboard -> Keys).
"""

import os

import fal_client
import httpx

UPSCALE_MODEL = "fal-ai/esrgan"
REMOVE_BG_MODEL = "fal-ai/birefnet/v2"  # much better hair/fine-edge matting than basic rembg
PRODUCT_SHOT_MODEL = "fal-ai/bria/product-shot"
MODEL_MOCKUP_MODEL = "fal-ai/nano-banana/edit"
MAX_SCALE_PER_CALL = 8.0
MAX_CALLS = 4  # 8x chained 4 times = 4096x ceiling, plenty of headroom


def _check_key() -> None:
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY env var is not set.")


def _upscale_once(image_bytes: bytes, content_type: str, scale: float) -> bytes:
    image_url = fal_client.upload(image_bytes, content_type)
    result = fal_client.subscribe(
        UPSCALE_MODEL,
        arguments={"image_url": image_url, "scale": scale},
    )
    out_url = result["image"]["url"]
    with httpx.Client(timeout=180) as client:
        r = client.get(out_url)
        r.raise_for_status()
        return r.content


def upscale_image(image_bytes: bytes, content_type: str, scale: float) -> bytes:
    """Upload `image_bytes` to fal.ai and run it through the upscale model,
    chaining calls (capped at MAX_SCALE_PER_CALL each) if `scale` exceeds
    what a single call supports."""
    _check_key()
    current_bytes = image_bytes
    current_content_type = content_type
    remaining = scale
    calls = 0
    while remaining > 1.02 and calls < MAX_CALLS:
        step_scale = min(remaining, MAX_SCALE_PER_CALL)
        current_bytes = _upscale_once(current_bytes, current_content_type, step_scale)
        current_content_type = "image/png"
        remaining /= step_scale
        calls += 1
    return current_bytes


def remove_background(image_bytes: bytes, content_type: str) -> bytes:
    """Upload `image_bytes` to fal.ai and run it through a background-removal
    model, returning a transparent PNG."""
    _check_key()
    image_url = fal_client.upload(image_bytes, content_type)
    result = fal_client.subscribe(
        REMOVE_BG_MODEL,
        arguments={"image_url": image_url},
    )
    out_url = result["image"]["url"]
    with httpx.Client(timeout=180) as client:
        r = client.get(out_url)
        r.raise_for_status()
        return r.content


def generate_product_shot(image_url: str, scene_description: str,
                           num_results: int = 2) -> list[tuple[bytes, str]]:
    """Given the URL of an already-rendered product photo (e.g. a Printify
    mockup image), generate `num_results` new variants of it placed in a
    different scene/background via fal.ai's Bria product-shot model.
    Returns a list of (image_bytes, content_type) tuples."""
    _check_key()
    result = fal_client.subscribe(
        PRODUCT_SHOT_MODEL,
        arguments={
            "image_url": image_url,
            "scene_description": scene_description,
            "num_results": num_results,
            "fast": False,  # slower but much better integration/lighting than the fast model
            "placement_type": "manual_placement",  # "automatic" ignores num_results and
            "manual_placement_selection": "bottom_center",  # returns every candidate placement — costly
            "optimize_description": True,
        },
    )
    out = []
    with httpx.Client(timeout=180) as client:
        # Safety net: never return more than requested, even if the API
        # over-delivers — each extra image is billed, so cap it here too.
        for img in (result.get("images") or [])[:num_results]:
            url = img.get("url")
            if not url:
                continue
            r = client.get(url)
            r.raise_for_status()
            out.append((r.content, img.get("content_type") or "image/png"))
    return out


def upload_image_bytes(image_bytes: bytes, content_type: str) -> str:
    """Upload local image bytes to fal.ai's storage, returning a URL other
    fal.ai model calls can reference as input."""
    _check_key()
    return fal_client.upload(image_bytes, content_type)


def generate_model_mockup(image_urls: list[str], prompt: str,
                           num_images: int = 1) -> list[tuple[bytes, str]]:
    """Composite/generate a lifestyle photo of a product via fal.ai's
    Gemini-based image-edit model. `image_urls` is 1 URL (product photo
    only — AI invents the person) or 2 (product photo + a real, user-
    supplied model photo). Returns a list of (image_bytes, content_type)."""
    _check_key()
    result = fal_client.subscribe(
        MODEL_MOCKUP_MODEL,
        arguments={
            "prompt": prompt,
            "image_urls": image_urls,
            "num_images": num_images,
        },
    )
    out = []
    with httpx.Client(timeout=180) as client:
        for img in (result.get("images") or [])[:num_images]:
            url = img.get("url")
            if not url:
                continue
            r = client.get(url)
            r.raise_for_status()
            out.append((r.content, img.get("content_type") or "image/png"))
    return out
