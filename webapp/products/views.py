import base64
import io
import itertools
import json
import re
import uuid
from decimal import Decimal, InvalidOperation

from PIL import Image

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def _safe_next(request, next_url):
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return None


def _current_shop_id(request) -> str:
    """The Printify shop the app currently acts on — whichever the user
    picked via the sidebar switcher (stored in session), falling back to
    PRINTIFY_SHOP_ID for anyone who hasn't picked one yet."""
    return request.session.get("shop_id") or pc.default_shop_id()


def set_shop(request):
    if request.method != "POST":
        return redirect("index")
    shop_id = request.POST.get("shop_id", "")
    try:
        valid_ids = {str(s["id"]) for s in pc.list_shops()}
    except Exception as exc:
        messages.error(request, f"Không lấy được danh sách shop: {exc}")
        valid_ids = set()
    if shop_id in valid_ids:
        request.session["shop_id"] = shop_id
        messages.success(request, "Đã chuyển shop đang thao tác.")
    else:
        messages.error(request, "Shop không hợp lệ.")
    next_url = _safe_next(request, request.POST.get("next"))
    return redirect(next_url) if next_url else redirect("index")

from . import fal_studio
from . import fal_upscale
from . import fal_video
from . import firecrawl_client
from . import openrouter_client
from . import printify_client as pc
from . import shopify_csv
from . import shopify_taxonomy
from .models import AiMockup, AiVideo, CrawledProduct, CrawlJob
from .templates_catalog import TEMPLATES

GENERIC_FILENAME_WORDS = {
    "img", "image", "photo", "picture", "pic", "screenshot", "screen",
    "shot", "untitled", "file", "dsc", "download", "downloaded", "copy",
    "new", "final", "design", "artwork", "art", "sunfade",
}


def subject_from_filename(filename: str) -> str:
    """Derive a human-readable subject from a design file name, e.g.
    'Sunfade_Yosemite_RetroSunburst_7200x10800.png' -> 'Yosemite Retro Sunburst'.
    Zero-cost, deterministic — no AI call. Returns '' if nothing usable."""
    base = re.sub(r"\.[^.]+$", "", filename)
    raw_tokens = [t for t in re.split(r"[_\-\s]+", base) if t]
    words = []
    for tok in raw_tokens:
        if re.fullmatch(r"\d+", tok):
            continue  # pure numbers (dates, IDs)
        if re.fullmatch(r"\d{2,5}[xX]\d{2,5}", tok):
            continue  # "7200x10800"-style resolution
        for w in re.sub(r"([a-z])([A-Z])", r"\1 \2", tok).split():
            if w and w.lower() not in GENERIC_FILENAME_WORDS:
                words.append(w)
    if not words:
        return ""

    def title_case(w: str) -> str:
        if len(w) <= 3 and w == w.upper():
            return w
        return w[:1].upper() + w[1:].lower()

    return " ".join(title_case(w) for w in words)


def index(request):
    templates = [
        {"key": key, "label": t["label"], "sizes": [v["label"] for v in t["variants"]],
         "artwork_spec": t["artwork_spec"],
         "default_description": t.get("default_description", ""),
         "default_tags": ", ".join(t.get("default_tags", []))}
        for key, t in TEMPLATES.items()
    ]
    return render(request, "products/index.html", {"templates": templates})


def template_gallery(request):
    templates = [
        {
            "key": key,
            "label": t["label"],
            "sizes": [v["label"] for v in t["variants"]],
            "prices": sorted({v["price"] / 100 for v in t["variants"]}),
            "artwork_spec": t["artwork_spec"],
            "sample_prompt": t.get("sample_prompt", ""),
        }
        for key, t in TEMPLATES.items()
    ]
    return render(request, "products/template_gallery.html", {"templates": templates})


def template_images(request, key):
    tpl = TEMPLATES.get(key)
    if not tpl:
        return JsonResponse({"error": "Template không tồn tại."}, status=404)

    cache_key = f"blueprint_images:{tpl['blueprint_id']}"
    images = cache.get(cache_key)
    if images is None:
        try:
            images = pc.get_blueprint_images(tpl["blueprint_id"])
        except Exception as exc:
            return JsonResponse({"error": f"Không lấy được ảnh mẫu: {exc}"}, status=502)
        cache.set(cache_key, images, 60 * 60 * 24)

    return JsonResponse({"label": tpl["label"], "images": images})


CROP_ANCHORS = {"center", "top", "bottom", "left", "right"}
STUDIO_MAX_WORKING_DIM = 1600  # fal-ai/bria/expand caps canvas area at 5000x5000px


def _fit_cover(img: Image.Image, target_w: int, target_h: int, anchor: str = "center") -> Image.Image:
    """Resize `img` to fully cover target_w x target_h, then crop to exactly
    that size (no distortion, no letterboxing). `anchor` controls which part
    of the excess is kept: center/top/bottom/left/right."""
    src_w, src_h = img.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = max(target_w, round(src_w * scale))
    new_h = max(target_h, round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    excess_x = new_w - target_w
    excess_y = new_h - target_h

    if anchor == "top":
        left, top = excess_x // 2, 0
    elif anchor == "bottom":
        left, top = excess_x // 2, excess_y
    elif anchor == "left":
        left, top = 0, excess_y // 2
    elif anchor == "right":
        left, top = excess_x, excess_y // 2
    else:
        left, top = excess_x // 2, excess_y // 2

    return resized.crop((left, top, left + target_w, top + target_h))


def upscale_page(request):
    upscalable = [
        {"key": key, "label": t["label"],
         "target_width": t["target_width"], "target_height": t["target_height"]}
        for key, t in TEMPLATES.items()
        if t.get("target_width")
    ]

    if request.method != "POST":
        return render(request, "products/upscale.html", {"templates": upscalable})

    template_key = request.POST.get("template")
    image_file = request.FILES.get("image")
    tpl = TEMPLATES.get(template_key)
    crop_anchor = request.POST.get("crop_anchor", "center")
    if crop_anchor not in CROP_ANCHORS:
        crop_anchor = "center"
    remove_bg = request.POST.get("remove_bg") == "on"

    if not tpl or not tpl.get("target_width"):
        messages.error(request, "Template không hợp lệ.")
        return redirect("upscale_page")
    if not image_file:
        messages.error(request, "Bạn chưa chọn ảnh design.")
        return redirect("upscale_page")

    target_w, target_h = tpl["target_width"], tpl["target_height"]

    try:
        raw_bytes = image_file.read()
        content_type = image_file.content_type or "image/png"
        original = Image.open(io.BytesIO(raw_bytes))
        original.load()
        orig_w, orig_h = original.size
        needed_scale = max(target_w / orig_w, target_h / orig_h)

        if needed_scale > 1.02:
            working_bytes = fal_upscale.upscale_image(raw_bytes, content_type, needed_scale)
            content_type = "image/png"
        else:
            working_bytes = raw_bytes

        # Remove background AFTER upscaling: the upscale model isn't
        # guaranteed to preserve an alpha channel cleanly, so we run it on
        # the plain image first, then cut out the background at full res.
        if remove_bg:
            working_bytes = fal_upscale.remove_background(working_bytes, content_type)
            content_type = "image/png"

        result_img = Image.open(io.BytesIO(working_bytes))
        result_img.load()

        final_img = _fit_cover(result_img, target_w, target_h, crop_anchor)
        if final_img.mode not in ("RGB", "RGBA", "L", "LA"):
            final_img = final_img.convert("RGBA" if remove_bg else "RGB")

        buf = io.BytesIO()
        final_img.save(buf, format="PNG")

        filename = f"upscaled/{template_key}_{uuid.uuid4().hex[:8]}.png"
        saved_path = default_storage.save(filename, ContentFile(buf.getvalue()))
        result_url = default_storage.url(saved_path)
    except Exception as exc:
        messages.error(request, f"Lỗi khi xử lý ảnh: {exc}")
        return redirect("upscale_page")

    context = {
        "templates": upscalable,
        "result_url": result_url,
        "result_label": tpl["label"],
        "result_width": target_w,
        "result_height": target_h,
        "was_upscaled": needed_scale > 1.02,
    }
    return render(request, "products/upscale.html", context)


def _bytes_to_data_url(data: bytes, content_type: str = "image/png") -> str:
    b64 = base64.b64encode(data).decode()
    return f"data:{content_type};base64,{b64}"


def _resolve_image_for_vision(source_image: str) -> str:
    """Return something a vision LLM can actually fetch. Remote http(s)
    URLs pass through unchanged; a local storage path (dev mode, no R2
    configured) gets read directly off disk and inlined as a data: URL,
    since the LLM provider can't reach localhost."""
    if source_image.startswith(("http://", "https://", "data:")):
        return source_image
    media_prefix = settings.MEDIA_URL.strip("/") + "/"
    path = source_image.lstrip("/")
    if path.startswith(media_prefix):
        path = path[len(media_prefix):]
    try:
        with default_storage.open(path, "rb") as f:
            content_type = "image/png" if path.lower().endswith(".png") else "image/jpeg"
            return _bytes_to_data_url(f.read(), content_type)
    except OSError:
        return source_image  # best effort — let it fail upstream with a clear error


def _crop_to_content(img: Image.Image) -> Image.Image:
    """Crop transparent padding down to the actual subject, using the alpha
    channel's bounding box. No-op for images without transparency."""
    if img.mode != "RGBA":
        return img
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


def _compute_expand_canvas(w: int, h: int, target_w: int, target_h: int):
    """Given a current image size and a target aspect ratio, compute the
    smallest canvas (>= current size) with that exact ratio, and the
    (x, y) offset to center the original image within it."""
    target_ratio = target_w / target_h
    current_ratio = w / h
    if current_ratio > target_ratio:
        canvas_w, canvas_h = w, round(w / target_ratio)
    else:
        canvas_w, canvas_h = round(h * target_ratio), h
    pad_x = (canvas_w - w) // 2
    pad_y = (canvas_h - h) // 2
    return canvas_w, canvas_h, pad_x, pad_y


def ai_studio_page(request):
    styles = [{"key": k, "label": v["label"]} for k, v in fal_studio.STYLE_PRESETS.items()]
    templates = [
        {"key": key, "label": t["label"],
         "target_width": t["target_width"], "target_height": t["target_height"]}
        for key, t in TEMPLATES.items()
        if t.get("target_width")
    ]
    return render(request, "products/ai_studio.html", {"styles": styles, "templates": templates})


def studio_restyle(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    image_file = request.FILES.get("image")
    style_key = request.POST.get("style")
    if not image_file:
        return JsonResponse({"error": "Chưa có ảnh để xử lý."}, status=400)
    if not style_key:
        return JsonResponse({"error": "Bạn chưa chọn style."}, status=400)
    try:
        raw = image_file.read()
        content_type = image_file.content_type or "image/png"
        result_bytes = fal_studio.restyle_image(raw, content_type, style_key)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"image_data_url": _bytes_to_data_url(result_bytes)})


def studio_remove_bg(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    image_file = request.FILES.get("image")
    if not image_file:
        return JsonResponse({"error": "Chưa có ảnh để xử lý."}, status=400)
    try:
        raw = image_file.read()
        content_type = image_file.content_type or "image/png"
        result_bytes = fal_upscale.remove_background(raw, content_type)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"image_data_url": _bytes_to_data_url(result_bytes)})


def studio_resize(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    image_file = request.FILES.get("image")
    tpl = TEMPLATES.get(request.POST.get("template"))
    if not image_file:
        return JsonResponse({"error": "Chưa có ảnh để xử lý."}, status=400)
    if not tpl or not tpl.get("target_width"):
        return JsonResponse({"error": "Template không hợp lệ."}, status=400)

    target_w, target_h = tpl["target_width"], tpl["target_height"]
    needs_transparent = bool(tpl.get("transparent_bg"))
    try:
        raw = image_file.read()
        img = Image.open(io.BytesIO(raw))
        img.load()
        img = _crop_to_content(img)

        if needs_transparent:
            # Outpainting can't produce transparency — the model always
            # paints SOME pixels into the expanded area, even from a
            # transparent input (it flattens to black/white first). For
            # templates that need a transparent background, fit the
            # subject inside the canvas instead of filling it, and pad the
            # remainder with real transparency (no AI, no cropping).
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            w, h = img.size
            needed_scale = min(target_w / w, target_h / h)
            if needed_scale > 1.02:
                # fal-ai/esrgan drops the alpha channel entirely (returns
                # plain RGB, with transparent areas rendered as black) —
                # upscale the RGB and alpha separately and recombine rather
                # than trusting it to preserve transparency.
                alpha_channel = img.getchannel("A")
                rgb_buf = io.BytesIO()
                img.convert("RGB").save(rgb_buf, format="PNG")
                upscaled_bytes = fal_upscale.upscale_image(rgb_buf.getvalue(), "image/png", needed_scale)
                upscaled_rgb = Image.open(io.BytesIO(upscaled_bytes)).convert("RGB")
                upscaled_rgb.load()
                upscaled_alpha = alpha_channel.resize(upscaled_rgb.size, Image.LANCZOS)
                img = Image.merge("RGBA", (*upscaled_rgb.split(), upscaled_alpha))

            # Safety clamp in case the upscaler overshoots either axis.
            if img.width > target_w or img.height > target_h:
                img.thumbnail((target_w, target_h), Image.LANCZOS)

            final_img = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = (target_w - img.width) // 2
            paste_y = (target_h - img.height) // 2
            final_img.paste(img, (paste_x, paste_y), img)
        else:
            # The outpaint model caps canvas area at 5000x5000px — work at
            # a smaller resolution here and upscale back up after.
            if max(img.size) > STUDIO_MAX_WORKING_DIM:
                img.thumbnail((STUDIO_MAX_WORKING_DIM, STUDIO_MAX_WORKING_DIM), Image.LANCZOS)

            w, h = img.size
            canvas_w, canvas_h, pad_x, pad_y = _compute_expand_canvas(w, h, target_w, target_h)

            if (canvas_w, canvas_h) != (w, h):
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                expanded_bytes = fal_studio.outpaint_to_canvas(
                    buf.getvalue(), "image/png", w, h, canvas_w, canvas_h, pad_x, pad_y,
                )
                img = Image.open(io.BytesIO(expanded_bytes))
                img.load()

            # Final step: upscale (existing ESRGAN pipeline) to the exact
            # print-resolution pixel size the template requires.
            cur_w, cur_h = img.size
            needed_scale = max(target_w / cur_w, target_h / cur_h)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            working_bytes = buf.getvalue()
            if needed_scale > 1.02:
                working_bytes = fal_upscale.upscale_image(working_bytes, "image/png", needed_scale)

            final_img = Image.open(io.BytesIO(working_bytes))
            final_img.load()
            final_img = _fit_cover(final_img, target_w, target_h, "center")
            if final_img.mode not in ("RGB", "RGBA", "L", "LA"):
                final_img = final_img.convert("RGBA")

        out_buf = io.BytesIO()
        final_img.save(out_buf, format="PNG")
        filename = f"studio/{uuid.uuid4().hex[:8]}.png"
        saved_path = default_storage.save(filename, ContentFile(out_buf.getvalue()))
        result_url = default_storage.url(saved_path)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    return JsonResponse({"image_url": result_url, "width": target_w, "height": target_h})


def batch_page(request):
    templates = [{"key": key, "label": t["label"]} for key, t in TEMPLATES.items()]

    if request.method != "POST":
        return render(request, "products/batch.html", {"templates": templates})

    template_key = request.POST.get("template")
    tpl = TEMPLATES.get(template_key)
    image_files = request.FILES.getlist("images")

    if not tpl:
        messages.error(request, "Template không hợp lệ.")
        return redirect("batch_page")
    if not image_files:
        messages.error(request, "Bạn chưa chọn ảnh nào.")
        return redirect("batch_page")

    default_tags = tpl.get("default_tags", [])
    description = tpl.get("default_description", "")

    try:
        shop_id = _current_shop_id(request)
    except Exception as exc:
        messages.error(request, f"Lỗi cấu hình shop: {exc}")
        return redirect("batch_page")

    results = []
    for image_file in image_files:
        subject = subject_from_filename(image_file.name)
        title = f"{subject} {tpl['label'].split(' (')[0]}".strip() if subject else ""
        tags = list(dict.fromkeys(
            ([subject] if subject else []) + default_tags
        ))[:15]

        if not title:
            results.append({
                "filename": image_file.name, "ok": False,
                "error": "Không đọc được chủ đề từ tên file — bỏ qua, hãy đổi tên file rõ nghĩa hơn rồi tạo tay.",
            })
            continue

        try:
            uploaded = pc.upload_artwork_file(image_file.read(), image_file.name)
            product = pc.create_from_template(
                tpl, uploaded["id"], title, description, tags, shop_id,
            )
            results.append({
                "filename": image_file.name, "ok": True,
                "title": title, "product_id": product["id"],
            })
        except Exception as exc:
            results.append({"filename": image_file.name, "ok": False, "error": str(exc)})

    ok_count = sum(1 for r in results if r["ok"])
    messages.success(request, f"Đã tạo {ok_count}/{len(results)} sản phẩm draft.")
    return render(request, "products/batch.html", {"templates": templates, "results": results})


def _image_file_to_data_url(image_file) -> str:
    """Downscale an uploaded image and encode it as a base64 data: URL for
    sending to a vision model — no need for full print resolution here."""
    img = Image.open(image_file)
    img.load()
    img.thumbnail((1024, 1024), Image.LANCZOS)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def generate_title(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    image_file = request.FILES.get("image")
    tpl = TEMPLATES.get(request.POST.get("template"))
    if not image_file:
        return JsonResponse({"error": "Bạn chưa chọn ảnh design."}, status=400)

    label = tpl["label"] if tpl else "sản phẩm in theo yêu cầu"
    try:
        data_url = _image_file_to_data_url(image_file)
        prompt = (
            f"Look at this artwork image, made for a '{label}' product. Write "
            f"ONE product title: subject + style, under 140 characters. No "
            f"copyrighted names or brand logos. Respond with ONLY the title "
            f"text, no quotes, no explanation."
        )
        title = openrouter_client.vision_chat(data_url, prompt, max_tokens=60)
        title = title.strip().strip('"').strip()
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    return JsonResponse({"title": title})


def generate_tags(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    image_file = request.FILES.get("image")
    tpl = TEMPLATES.get(request.POST.get("template"))
    existing = [t.strip() for t in request.POST.get("existing_tags", "").split(",") if t.strip()]
    if not image_file:
        return JsonResponse({"error": "Bạn chưa chọn ảnh design."}, status=400)

    label = tpl["label"] if tpl else "sản phẩm in theo yêu cầu"
    try:
        data_url = _image_file_to_data_url(image_file)
        existing_str = ", ".join(existing) if existing else "(none yet)"
        prompt = (
            f"Look at this artwork image, made for a '{label}' product. "
            f"Existing tags: {existing_str}. Suggest 5 ADDITIONAL tags "
            f"describing this specific artwork's subject/style that are NOT "
            f"already in that list and not too similar to them. Respond with "
            f"ONLY a comma-separated list of the 5 new tags, nothing else."
        )
        raw = openrouter_client.vision_chat(data_url, prompt, max_tokens=100)
        new_tags = [t.strip() for t in raw.split(",") if t.strip()]
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    combined = list(dict.fromkeys(existing + new_tags))[:15]
    return JsonResponse({"tags": combined})


def create_product(request):
    if request.method != "POST":
        return redirect("index")

    template_key = request.POST.get("template")
    title = request.POST.get("title", "").strip()
    description = request.POST.get("description", "").strip()
    tags_raw = request.POST.get("tags", "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    image_file = request.FILES.get("image")

    tpl = TEMPLATES.get(template_key)
    if not tpl:
        messages.error(request, f"Template không hợp lệ: {template_key}")
        return redirect("index")
    if not image_file:
        messages.error(request, "Bạn chưa chọn ảnh design.")
        return redirect("index")
    if not title:
        messages.error(request, "Bạn chưa điền title.")
        return redirect("index")

    try:
        shop_id = _current_shop_id(request)
        uploaded = pc.upload_artwork_file(image_file.read(), image_file.name)
        product = pc.create_from_template(
            tpl, uploaded["id"], title, description, tags, shop_id,
        )
    except Exception as exc:
        messages.error(request, f"Lỗi khi tạo sản phẩm: {exc}")
        return redirect("index")

    messages.success(request, "Đã tạo draft trên Printify. Mockup có thể mất vài giây để render.")
    return redirect(reverse("product_detail", args=[product["id"]]))


def library_page(request):
    try:
        shop_id = _current_shop_id(request)
        products = pc.list_all_products(shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được danh sách sản phẩm: {exc}")
        products = []

    drafts = []
    for p in products:
        external = p.get("external") or {}
        if external.get("id"):
            continue  # already published, skip — this is a draft-only library
        images = p.get("images") or []
        drafts.append({
            "id": p["id"],
            "title": p["title"],
            "thumb": images[0]["src"] if images else None,
        })
    return render(request, "products/library.html", {"drafts": drafts, "active_tab": "draft"})


def product_detail(request, product_id):
    shop_id = _current_shop_id(request)
    try:
        product = pc.get_product(product_id, shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được sản phẩm: {exc}")
        return redirect("index")

    images = [img.get("src") for img in product.get("images", []) if img.get("src")]
    external = product.get("external") or {}
    context = {
        "product": product,
        "images": images,
        "enabled_variants": [v["title"] for v in product.get("variants", []) if v.get("is_enabled")],
        "is_published": bool(external.get("id")),
        "storefront_url": external.get("handle"),
    }
    return render(request, "products/detail.html", context)


MOCKUP_STYLES = {
    "cozy_living_room": {
        "label": "Phòng khách ấm cúng",
        "prompt": "a realistic professional product photo of the item "
                  "standing upright on a light wood coffee table in a cozy "
                  "modern living room, soft warm natural window light, "
                  "gently blurred background, sharp focus and natural soft "
                  "shadow directly beneath the product, true-to-life colors",
    },
    "outdoor_camp": {
        "label": "Bàn gỗ ngoài trời / dã ngoại",
        "prompt": "a realistic professional product photo of the item "
                  "standing upright on a rustic weathered wood picnic "
                  "table outdoors at a campsite, bright natural daylight, "
                  "gently blurred green forest background, sharp focus and "
                  "natural soft shadow directly beneath the product, "
                  "true-to-life colors",
    },
    "minimal_studio": {
        "label": "Studio tối giản",
        "prompt": "a realistic professional product photo of the item "
                  "standing upright on a plain light grey studio backdrop, "
                  "soft even studio lighting, sharp focus, natural soft "
                  "shadow directly beneath the product, true-to-life colors, "
                  "e-commerce catalog style",
    },
    "cafe_table": {
        "label": "Bàn quán cà phê",
        "prompt": "a realistic professional product photo of the item "
                  "standing upright on a natural wood grain café table next "
                  "to a cup of coffee, soft warm daylight through a nearby "
                  "window, gently blurred cozy coffee shop background, sharp "
                  "focus and natural soft shadow directly beneath the "
                  "product, true-to-life colors",
    },
}


MODEL_ACTION_HINTS = {
    "tshirt": "wearing this exact t-shirt, the printed design clearly "
              "visible and unaltered on the front, natural fit, front-facing pose",
    "hat": "wearing this exact trucker hat on their head, natural angle, realistic fit",
    "hat_vintage_a": "wearing this exact dad hat on their head, natural angle, realistic fit",
    "hat_vintage_b": "wearing this exact cap on their head, natural angle, realistic fit",
    "mug": "holding this exact mug with both hands close to their chest, cozy setting",
    "stein_mug": "holding this exact stein mug in one hand, casual pose",
    "tumbler": "holding this exact tumbler in one hand, casual outdoor pose",
    "candle": "holding this exact candle jar in both hands, cozy home setting",
    "can_glass_engraved": "holding this exact glass in one hand, casual pose",
}
GENERIC_MODEL_ACTION = "naturally holding or displaying this exact product, realistic lifestyle photo"

MODEL_PERSONAS = {
    "diverse": {
        "label": "Ngẫu nhiên / đa dạng",
        "prompt": "a fictional, generic-looking adult model (not a real "
                  "celebrity or public figure), natural friendly expression",
    },
    "female_casual": {
        "label": "Nữ trẻ, phong cách casual",
        "prompt": "a fictional young adult woman (not a real celebrity or "
                  "public figure), casual everyday style, natural friendly expression",
    },
    "male_casual": {
        "label": "Nam trẻ, phong cách casual",
        "prompt": "a fictional young adult man (not a real celebrity or "
                  "public figure), casual everyday style, natural friendly expression",
    },
}


def _guess_model_action(product: dict) -> str:
    blueprint_id = product.get("blueprint_id")
    for key, tpl in TEMPLATES.items():
        if tpl.get("blueprint_id") == blueprint_id:
            return MODEL_ACTION_HINTS.get(key, GENERIC_MODEL_ACTION)
    return GENERIC_MODEL_ACTION


GENERIC_VIDEO_MOTION = "camera slowly zooms in on the product, smooth subtle motion, professional product photography style"


def _list_published_products(shop_id: str) -> list:
    products = pc.list_all_products(shop_id)
    published = []
    for p in products:
        external = p.get("external") or {}
        if not external.get("id"):
            continue  # draft, not published
        images = p.get("images") or []
        published.append({
            "id": p["id"],
            "title": p["title"],
            "thumb": images[0]["src"] if images else None,
        })
    return published


def _save_ai_images(product_id: str, generated: list, product_title: str,
                     kind: str, label: str) -> list:
    saved = []
    for image_bytes, content_type in generated:
        ext = "png" if "png" in content_type else "jpg"
        filename = f"ai_mockups/{product_id}_{uuid.uuid4().hex[:8]}.{ext}"
        saved_path = default_storage.save(filename, ContentFile(image_bytes))
        saved.append(AiMockup.objects.create(
            product_id=product_id, product_title=product_title,
            kind=kind, label=label, file_path=saved_path,
        ))
    return saved


def mockup_gallery(request):
    mockups = AiMockup.objects.all()[:300]
    return render(request, "products/mockup_gallery.html", {"mockups": mockups, "active_tab": "mockup"})


def mockup_delete(request, mockup_id):
    if request.method != "POST":
        return redirect("mockup_gallery")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        mockup = AiMockup.objects.get(id=mockup_id)
    except AiMockup.DoesNotExist:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Không tìm thấy ảnh."}, status=404)
        messages.error(request, "Không tìm thấy ảnh.")
        return redirect("mockup_gallery")

    default_storage.delete(mockup.file_path)
    mockup.delete()
    if is_ajax:
        return JsonResponse({"ok": True})
    messages.success(request, "Đã xóa ảnh mockup.")
    return redirect("mockup_gallery")


MOCKUP_STUDIO_PAGE_SIZE = 30


def mockup_studio_index(request):
    try:
        shop_id = _current_shop_id(request)
        published = _list_published_products(shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được danh sách sản phẩm: {exc}")
        published = []

    paginator = Paginator(published, MOCKUP_STUDIO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "products/mockup_studio_index.html", {"page_obj": page_obj})


def mockup_studio(request, product_id):
    shop_id = _current_shop_id(request)
    try:
        product = pc.get_product(product_id, shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được sản phẩm: {exc}")
        return redirect("index")

    external = product.get("external") or {}
    if not external.get("id"):
        messages.error(request, "Mockup Studio chỉ áp dụng cho sản phẩm đã publish lên store.")
        return redirect("mockup_studio_index")

    source_images = [img.get("src") for img in product.get("images", []) if img.get("src")]
    context = {
        "product": product,
        "source_images": source_images,
        "styles": MOCKUP_STYLES,
        "personas": MODEL_PERSONAS,
        "default_model_action": _guess_model_action(product),
    }

    if request.method != "POST":
        return render(request, "products/mockup_studio.html", context)

    mode = request.POST.get("mode", "scene")
    if mode == "model":
        return _mockup_studio_model_post(request, product_id, source_images, context)
    return _mockup_studio_scene_post(request, product_id, source_images, context)


def _mockup_studio_scene_post(request, product_id, source_images, context):
    source_image = request.POST.get("source_image", "")
    style_key = request.POST.get("style", "custom")
    custom_prompt = request.POST.get("custom_prompt", "").strip()
    try:
        num_results = int(request.POST.get("num_results", 2))
    except ValueError:
        num_results = 2
    num_results = max(1, min(4, num_results))

    context["selected_source_image"] = source_image
    context["selected_style"] = style_key
    context["custom_prompt"] = custom_prompt
    context["num_results"] = num_results

    if source_image not in source_images:
        messages.error(request, "Ảnh gốc không hợp lệ.")
        return render(request, "products/mockup_studio.html", context)

    preset = MOCKUP_STYLES.get(style_key)
    scene_description = custom_prompt if style_key == "custom" else (preset["prompt"] if preset else "")
    if not scene_description:
        messages.error(request, "Bạn chưa nhập mô tả bối cảnh (chọn 'Tuỳ chỉnh' thì phải điền mô tả).")
        return render(request, "products/mockup_studio.html", context)

    try:
        generated = fal_upscale.generate_product_shot(source_image, scene_description, num_results)
    except Exception as exc:
        messages.error(request, f"Lỗi khi tạo mockup: {exc}")
        return render(request, "products/mockup_studio.html", context)

    style_label = preset["label"] if preset else "Tuỳ chỉnh"
    result_mockups = _save_ai_images(
        product_id, generated, context["product"].get("title", ""), "scene", style_label,
    )
    if not result_mockups:
        messages.error(request, "Không tạo được mockup nào — thử lại hoặc đổi mô tả bối cảnh.")

    context["result_mockups"] = result_mockups
    context["result_style_label"] = style_label
    return render(request, "products/mockup_studio.html", context)


def _mockup_studio_model_post(request, product_id, source_images, context):
    product_image = request.POST.get("model_product_image", "")
    action = request.POST.get("model_action", "").strip()
    model_source = request.POST.get("model_source", "ai")
    persona_key = request.POST.get("model_persona", "diverse")
    model_photo = request.FILES.get("model_photo")
    try:
        num_images = int(request.POST.get("model_num_images", 1))
    except ValueError:
        num_images = 1
    num_images = max(1, min(4, num_images))

    context["selected_model_product_image"] = product_image
    context["model_action"] = action
    context["model_source"] = model_source
    context["model_persona"] = persona_key
    context["model_num_images"] = num_images

    if product_image not in source_images:
        messages.error(request, "Ảnh sản phẩm không hợp lệ.")
        return render(request, "products/mockup_studio.html", context)
    if not action:
        messages.error(request, "Bạn chưa mô tả người mẫu sẽ làm gì với sản phẩm.")
        return render(request, "products/mockup_studio.html", context)

    try:
        if model_source == "upload":
            if not model_photo:
                messages.error(request, "Bạn chưa upload ảnh người mẫu.")
                return render(request, "products/mockup_studio.html", context)
            model_photo_url = fal_upscale.upload_image_bytes(
                model_photo.read(), model_photo.content_type or "image/jpeg",
            )
            prompt = (
                "The first image shows a product with a specific printed "
                "design. The second image shows a real person. Edit the "
                f"second image so the person is {action}. Keep the "
                "person's face, body, pose and background from the second "
                "image unchanged. Keep the product's exact printed design, "
                "colors, shape and text from the first image unchanged — "
                "do not alter or reinterpret it. Photorealistic result."
            )
            image_urls = [product_image, model_photo_url]
        else:
            persona = MODEL_PERSONAS.get(persona_key, MODEL_PERSONAS["diverse"])
            prompt = (
                "The provided image shows a product with a specific "
                f"printed design. Generate a new, photorealistic lifestyle "
                f"photo of {persona['prompt']} who is {action}. The "
                "product's exact printed design, colors, shape and text "
                "from the provided image must remain unchanged — do not "
                "alter or reinterpret it. Natural lighting, realistic "
                "setting, high quality photography."
            )
            image_urls = [product_image]

        generated = fal_upscale.generate_model_mockup(image_urls, prompt, num_images)
    except Exception as exc:
        messages.error(request, f"Lỗi khi tạo mockup người mẫu: {exc}")
        return render(request, "products/mockup_studio.html", context)

    result_mockups = _save_ai_images(
        product_id, generated, context["product"].get("title", ""), "model", action,
    )
    if not result_mockups:
        messages.error(request, "Không tạo được mockup nào — thử lại hoặc đổi mô tả.")

    context["model_result_mockups"] = result_mockups
    return render(request, "products/mockup_studio.html", context)


VIDEO_STUDIO_PAGE_SIZE = 30
VIDEO_PROMPT_MAX_IMAGES = 5

# Each tier maps directly to a real ad placement, per the fal.ai model
# research: Kling O3 Pro caps at 15s (cinematic single-take quality),
# Seedance 2.5 natively supports up to 30s (used for the longer tiers).
DURATION_TIERS = {
    "product_page": {"seconds": 5, "label": "Product Page (5s)", "desc": "Quảng cáo ngắn ngay trên trang sản phẩm", "recommended_model": "kling"},
    "homepage": {"seconds": 10, "label": "Homepage (10s)", "desc": "Banner/hero video trang chủ", "recommended_model": "kling"},
    "real_ad": {"seconds": 15, "label": "Real Ad (15s)", "desc": "Quảng cáo thật cho sản phẩm chủ lực", "recommended_model": "seedance"},
    "story_ad": {"seconds": 20, "label": "Story Ad (15-20s+)", "desc": "Quảng cáo dạng câu chuyện, nhiều beat", "recommended_model": "seedance"},
}

# Premium prompt-writing models, selectable per-generation on the UI —
# kept separate from OPENROUTER_MODEL (used for cheap title/tag/crawl
# tasks) since ad-video prompts are worth paying for real quality.
PROMPT_MODEL_CHOICES = [
    ("anthropic/claude-opus-5", "Claude Opus 5"),
    ("openai/gpt-5.6-sol", "GPT-5.6 Sol"),
]

MODEL_CAPABILITY_BRIEFS = {
    "kling": (
        "Target model: Kling O3 Pro. Its strength is smooth, highly "
        "controllable, cinematic single-take camera motion, up to 15s. "
        "It CANNOT cut between different shots/angles within one "
        "generation — describe one continuous choreography only."
    ),
    "seedance": (
        "Target model: Seedance 2.5. Its strength is a long single take "
        "(up to 30s) that keeps the subject/product visually consistent "
        "throughout, with synchronized audio. Good for multi-beat "
        "choreography within ONE continuous take — still no hard cuts."
    ),
    "ltx": (
        "Target model: LTX Video 2.0 Fast. A budget option, up to 20s, "
        "built-in audio, but motion coherence is less refined on complex "
        "or long moves than Kling/Seedance — keep the described motion "
        "simpler and more modest."
    ),
}


def _video_pacing_note(duration: int) -> str:
    if duration <= 6:
        return (
            f"The video will be exactly {duration} seconds long — that's "
            "only enough time for ONE simple continuous motion. Do not "
            "describe multiple beats or a sequence of events."
        )
    if duration <= 11:
        return (
            f"The video will be exactly {duration} seconds long. Describe "
            "TWO beats in order (e.g. establish the subject with impact, "
            "then one clear move or reveal), paced to fill the runtime "
            "without feeling static or repetitive."
        )
    if duration <= 16:
        return (
            f"The video will be exactly {duration} seconds long — enough "
            "for a real advertising arc. Describe 2-3 beats in order: an "
            "opening that establishes the subject with impact, a middle "
            "beat that reveals a real detail or moves into context, and a "
            "confident closing beat that settles on the product/subject "
            "like the final hold of a TV ad."
        )
    return (
        f"The video will be exactly {duration} seconds long — a short "
        "STORY ad, not a single camera move. Describe 3-4 beats forming "
        "an arc: (1) an immediate, attention-grabbing opening — the "
        "strongest visual is already on screen at frame 0, no slow "
        "build-up; (2) development — move into context or reveal a real "
        "detail; (3) an optional small turn or shift in energy; (4) a "
        "confident closing beat that lingers on the product/subject, like "
        "an ad's final hero shot. All of this is still ONE continuous, "
        "unbroken camera take — choreograph the camera through the "
        "beats, never describe hard cuts between shots."
    )


def _build_video_prompt_instruction(duration: int, num_images: int, target_model: str) -> str:
    capability_brief = MODEL_CAPABILITY_BRIEFS.get(target_model, MODEL_CAPABILITY_BRIEFS["kling"])
    pacing_note = _video_pacing_note(duration)

    craft_rules = (
        "CAMERA CRAFT — follow these rules used by professional motion "
        "directors:\n"
        "- One primary motion idea running through the clip. Never stack "
        "unrelated moves (rotate AND zoom AND pan at once) — image-to-"
        "video models blend stacked motion into a blurry mess.\n"
        "- The opening instant is already the strongest frame (the source "
        "photo) — never describe a fade-in, blur-to-focus, or slow reveal "
        "from black. Motion starts immediately, not after a build-up.\n"
        "- Keep scale/zoom change subtle and eased: a slow push-in or "
        "pull-back that accelerates in and decelerates out, never a snap "
        "or crash-zoom, never linear/mechanical.\n"
        "- Name camera moves with real cinematography terms — orbit, "
        "turntable, dolly/push-in, pan, tilt, parallax drift — not vague "
        "words like \"nice motion\" or \"cool effect.\"\n"
        "- Give lighting/mood in one short clause (e.g. \"soft studio key "
        "light\", \"warm golden-hour light\") so the render stays "
        "consistent instead of flat or overexposed.\n\n"
    )

    framing_note = (
        "The FIRST image below is the EXACT photo that will be used as "
        "the video's starting frame (frame 0) — build every prompt "
        "assuming that is what's on screen. Any additional photos are "
        "just other angles of the same subject for material/color "
        "reference only — never describe a crop, background or detail "
        "that is only visible in a reference photo and not in the first "
        "image.\n\n"
        if num_images > 1 else ""
    )

    style_instruction = (
        "First, look at the FIRST image and decide: does it show a real, "
        "photorealistic PERSON (a model wearing/holding/using the "
        "product)?\n\n"
        "IF YES — write 4 DISTINCT prompts using human-plausible motion "
        "only (a real face/body warps if pushed too hard, so keep every "
        "move small and natural):\n"
        "1. \"natural\" — subtle life-like motion only: a soft breath, "
        "gentle hair/fabric sway, a slow blink or small head movement; "
        "camera holds still or drifts almost imperceptibly.\n"
        "2. \"detail\" — camera slowly pushes in from the person toward "
        "the product they're holding/wearing, settling close on the "
        "print/design; the person stays nearly still.\n"
        "3. \"turn\" — the person makes one slow, continuous, natural "
        "turn (body or head) so the product reads from a slightly "
        "different angle; no jerky or repeated movement.\n"
        "4. \"dynamic\" — the most energetic of the four but still "
        "human-plausible: one small natural gesture paired with a "
        "slightly quicker camera push-in, ad energy without breaking "
        "realism.\n\n"
        "IF NO (a plain product/scene, no person) — write 4 DISTINCT "
        "camera/motion prompts:\n"
        "1. \"rotate\" — a clean, professional turntable-style shot: the "
        "product slowly rotates or the camera orbits at a constant, even "
        "speed, product centered and anchored, soft studio lighting.\n"
        "2. \"zoom\" — a slow, eased dolly/push-in that settles close on "
        "one specific real detail visible in the photos — subtle scale "
        "change, never a crash-zoom.\n"
        "3. \"lifestyle\" — the product in a natural real-world setting "
        "implied by the product type, a gentle Ken-Burns-style pan or "
        "drift, soft ambient light.\n"
        "4. \"dynamic\" — the fastest, punchiest camera move of the four "
        "for a social-media-ad feel — still ONE motion performed with "
        "more energy, not multiple stacked moves.\n\n"
        "Apply the duration pacing above WITHIN each of these 4 styles — "
        "a longer duration means more beats inside that same style "
        "archetype, not a departure from it.\n\n"
    )

    style_keys = (
        'if a person is shown: '
        '[{"key": "natural", "label": "Chuyển động tự nhiên", "prompt": "..."}, '
        '{"key": "detail", "label": "Cận cảnh sản phẩm", "prompt": "..."}, '
        '{"key": "turn", "label": "Xoay người nhẹ", "prompt": "..."}, '
        '{"key": "dynamic", "label": "Năng động, quảng cáo", "prompt": "..."}] '
        '— otherwise: '
        '[{"key": "rotate", "label": "Xoay chuyên nghiệp", "prompt": "..."}, '
        '{"key": "zoom", "label": "Zoom cận cảnh", "prompt": "..."}, '
        '{"key": "lifestyle", "label": "Bối cảnh đời thường", "prompt": "..."}, '
        '{"key": "dynamic", "label": "Năng động, quảng cáo", "prompt": "..."}] '
    )

    return (
        "You are a world-class creative director for AI-generated "
        "advertising video (image-to-video models like Kling/Seedance/"
        "LTX — they animate a static image, they don't generate from "
        f"nothing). You are shown {num_images} photo(s) of the SAME "
        "subject. Study its actual shape, material, color and printed "
        "design/appearance in the images.\n\n"
        f"{capability_brief}\n\n"
        f"{framing_note}"
        f"{pacing_note}\n\n"
        f"{craft_rules}"
        f"{style_instruction}"
        "Each prompt must be in English, describing ONLY camera/"
        "lighting/motion (and physically plausible human motion if a "
        "person is shown), correctly paced for the exact duration stated "
        "above — never invent new colors, text, logos or shapes that "
        "contradict what is actually in the photos.\n\n"
        "Respond with ONLY a JSON array, no markdown fences, no extra "
        "text, no pretty-printing — a single compact line with no line "
        f"breaks or indentation, in this exact shape: {style_keys}"
        "— \"label\" must be the Vietnamese labels given above, \"prompt\" in English."
    )


def video_ai_upload_image(request):
    """AJAX endpoint: host an uploaded file on fal.ai's storage immediately
    on file-select (not just at final form submit), so "AI sinh prompt"
    has a real URL to hand the vision model right away."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    image_file = request.FILES.get("image")
    if not image_file:
        return JsonResponse({"error": "Chưa có file ảnh."}, status=400)
    try:
        url = fal_upscale.upload_image_bytes(image_file.read(), image_file.content_type or "image/jpeg")
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"url": url})


def video_ai_studio(request):
    context = {
        "duration_min": fal_video.DURATION_MIN,
        "duration_max": fal_video.DURATION_MAX,
        "resolutions": fal_video.SEEDANCE_RESOLUTIONS,
        "aspect_ratios": fal_video.ASPECT_RATIOS,
        "duration_tiers": DURATION_TIERS,
        "prompt_model_choices": PROMPT_MODEL_CHOICES,
        "default_prompt": GENERIC_VIDEO_MOTION,
        "generate_audio": True,
        "selected_duration_tier": "product_page",
        "selected_duration": DURATION_TIERS["product_page"]["seconds"],
        "selected_model": "kling",
        "selected_prompt_model": PROMPT_MODEL_CHOICES[0][0],
    }

    # The "pick from an existing product" flow hands back its chosen
    # image via query params so it arrives here pre-selected.
    picked_image = request.GET.get("source_image", "")
    if picked_image:
        context.update({
            "selected_source_image": picked_image,
            "picked_product_id": request.GET.get("product_id", ""),
            "picked_product_title": request.GET.get("product_title", ""),
        })

    if request.method != "POST":
        return render(request, "products/video_ai_studio.html", context)

    duration_tier = request.POST.get("duration_tier", "product_page")
    if duration_tier not in DURATION_TIERS:
        duration_tier = "product_page"
    model = request.POST.get("model") or DURATION_TIERS[duration_tier]["recommended_model"]
    if model not in ("kling", "seedance", "ltx"):
        model = "kling"
    prompt = request.POST.get("prompt", "").strip()
    duration = fal_video.clamp_duration(
        model, request.POST.get("duration") or DURATION_TIERS[duration_tier]["seconds"],
    )
    resolution = request.POST.get("resolution", "720p")
    aspect_ratio = request.POST.get("aspect_ratio", "16:9")
    if aspect_ratio not in fal_video.ASPECT_RATIOS:
        aspect_ratio = fal_video.ASPECT_RATIOS[0]
    if model == "ltx":
        aspect_ratio = "16:9"  # LTX only outputs 16:9, no param to change it
    generate_audio = request.POST.get("generate_audio") == "on"
    product_id = request.POST.get("product_id", "").strip()
    product_title = request.POST.get("product_title", "").strip()
    prompt_model = request.POST.get("prompt_model") or PROMPT_MODEL_CHOICES[0][0]
    if prompt_model not in dict(PROMPT_MODEL_CHOICES):
        prompt_model = PROMPT_MODEL_CHOICES[0][0]

    context.update({
        "selected_model": model,
        "prompt": prompt,
        "selected_duration": duration,
        "selected_duration_tier": duration_tier,
        "selected_resolution": resolution,
        "selected_aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
        "selected_prompt_model": prompt_model,
        "picked_product_id": product_id,
        "picked_product_title": product_title,
    })

    # A direct upload takes priority over a previously-picked URL.
    uploaded_file = request.FILES.get("image_upload")
    source_image = request.POST.get("source_image", "").strip()
    if uploaded_file:
        try:
            source_image = fal_upscale.upload_image_bytes(
                uploaded_file.read(), uploaded_file.content_type or "image/jpeg",
            )
        except Exception as exc:
            messages.error(request, f"Lỗi khi upload ảnh: {exc}")
            return render(request, "products/video_ai_studio.html", context)
    context["selected_source_image"] = source_image

    if not source_image:
        messages.error(request, "Bạn chưa upload ảnh hoặc chọn ảnh nguồn.")
        return render(request, "products/video_ai_studio.html", context)
    if not prompt:
        messages.error(request, "Bạn chưa mô tả chuyển động cho video.")
        return render(request, "products/video_ai_studio.html", context)

    try:
        if model == "seedance":
            if resolution not in fal_video.SEEDANCE_RESOLUTIONS:
                resolution = fal_video.SEEDANCE_RESOLUTIONS[1]
            video_bytes, content_type = fal_video.generate_seedance_video(
                source_image, prompt, duration, resolution, aspect_ratio, generate_audio,
            )
        elif model == "ltx":
            resolution = "1080p"
            video_bytes, content_type = fal_video.generate_ltx_video(
                source_image, prompt, duration,
            )
        else:
            resolution = ""  # Kling O3 Pro has no resolution knob
            video_bytes, content_type = fal_video.generate_kling_video(
                source_image, prompt, duration, generate_audio,
            )
    except Exception as exc:
        messages.error(request, f"Lỗi khi tạo video: {exc}")
        return render(request, "products/video_ai_studio.html", context)

    cost = fal_video.estimate_cost_usd(model, duration, resolution, generate_audio)
    filename = f"ai_videos/{(product_id or 'upload')}_{uuid.uuid4().hex[:8]}.mp4"
    saved_path = default_storage.save(filename, ContentFile(video_bytes))
    result_video = AiVideo.objects.create(
        product_id=product_id, product_title=product_title,
        source_image=source_image[:1000], duration_tier=duration_tier,
        prompt_model=prompt_model, model=model, prompt=prompt,
        duration=str(duration), aspect_ratio=aspect_ratio,
        cost_usd=cost, file_path=saved_path,
    )
    context["result_video"] = result_video
    return render(request, "products/video_ai_studio.html", context)


def video_ai_pick_products(request):
    try:
        shop_id = _current_shop_id(request)
        published = _list_published_products(shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được danh sách sản phẩm: {exc}")
        published = []

    paginator = Paginator(published, VIDEO_STUDIO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "products/video_ai_pick_products.html", {"page_obj": page_obj})


def video_ai_pick_product_images(request, product_id):
    shop_id = _current_shop_id(request)
    try:
        product = pc.get_product(product_id, shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được sản phẩm: {exc}")
        return redirect("video_ai_pick_products")

    original_images = [img.get("src") for img in product.get("images", []) if img.get("src")]
    mockup_kind_labels = dict(AiMockup.KIND_CHOICES)
    ai_mockups = AiMockup.objects.filter(product_id=product_id)
    image_choices = (
        [{"src": s, "tag": "Ảnh gốc"} for s in original_images]
        + [
            {"src": m.url, "tag": f"🤖 {m.label or mockup_kind_labels.get(m.kind, 'AI')}"}
            for m in ai_mockups
        ]
    )
    return render(request, "products/video_ai_pick_product_images.html", {
        "product": product, "image_choices": image_choices,
    })


def video_generate_prompts(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    selected_source_image = request.POST.get("source_image", "").strip()
    if not selected_source_image:
        return JsonResponse({"error": "Chưa có ảnh nguồn để AI xem."}, status=400)

    target_model = request.POST.get("model", "kling")
    if target_model not in ("kling", "seedance", "ltx"):
        target_model = "kling"
    prompt_model = request.POST.get("prompt_model") or PROMPT_MODEL_CHOICES[0][0]
    if prompt_model not in dict(PROMPT_MODEL_CHOICES):
        prompt_model = PROMPT_MODEL_CHOICES[0][0]

    try:
        duration = int(request.POST.get("duration", 5))
    except ValueError:
        duration = 5
    duration = fal_video.clamp_duration(target_model, duration)

    image_urls = [_resolve_image_for_vision(selected_source_image)]
    # If this image came from a known product, add a couple more of that
    # product's photos as material/color reference (never as the frame-0
    # image itself — that's always the one actually being animated).
    product_id = request.POST.get("product_id", "").strip()
    if product_id:
        try:
            shop_id = _current_shop_id(request)
            product = pc.get_product(product_id, shop_id)
            for img in product.get("images", []):
                src = img.get("src")
                if src and src != selected_source_image and len(image_urls) < VIDEO_PROMPT_MAX_IMAGES:
                    image_urls.append(src)
        except Exception:
            pass  # reference images are a nice-to-have, never block prompt generation

    instruction = _build_video_prompt_instruction(duration, len(image_urls), target_model)

    try:
        raw = openrouter_client.vision_chat_multi(image_urls, instruction, max_tokens=3000, model=prompt_model)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*|\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    try:
        styles = json.loads(cleaned)
        if not isinstance(styles, list) or not styles:
            raise ValueError("empty or non-list response")
    except (ValueError, json.JSONDecodeError):
        # Model didn't follow the JSON format — fall back to a single
        # style using the raw text so the feature still degrades gracefully.
        styles = [{"key": "ai", "label": "AI gợi ý", "prompt": raw}]

    return JsonResponse({"styles": styles})


def video_gallery(request):
    videos = AiVideo.objects.all()[:300]
    return render(request, "products/video_gallery.html", {"videos": videos, "active_tab": "video"})


def video_delete(request, video_id):
    if request.method != "POST":
        return redirect("video_gallery")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        video = AiVideo.objects.get(id=video_id)
    except AiVideo.DoesNotExist:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Không tìm thấy video."}, status=404)
        messages.error(request, "Không tìm thấy video.")
        return redirect("video_gallery")

    default_storage.delete(video.file_path)
    video.delete()
    if is_ajax:
        return JsonResponse({"ok": True})
    messages.success(request, "Đã xóa video.")
    return redirect("video_gallery")


def publish_product(request, product_id):
    if request.method != "POST":
        return redirect("product_detail", product_id=product_id)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    next_url = _safe_next(request, request.POST.get("next"))
    shop_id = _current_shop_id(request)
    try:
        pc.publish_product(product_id, shop_id)
        if is_ajax:
            return JsonResponse({"ok": True})
        messages.success(request, f"Đã gửi yêu cầu publish sản phẩm {product_id} lên store.")
    except Exception as exc:
        if is_ajax:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"Lỗi khi publish: {exc}")
    return redirect(next_url) if next_url else redirect("product_detail", product_id=product_id)


def delete_product(request, product_id):
    if request.method != "POST":
        return redirect("product_detail", product_id=product_id)
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    next_url = _safe_next(request, request.POST.get("next"))
    shop_id = _current_shop_id(request)
    try:
        pc.delete_product(product_id, shop_id)
        if is_ajax:
            return JsonResponse({"ok": True})
        messages.success(request, "Đã xóa sản phẩm.")
    except Exception as exc:
        if is_ajax:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, f"Lỗi khi xóa: {exc}")
        return redirect(next_url) if next_url else redirect("product_detail", product_id=product_id)
    return redirect(next_url) if next_url else redirect("library_page")


def _safe_decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _slugify_sku(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").upper()).strip("-")
    return re.sub(r"-{2,}", "-", text)[:40]


def _vendor_sku_prefix(vendor: str) -> str:
    letters = re.sub(r"[^A-Za-z0-9]", "", vendor or "").upper()
    return letters[:6] or "SKU"


def _unique_sku(candidate: str, used: set) -> str:
    """Guarantee SKU uniqueness across the whole store (not just this
    crawl job) — AI-composed SKUs for two different products can
    coincidentally collide, and Shopify inventory tracking breaks if two
    variants share a SKU."""
    base = candidate or "ITEM"
    sku = base
    n = 2
    while sku in used:
        sku = f"{base}-{n}"
        n += 1
    used.add(sku)
    return sku


def _existing_skus() -> set:
    used = set()
    for sku, variants in CrawledProduct.objects.values_list("sku", "variants"):
        if sku:
            used.add(sku)
        for v in (variants or []):
            if isinstance(v, dict) and v.get("sku"):
                used.add(v["sku"])
    return used


MAX_VARIANT_COMBOS = 100


def _expand_variants(extracted: dict) -> list[dict]:
    """Build the full variant matrix (cartesian product of every option's
    values) from the AI's `options` (all values per dimension, read off
    the page's swatches/buttons) + `selected_variant` (the one confirmed
    combo's price/sku/etc) + `option_value_images`/`option_value_prices`
    (per-value photos and prices — e.g. every color's own photo, every
    size's own price — instead of just whatever happened to be selected
    when the page loaded). The AI only ever has to read labels off the
    page — never guess a price/image for a combination it can't actually
    see; anything not confirmed just inherits the base product price/
    sku/image as a fallback (handled by shopify_csv.py already)."""
    options = [o for o in (extracted.get("options") or []) if o.get("name") and o.get("values")][:3]
    if not options:
        return []

    value_lists = [o["values"][:20] for o in options]  # guard against one runaway dimension
    combos = list(itertools.product(*value_lists))[:MAX_VARIANT_COMBOS]

    selected = extracted.get("selected_variant") or {}
    selected_values = tuple(selected.get("option_values") or [])

    def _value_map(field: str) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for entry in (extracted.get(field) or []):
            name, value = entry.get("option_name"), entry.get("value")
            payload = entry.get("image") if field == "option_value_images" else entry.get("price")
            if name and value and payload is not None:
                result.setdefault(name, {})[value] = payload
        return result

    value_images = _value_map("option_value_images")
    value_prices = _value_map("option_value_prices")

    variants = []
    for combo in combos:
        variant = {}
        for i, value in enumerate(combo):
            opt_name = options[i]["name"]
            variant[f"option{i + 1}_name"] = opt_name
            variant[f"option{i + 1}_value"] = value
            if value in value_images.get(opt_name, {}):
                variant["image"] = value_images[opt_name][value]
            if value in value_prices.get(opt_name, {}):
                variant["price"] = value_prices[opt_name][value]
        if combo == selected_values:
            for field in ("price", "sku", "barcode", "weight_grams", "image"):
                if selected.get(field) is not None:
                    variant[field] = selected[field]
        variants.append(variant)
    return variants


def _real_compare_at_price(price, compare_at_price):
    """Many source pages always emit a "compare at"/"regular price" field
    in their JSON-LD even when nothing is on sale, so the AI faithfully
    extracts it even when it's identical to (or lower than) the actual
    price — telling the model to "omit if not on sale" isn't reliable
    enough on its own. A compare-at price only means anything if it's
    strictly higher than the selling price; otherwise it's not a real
    discount and would just show a fake/zero "% off" badge on Shopify."""
    if compare_at_price is None or price is None or compare_at_price <= price:
        return None
    return compare_at_price


def _dedupe_repeated_text(text: str) -> str:
    """Some product pages print the description twice in the raw HTML
    (once as visible copy, once in schema.org/JSON-LD metadata), and the
    extraction model concatenates both — often with the first copy cut
    short mid-word where the page truncated it. Detect an exact repeat
    of an early chunk and keep whichever half is longer/more complete."""
    text = (text or "").strip()
    if len(text) < 200:
        return text
    probe = text[:60]
    second = text.find(probe, 60)
    if second == -1:
        return text
    before, after = text[:second].rstrip(), text[second:].rstrip()
    return after if len(after) > len(before) else before


CRAWL_LIMIT_MAX = 100


def _parse_url_list(raw: str) -> list[str]:
    urls = []
    for line in raw.splitlines():
        line = line.strip()
        if line and line.startswith(("http://", "https://")) and line not in urls:
            urls.append(line)
    return urls[:firecrawl_client.MAX_BATCH_URLS]


def crawl_studio(request):
    context = {
        "credits_per_page": firecrawl_client.CREDITS_PER_PAGE,
        "limit_max": CRAWL_LIMIT_MAX,
        "max_batch_urls": firecrawl_client.MAX_BATCH_URLS,
        "default_limit": 20,
    }
    if request.method != "POST":
        return render(request, "products/crawl_studio.html", context)

    mode = request.POST.get("mode", "single")
    if mode not in ("single", "category", "urls"):
        mode = "single"
    source_url = request.POST.get("source_url", "").strip()
    url_list_raw = request.POST.get("url_list", "").strip()
    try:
        limit = int(request.POST.get("limit", 20))
    except ValueError:
        limit = 20
    limit = max(1, min(limit, CRAWL_LIMIT_MAX))
    vendor = request.POST.get("vendor", "").strip()
    try:
        quantity = max(0, int(request.POST.get("quantity", 0)))
    except ValueError:
        quantity = 0

    context.update({
        "source_url": source_url, "url_list": url_list_raw, "mode": mode, "limit": limit,
        "vendor": vendor, "quantity": quantity,
    })

    urls = _parse_url_list(url_list_raw) if mode == "urls" else []
    if mode == "urls" and not urls:
        messages.error(request, "Bạn chưa dán URL nào (mỗi URL 1 dòng).")
        return render(request, "products/crawl_studio.html", context)
    if mode != "urls" and not source_url:
        messages.error(request, "Bạn chưa nhập URL.")
        return render(request, "products/crawl_studio.html", context)
    if not vendor:
        messages.error(request, "Bạn chưa nhập Vendor.")
        return render(request, "products/crawl_studio.html", context)

    job_source_url = ", ".join(urls[:3]) + ("…" if len(urls) > 3 else "") if mode == "urls" else source_url

    try:
        if mode == "category":
            result = firecrawl_client.crawl_category_page(source_url, limit)
        elif mode == "urls":
            result = firecrawl_client.batch_scrape_products(urls)
        else:
            result = firecrawl_client.scrape_product(source_url)
    except Exception as exc:
        messages.error(request, f"Lỗi khi crawl: {exc}")
        return render(request, "products/crawl_studio.html", context)

    products = result.get("products") or []
    if not products:
        messages.error(request, "Không trích xuất được sản phẩm nào từ (các) URL này.")
        return render(request, "products/crawl_studio.html", context)

    job = CrawlJob.objects.create(
        source_url=job_source_url[:1000], mode=mode, vendor=vendor, quantity=quantity,
        credits_used=result.get("credits_used", 0),
    )
    used_skus = _existing_skus()
    vendor_prefix = _vendor_sku_prefix(vendor)
    for p in products:
        extracted = p["extracted"]
        title = (extracted.get("title") or "")[:500]
        product_type = (extracted.get("product_type") or "")[:255]
        # The AI's raw category guess is often a plausible-looking but
        # non-existent taxonomy path (Shopify silently drops anything
        # that doesn't exactly match its Standard Product Taxonomy on
        # import) — snap it to a real one before saving.
        try:
            real_category = shopify_taxonomy.pick_category(
                title, product_type, extracted.get("product_category") or "",
            )
        except Exception:
            real_category = ""
        price = _safe_decimal(extracted.get("price"))
        compare_at_price = _real_compare_at_price(
            price, _safe_decimal(extracted.get("compare_at_price") or None),
        )

        # AI composes a SKU when the source page has none; guarantee it's
        # actually unique store-wide (across every past crawl too) and
        # prefix it with the vendor so it reads like a real internal code.
        raw_base_sku = extracted.get("sku") or title
        product_sku = _unique_sku(f"{vendor_prefix}-{_slugify_sku(raw_base_sku)}", used_skus)
        variants = []
        for v in _expand_variants(extracted):
            # Base the fallback on the short product SKU, not the (often
            # long) title — otherwise _slugify_sku's length cap eats the
            # whole budget before it even reaches the option values, and
            # every variant's SKU collapses to the same truncated title
            # text distinguished only by an arbitrary -2/-3/-4 counter.
            fallback = v.get("sku") or (
                f"{raw_base_sku} {v.get('option1_value', '')} {v.get('option2_value', '')} {v.get('option3_value', '')}"
            )
            v["sku"] = _unique_sku(f"{vendor_prefix}-{_slugify_sku(fallback)}", used_skus)
            variants.append(v)

        CrawledProduct.objects.create(
            job=job,
            source_url=p["source_url"],
            title=title,
            description=_dedupe_repeated_text(extracted.get("description_html")),
            price=price,
            compare_at_price=compare_at_price,
            sku=product_sku[:255],
            barcode=(extracted.get("barcode") or "")[:255],
            weight_grams=_safe_decimal(extracted.get("weight_grams") or None),
            seo_title=(extracted.get("seo_title") or "")[:255],
            seo_description=(extracted.get("seo_description") or "")[:500],
            product_category=real_category[:255],
            product_type=product_type,
            tags=extracted.get("tags") or [],
            images=extracted.get("images") or [],
            variants=variants,
        )

    messages.success(
        request,
        f"Đã crawl {len(products)} sản phẩm — dùng {result.get('credits_used', 0)} credit.",
    )
    return redirect("crawl_detail", job_id=job.id)


def crawl_detail(request, job_id):
    job = get_object_or_404(CrawlJob, id=job_id)
    return render(request, "products/crawl_detail.html", {"job": job, "products": job.products.all()})


def crawl_toggle_product(request, product_id):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)
    try:
        product = CrawledProduct.objects.get(id=product_id)
    except CrawledProduct.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Không tìm thấy."}, status=404)
    product.include_in_export = not product.include_in_export
    product.save(update_fields=["include_in_export"])
    return JsonResponse({"ok": True, "include_in_export": product.include_in_export})


def crawl_export_csv(request, job_id):
    job = get_object_or_404(CrawlJob, id=job_id)
    csv_content = shopify_csv.build_shopify_csv(job)
    response = HttpResponse(csv_content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="shopify_import_{job.id}.csv"'
    return response


def crawl_gallery(request):
    jobs = CrawlJob.objects.all()[:100]
    return render(request, "products/crawl_gallery.html", {"jobs": jobs, "active_tab": "crawl"})


def crawl_delete(request, job_id):
    if request.method != "POST":
        return redirect("crawl_gallery")
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        job = CrawlJob.objects.get(id=job_id)
    except CrawlJob.DoesNotExist:
        if is_ajax:
            return JsonResponse({"ok": False, "error": "Không tìm thấy."}, status=404)
        messages.error(request, "Không tìm thấy.")
        return redirect("crawl_gallery")
    job.delete()
    if is_ajax:
        return JsonResponse({"ok": True})
    messages.success(request, "Đã xóa job crawl.")
    return redirect("crawl_gallery")
