import base64
import io
import re
import uuid

from PIL import Image

from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import reverse

from . import fal_studio
from . import fal_upscale
from . import openrouter_client
from . import printify_client as pc
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
        shop_id = pc.default_shop_id()
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
        shop_id = pc.default_shop_id()
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
        shop_id = pc.default_shop_id()
        products = pc.list_products(shop_id, limit=50)
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
    return render(request, "products/library.html", {"drafts": drafts})


def product_detail(request, product_id):
    shop_id = pc.default_shop_id()
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


def publish_product(request, product_id):
    if request.method != "POST":
        return redirect("product_detail", product_id=product_id)
    shop_id = pc.default_shop_id()
    try:
        pc.publish_product(product_id, shop_id)
        messages.success(request, "Đã gửi yêu cầu publish lên store.")
    except Exception as exc:
        messages.error(request, f"Lỗi khi publish: {exc}")
    return redirect("product_detail", product_id=product_id)


def delete_product(request, product_id):
    if request.method != "POST":
        return redirect("product_detail", product_id=product_id)
    shop_id = pc.default_shop_id()
    try:
        pc.delete_product(product_id, shop_id)
        messages.success(request, "Đã xóa sản phẩm.")
    except Exception as exc:
        messages.error(request, f"Lỗi khi xóa: {exc}")
        return redirect("product_detail", product_id=product_id)
    return redirect("library_page")
