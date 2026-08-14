import base64
import io
import json
import re
import uuid

from PIL import Image

from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect
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
from . import openrouter_client
from . import printify_client as pc
from .models import AiMockup, AiVideo
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


VIDEO_MOTION_HINTS = {
    "tshirt": "camera slowly orbits around the folded t-shirt, soft studio "
              "lighting, subtle fabric movement, smooth motion",
    "hat": "camera slowly orbits around the hat, soft studio lighting, smooth motion",
    "hat_vintage_a": "camera slowly orbits around the hat, soft studio lighting, smooth motion",
    "hat_vintage_b": "camera slowly orbits around the hat, soft studio lighting, smooth motion",
    "mug": "steam gently rises from the mug, camera slowly zooms in, soft studio lighting",
    "stein_mug": "camera slowly zooms in on the mug, soft studio lighting",
    "tumbler": "camera slowly rotates around the tumbler, soft studio lighting",
    "candle": "the candle flame gently flickers, camera slowly zooms in, warm soft lighting",
    "can_glass_engraved": "camera slowly rotates around the glass, light glinting off the etched design",
    "poster_matte_2x3": "camera slowly zooms in on the poster, subtle parallax, soft lighting",
    "canvas_2x3": "camera slowly zooms in on the canvas, subtle parallax, soft lighting",
    "sticker": "camera slowly zooms in on the sticker, subtle light glint",
}
GENERIC_VIDEO_MOTION = "camera slowly zooms in on the product, smooth subtle motion, professional product photography style"


def _guess_video_motion(product: dict) -> str:
    blueprint_id = product.get("blueprint_id")
    for key, tpl in TEMPLATES.items():
        if tpl.get("blueprint_id") == blueprint_id:
            return VIDEO_MOTION_HINTS.get(key, GENERIC_VIDEO_MOTION)
    return GENERIC_VIDEO_MOTION


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


def video_studio_index(request):
    try:
        shop_id = _current_shop_id(request)
        published = _list_published_products(shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được danh sách sản phẩm: {exc}")
        published = []

    paginator = Paginator(published, VIDEO_STUDIO_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "products/video_studio_index.html", {"page_obj": page_obj})


def video_studio(request, product_id):
    shop_id = _current_shop_id(request)
    try:
        product = pc.get_product(product_id, shop_id)
    except Exception as exc:
        messages.error(request, f"Không lấy được sản phẩm: {exc}")
        return redirect("index")

    external = product.get("external") or {}
    if not external.get("id"):
        messages.error(request, "Video AI chỉ áp dụng cho sản phẩm đã publish lên store.")
        return redirect("video_studio_index")

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
    source_images = [c["src"] for c in image_choices]
    context = {
        "product": product,
        "source_images": source_images,
        "image_choices": image_choices,
        "default_prompt": _guess_video_motion(product),
        "duration_min": fal_video.DURATION_MIN,
        "duration_max": fal_video.DURATION_MAX,
        "resolutions": fal_video.SEEDANCE_RESOLUTIONS,
        "aspect_ratios": fal_video.ASPECT_RATIOS,
        "generate_audio": True,
        "selected_duration": 6,
    }

    if request.method != "POST":
        return render(request, "products/video_studio.html", context)

    source_image = request.POST.get("source_image", "")
    model = request.POST.get("model", "kling")
    if model not in ("kling", "seedance", "ltx"):
        model = "kling"
    prompt = request.POST.get("prompt", "").strip()
    duration = fal_video.clamp_duration(model, request.POST.get("duration"))
    resolution = request.POST.get("resolution", "720p")
    aspect_ratio = request.POST.get("aspect_ratio", "16:9")
    if aspect_ratio not in fal_video.ASPECT_RATIOS:
        aspect_ratio = fal_video.ASPECT_RATIOS[0]
    if model == "ltx":
        aspect_ratio = "16:9"  # LTX only outputs 16:9, no param to change it
    generate_audio = request.POST.get("generate_audio") == "on"

    context.update({
        "selected_source_image": source_image,
        "selected_model": model,
        "prompt": prompt,
        "selected_duration": duration,
        "selected_resolution": resolution,
        "selected_aspect_ratio": aspect_ratio,
        "generate_audio": generate_audio,
    })

    if source_image not in source_images:
        messages.error(request, "Ảnh gốc không hợp lệ.")
        return render(request, "products/video_studio.html", context)
    if not prompt:
        messages.error(request, "Bạn chưa mô tả chuyển động cho video.")
        return render(request, "products/video_studio.html", context)

    try:
        if model == "seedance":
            if resolution not in fal_video.SEEDANCE_RESOLUTIONS:
                resolution = fal_video.SEEDANCE_RESOLUTIONS[0]
            video_bytes, content_type = fal_video.generate_seedance_video(
                source_image, prompt, duration, resolution, aspect_ratio, generate_audio,
            )
        elif model == "ltx":
            resolution = "1080p"
            video_bytes, content_type = fal_video.generate_ltx_video(
                source_image, prompt, duration,
            )
        else:
            resolution = "720p"  # Kling has no resolution knob; kept for cost estimate consistency
            video_bytes, content_type = fal_video.generate_kling_video(
                source_image, prompt, duration, aspect_ratio,
            )
    except Exception as exc:
        messages.error(request, f"Lỗi khi tạo video: {exc}")
        return render(request, "products/video_studio.html", context)

    cost = fal_video.estimate_cost_usd(model, duration, resolution)
    filename = f"ai_videos/{product_id}_{uuid.uuid4().hex[:8]}.mp4"
    saved_path = default_storage.save(filename, ContentFile(video_bytes))
    result_video = AiVideo.objects.create(
        product_id=product_id, product_title=product.get("title", ""),
        model=model, prompt=prompt, duration=str(duration), aspect_ratio=aspect_ratio,
        cost_usd=cost, file_path=saved_path,
    )
    context["result_video"] = result_video
    return render(request, "products/video_studio.html", context)


VIDEO_PROMPT_MAX_IMAGES = 5


def video_generate_prompts(request, product_id):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    shop_id = _current_shop_id(request)
    try:
        product = pc.get_product(product_id, shop_id)
    except Exception as exc:
        return JsonResponse({"error": f"Không lấy được sản phẩm: {exc}"}, status=502)

    original_images = [img.get("src") for img in product.get("images", []) if img.get("src")]
    if not original_images:
        return JsonResponse({"error": "Sản phẩm chưa có ảnh mockup nào."}, status=400)

    # Scope the vision call to whichever photo the user actually selected as
    # the video's source (radio button) — including AI mockups (e.g. a
    # model holding/wearing the product) — so the written prompt matches
    # what will really be on screen at frame 0, not a generic blend of
    # unrelated angles.
    selected_source_image = request.POST.get("source_image", "").strip()
    mockup_by_url = {m.url: m for m in AiMockup.objects.filter(product_id=product_id)}
    selected_mockup = mockup_by_url.get(selected_source_image)
    is_model_shot = bool(selected_mockup and selected_mockup.kind == "model")

    image_urls = []
    if selected_mockup:
        # AI mockups may be stored on local disk in dev (a relative
        # /media/ path OpenRouter's servers can't fetch), so read the
        # bytes ourselves and inline them as a data: URL instead of
        # trusting the storage URL to be publicly reachable.
        try:
            with default_storage.open(selected_mockup.file_path, "rb") as f:
                image_urls.append(_bytes_to_data_url(f.read(), "image/png"))
        except OSError:
            pass
    elif selected_source_image:
        image_urls.append(selected_source_image)
    for src in original_images:
        if src not in image_urls:
            image_urls.append(src)
    image_urls = image_urls[:VIDEO_PROMPT_MAX_IMAGES]

    try:
        duration = int(request.POST.get("duration", 6))
    except ValueError:
        duration = 6
    duration = max(fal_video.DURATION_MIN, min(fal_video.DURATION_MAX, duration))
    if duration <= 8:
        pacing_note = (
            f"The video will be exactly {duration} seconds long — that's "
            "only enough time for ONE simple continuous motion. Do not "
            "describe multiple beats or a sequence of events."
        )
    else:
        pacing_note = (
            f"The video will be exactly {duration} seconds long — long "
            "enough that a single static motion would feel repetitive or "
            "loop-y. Describe a short sequence of 2-3 distinct motion beats "
            "in order (e.g. establish the product, then move/reveal a "
            "detail, then a final settling shot), paced to fill the full "
            f"{duration}s runtime without feeling static."
        )

    craft_rules = (
        "CAMERA CRAFT — follow these rules used by professional motion "
        "directors:\n"
        "- One primary motion idea per prompt. Never stack unrelated moves "
        "(rotate AND zoom AND pan at once) — image-to-video models blend "
        "stacked motion into a blurry mess. Pick one clear camera move and "
        "commit to it for the whole clip.\n"
        "- The opening instant is already the strongest frame (the source "
        "photo) — never describe a fade-in, blur-to-focus, or slow reveal "
        "from black. Motion starts immediately, not after a build-up.\n"
        "- Keep scale/zoom change subtle and eased: a slow push-in or "
        "pull-back that accelerates in and decelerates out, never a snap "
        "or crash-zoom, never linear/mechanical.\n"
        "- Name the camera move with real cinematography terms — orbit, "
        "turntable, dolly/push-in, pan, tilt, parallax drift — not vague "
        "words like \"nice motion\" or \"cool effect.\"\n"
        "- Give lighting/mood in one short clause (e.g. \"soft studio key "
        "light\", \"warm golden-hour light\") so the render stays "
        "consistent instead of flat or overexposed.\n\n"
    )

    framing_note = (
        "The FIRST image below is the EXACT photo that will be used as the "
        "video's starting frame (frame 0) — build every prompt assuming "
        "that is what's on screen. Any additional photos are just other "
        "angles of the same product for material/color reference only — "
        "never describe a crop, background or detail that is only visible "
        "in a reference photo and not in the first image.\n\n"
        if len(image_urls) > 1 else ""
    )

    if is_model_shot:
        style_instruction = (
            "The first image shows a REAL PERSON naturally holding/wearing/"
            "using the product — not a plain product-only shot. Write 4 "
            "DISTINCT prompts for animating THIS photo, each a different "
            "style. Because it's a photorealistic person, keep every move "
            "small and physically plausible — exaggerated or fast motion "
            "on a real face/body reads as broken or uncanny; never warp a "
            "face or limb:\n"
            "1. \"natural\" — subtle life-like motion only: a soft breath, "
            "gentle hair or fabric sway, a slow blink or small natural "
            "head movement; camera holds still or drifts almost "
            "imperceptibly.\n"
            "2. \"detail\" — camera slowly pushes in from the person "
            "toward the product they're holding/wearing, settling close "
            "on the print/design; the person stays nearly still "
            "throughout.\n"
            "3. \"turn\" — the person makes one slow, continuous, natural "
            "turn (body or head) so the product reads from a slightly "
            "different angle; no jerky or repeated movement.\n"
            "4. \"dynamic\" — the most energetic of the four but still "
            "human-plausible: one small natural gesture (e.g. adjusting "
            "the product, a soft smile) paired with a slightly quicker "
            "camera push-in, social-ad energy without breaking realism.\n\n"
        )
        style_keys = (
            '[{"key": "natural", "label": "Chuyển động tự nhiên", "prompt": "..."}, '
            '{"key": "detail", "label": "Cận cảnh sản phẩm", "prompt": "..."}, '
            '{"key": "turn", "label": "Xoay người nhẹ", "prompt": "..."}, '
            '{"key": "dynamic", "label": "Năng động, quảng cáo", "prompt": "..."}] '
        )
    else:
        style_instruction = (
            "Write 4 DISTINCT camera/motion prompts for animating this "
            "exact product, each a different style:\n"
            "1. \"rotate\" — a clean, professional turntable-style product "
            "shot: either the product slowly rotates in place or the "
            "camera orbits around it at a constant, even speed, product "
            "centered and anchored, soft studio lighting.\n"
            "2. \"zoom\" — a slow, eased dolly/push-in that settles close "
            "on one specific real detail visible in the photos (texture, "
            "print, seam, material) — subtle scale change, never a crash-"
            "zoom.\n"
            "3. \"lifestyle\" — the product in a natural real-world "
            "setting implied by the product type, a gentle Ken-Burns-style "
            "pan or drift (one direction, slow, eased), soft ambient "
            "light.\n"
            "4. \"dynamic\" — the fastest, punchiest camera move of the "
            "four (a quicker push-in or a short parallax swing) for a "
            "social-media-ad feel — still ONE motion performed with more "
            "energy/speed, not multiple stacked moves.\n\n"
        )
        style_keys = (
            '[{"key": "rotate", "label": "Xoay chuyên nghiệp", "prompt": "..."}, '
            '{"key": "zoom", "label": "Zoom cận cảnh", "prompt": "..."}, '
            '{"key": "lifestyle", "label": "Bối cảnh đời thường", "prompt": "..."}, '
            '{"key": "dynamic", "label": "Năng động, quảng cáo", "prompt": "..."}] '
        )

    instruction = (
        "You are a creative director for short AI-generated product promo "
        "videos (used with image-to-video models like Kling/Seedance/LTX). "
        f"You are shown {len(image_urls)} photo(s) related to the SAME "
        "product. Study the product's actual shape, material, color, and "
        "printed design in the images.\n\n"
        f"{framing_note}"
        f"{pacing_note}\n\n"
        f"{craft_rules}"
        f"{style_instruction}"
        "Each prompt must be in English, 1-2 sentences, describing ONLY "
        "camera/lighting/motion, correctly paced for the exact duration "
        "stated above — never invent new colors, text, logos or shapes "
        "that contradict what is actually in the photos.\n\n"
        "Respond with ONLY a JSON array, no markdown fences, no extra "
        "text, no pretty-printing — a single compact line with no line "
        f"breaks or indentation, in this exact shape: {style_keys}"
        "— \"label\" must be the Vietnamese labels given above, \"prompt\" in English."
    )

    try:
        raw = openrouter_client.vision_chat_multi(image_urls, instruction, max_tokens=2000)
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
