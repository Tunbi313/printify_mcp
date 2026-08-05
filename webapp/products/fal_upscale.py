"""
Thin client for fal.ai's hosted upscale model, used to blow up uploaded
artwork to the exact pixel size a Printify template requires.

Auth: set env var FAL_KEY (fal.ai dashboard -> Keys).
"""

import os

import fal_client
import httpx

UPSCALE_MODEL = "fal-ai/esrgan"
REMOVE_BG_MODEL = "fal-ai/imageutils/rembg"
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
