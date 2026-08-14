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
LTX_MODEL = "fal-ai/ltxv-2/image-to-video/fast"

# User-facing duration range. Each model's real floor is enforced in
# clamp_duration() below — never trust a client-submitted duration
# directly, video is billed per second and a stray value can cost $10+.
DURATION_MIN = 3
DURATION_MAX = 20
SEEDANCE_RESOLUTIONS = ("480p", "720p")
ASPECT_RATIOS = ("16:9", "9:16", "1:1")  # LTX ignores this — always 16:9

RATES_PER_SECOND = {
    "kling": 0.07,
    "seedance_720p": 0.473,
    "seedance_480p": 0.2205,
    "ltx": 0.04,
}


def clamp_duration(model: str, duration) -> int:
    """Snap a user-entered duration to what the chosen model actually
    supports: Kling only accepts exactly 5 or 10, Seedance's real floor is
    4s, LTX's is 6s (below that it fails outright)."""
    try:
        seconds = int(duration)
    except (TypeError, ValueError):
        seconds = DURATION_MIN
    seconds = max(DURATION_MIN, min(DURATION_MAX, seconds))
    if model == "kling":
        return 5 if seconds <= 7 else 10
    if model == "seedance":
        return max(4, seconds)
    if model == "ltx":
        return max(6, seconds)
    return seconds


def _check_key() -> None:
    if not os.environ.get("FAL_KEY"):
        raise RuntimeError("FAL_KEY env var is not set.")


def _download(url: str) -> tuple[bytes, str]:
    with httpx.Client(timeout=300) as client:
        r = client.get(url)
        r.raise_for_status()
        content_type = r.headers.get("content-type", "video/mp4")
        return r.content, content_type


def generate_kling_video(image_url: str, prompt: str, duration,
                          aspect_ratio: str) -> tuple[bytes, str]:
    _check_key()
    duration = clamp_duration("kling", duration)
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = ASPECT_RATIOS[0]

    result = fal_client.subscribe(
        KLING_MODEL,
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
        },
    )
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise RuntimeError("Kling không trả về video (có thể do prompt vi phạm chính sách nội dung).")
    return _download(url)


def generate_seedance_video(image_url: str, prompt: str, duration,
                             resolution: str, aspect_ratio: str,
                             generate_audio: bool = True) -> tuple[bytes, str]:
    _check_key()
    duration = clamp_duration("seedance", duration)
    if resolution not in SEEDANCE_RESOLUTIONS:
        resolution = SEEDANCE_RESOLUTIONS[0]
    if aspect_ratio not in ASPECT_RATIOS:
        aspect_ratio = ASPECT_RATIOS[0]

    result = fal_client.subscribe(
        SEEDANCE_MODEL,
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": str(duration),
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


def generate_ltx_video(image_url: str, prompt: str, duration) -> tuple[bytes, str]:
    """LTX Video 2.0 Fast — cheapest per-second option, natively supports
    6-20s clips with built-in audio. Output is always 16:9 (no aspect_ratio
    param — the model doesn't support other framings)."""
    _check_key()
    duration = clamp_duration("ltx", duration)

    result = fal_client.subscribe(
        LTX_MODEL,
        arguments={
            "image_url": image_url,
            "prompt": prompt,
            "duration": duration,
            "resolution": "1080p",
            "frame_rate": 25,  # required for the 12-20s range; fine at any duration
        },
    )
    video = result.get("video") or {}
    url = video.get("url")
    if not url:
        raise RuntimeError("LTX không trả về video (có thể do prompt vi phạm chính sách nội dung).")
    return _download(url)


def estimate_cost_usd(model: str, duration, resolution: str = "720p") -> float:
    """Rough cost estimate shown to the user before they spend real money —
    keep in sync with fal.ai's published per-second rates."""
    seconds = clamp_duration(model, duration)
    if model == "seedance":
        rate = RATES_PER_SECOND["seedance_720p"] if resolution == "720p" else RATES_PER_SECOND["seedance_480p"]
    elif model == "ltx":
        rate = RATES_PER_SECOND["ltx"]
    else:
        rate = RATES_PER_SECOND["kling"]
    return round(rate * seconds, 2)
