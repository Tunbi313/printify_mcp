from django.contrib import messages
from django.shortcuts import render, redirect
from django.urls import reverse

from . import printify_client as pc
from .templates_catalog import TEMPLATES


def index(request):
    templates = [
        {"key": key, "label": t["label"], "sizes": [v["label"] for v in t["variants"]],
         "artwork_spec": t["artwork_spec"]}
        for key, t in TEMPLATES.items()
    ]
    return render(request, "products/index.html", {"templates": templates})


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
    return redirect("index")
