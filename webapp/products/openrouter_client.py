"""
Thin client for OpenRouter's chat completions API (OpenAI-compatible),
used for vision-based title/tag suggestions on the create-product page.

Auth: set env var OPENROUTER_API_KEY (openrouter.ai -> Keys).
Model: set env var OPENROUTER_MODEL to override the default.
"""

import os
import httpx

API_BASE = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "google/gemini-2.5-pro"


def _check_key() -> None:
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY env var is not set.")


def vision_chat(image_data_url: str, prompt: str, max_tokens: int = 200) -> str:
    """Send one image + text prompt to a vision-capable model via OpenRouter,
    return the plain text response."""
    _check_key()
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=90) as client:
        r = client.post(f"{API_BASE}/chat/completions", headers=headers, json=payload)
        if r.is_error:
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:500]}")
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()


def text_chat(prompt: str, max_tokens: int = 200) -> str:
    """Send a plain text-only prompt to the configured model, return the
    plain text response. Used for text classification/picking tasks that
    don't need vision (e.g. matching a product to a taxonomy category)."""
    _check_key()
    model = os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60) as client:
        r = client.post(f"{API_BASE}/chat/completions", headers=headers, json=payload)
        if r.is_error:
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:500]}")
        data = r.json()
        return (data["choices"][0]["message"]["content"] or "").strip()


def vision_chat_multi(image_urls: list[str], prompt: str, max_tokens: int = 200, model: str | None = None) -> str:
    """Send multiple images (already-hosted URLs, e.g. a product's several
    mockup angles) + one text prompt to a vision-capable model, so it can
    reason about the whole product rather than a single photo. Pass
    `model` to override the configured default for this one call (e.g. a
    premium model for a specific high-value creative task)."""
    _check_key()
    model = model or os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL)
    content = [{"type": "text", "text": prompt}]
    for url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=90) as client:
        r = client.post(f"{API_BASE}/chat/completions", headers=headers, json=payload)
        if r.is_error:
            raise RuntimeError(f"OpenRouter {r.status_code}: {r.text[:500]}")
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
