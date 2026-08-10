"""
Thin client for fal.ai's image-to-video models, used by Video AI to animate
an existing product mockup image into a short promo clip.

Auth: set env var FAL_KEY (fal.ai dashboard -> Keys).
"""

import os

import fal_client
import httpx

KLING_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/image-to-video"
SEEDANCE_MODEL = "bytedance/seedance-2.5/image-to-video"

# Whitelisted, cheap-ish combos only — never trust client-submitted
# duration/resolution directly, video generation is billed per second and a
# stray value (e.g. duration=30) can cost $10+ in one call.
KLING_DURATIONS = ("5", "10")
SEEDANCE_DURATIONS = ("5", "10")
SEEDANCE_RESOLUTIONS = ("480p", "720p")
ASPECT_RATIOS = ("16:9", "9:16", "1:1")


def _check_key() -> None:
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY env var is not set.")


def _download(url: str) -> tuple[bytes, str]:
    with httpx.Client(timeout=300) as client:
        r = client.get(url)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "video/mp4")
        return r.content, content_type


def generate_kling_video(image_url: str, prompt: str, duration: str,
                          aspect_ratio: str) -> tuple[bytes, str]:
    _check_key()
    if duration not in KLING_DURATIONS:
        duration = KLING_DURATIONS[0]
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = ASPECT_RATIOS[0]

    result = fal_client.subscribe(
        KLING_MODEL,
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
        },
    )
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise RuntimeError("Kling không trả về video (có thể do prompt vi phạm chính sách nội dung).")
    return _download(url)


def generate_seedance_video(image_url: str, prompt: str, duration: str,
                             resolution: str, aspect_ratio: str,
                             generate_audio: bool = True) -> tuple[bytes, str]:
    _check_key()
    if duration not in SEEDANCE_DURATIONS:
        duration = SEEDANCE_DURATIONS[0]
    if resolution not in SEEDANCE_RESOLUTIONS:
        resolution = SEEDANCE_RESOLUTIONS[0]
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = ASPECT_RATIOS[0]

    result = fal_client.subscribe(
        SEEDANCE_MODEL,
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "generate_audio": generate_audio,
        },
    )
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise RuntimeError("Seedance không trả về video (có thể do prompt vi phạm chính sách nội dung).")
    return _download(url)


def estimate_cost_usd(model: str, duration: str, resolution: str = "720p") -> float:
    """Rough cost estimate shown to the user before they spend real money —
    keep in sync with fal.ai's published per-second rates."""
    try:
        seconds = int(duration)
    except ValueError:
        seconds = 5
    if model == "seedance":
        per_second = 0.473 if resolution == "720p" else 0.2205
    else:
        per_second = 0.07  # Kling 2.5 Turbo Pro
    return round(per_second * seconds, 2)
