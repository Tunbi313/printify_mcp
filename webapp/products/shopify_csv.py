"""
Builds a Shopify product-import CSV from a CrawlJob's extracted products.
Follows Shopify's documented convention: the first row of a product
carries all product-level fields, extra variant/image rows repeat only
the Handle. Column set is aligned to Shopify's real product-export
template — store-specific custom metafields (e.g. a candle store's
material/scent metafields) are intentionally left out since they don't
generalize across stores; only the universal fields are included.
"""

import csv
import io

from django.utils.text import slugify

CSV_HEADERS = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Product Category", "Type", "Tags", "Published",
    "Option1 Name", "Option1 Value", "Option1 Linked To",
    "Option2 Name", "Option2 Value", "Option2 Linked To",
    "Option3 Name", "Option3 Value", "Option3 Linked To",
    "Variant SKU", "Variant Grams", "Variant Inventory Tracker", "Variant Inventory Qty",
    "Variant Inventory Policy", "Variant Fulfillment Service",
    "Variant Price", "Variant Compare At Price",
    "Variant Requires Shipping", "Variant Taxable", "Variant Barcode",
    "Image Src", "Image Position", "Image Alt Text", "Variant Image",
    "Gift Card", "SEO Title", "SEO Description", "Variant Weight Unit",
    "Cost per item", "Status",
]


def _unique_handle(title: str, used: set) -> str:
    base = slugify(title)[:80] or "san-pham"
    handle = base
    n = 2
    while handle in used:
        handle = f"{base}-{n}"
        n += 1
    used.add(handle)
    return handle


def _fmt_number(value) -> str:
    return "" if value is None else f"{value:.2f}"


def _blank_row() -> dict:
    return dict.fromkeys(CSV_HEADERS, "")


def _variant_rows(product, handle: str) -> list[dict]:
    variant_list = product.variants or [{}]
    rows = []
    for i, variant in enumerate(variant_list):
        row = _blank_row()
        row["Handle"] = handle
        if i == 0:
            row["Title"] = product.title
            row["Body (HTML)"] = product.description
            row["Vendor"] = product.job.vendor
            row["Product Category"] = product.product_category
            row["Type"] = product.product_type
            row["Tags"] = ", ".join(product.tags or [])
            row["Published"] = "TRUE"
            row["Gift Card"] = "FALSE"
            row["SEO Title"] = product.seo_title
            row["SEO Description"] = product.seo_description
            row["Cost per item"] = _fmt_number(product.price)
            row["Status"] = "draft"

        has_options = any(variant.get(f"option{n}_name") for n in (1, 2, 3))
        if len(variant_list) == 1 and not has_options:
            row["Option1 Name"] = "Title"
            row["Option1 Value"] = "Default Title"
        else:
            for n in (1, 2, 3):
                row[f"Option{n} Name"] = variant.get(f"option{n}_name", "")
                row[f"Option{n} Value"] = variant.get(f"option{n}_value", "")

        row["Variant SKU"] = variant.get("sku") or product.sku
        row["Variant Grams"] = _fmt_number(variant.get("weight_grams") or product.weight_grams) or "0"
        row["Variant Inventory Tracker"] = "shopify"
        row["Variant Inventory Qty"] = str(product.job.quantity)
        row["Variant Inventory Policy"] = "deny"
        row["Variant Fulfillment Service"] = "manual"
        row["Variant Price"] = _fmt_number(variant.get("price") or product.price)
        row["Variant Compare At Price"] = _fmt_number(product.compare_at_price)
        row["Variant Requires Shipping"] = "TRUE"
        row["Variant Taxable"] = "TRUE"
        row["Variant Barcode"] = variant.get("barcode") or product.barcode
        row["Variant Image"] = variant.get("image", "")
        row["Variant Weight Unit"] = "g"
        rows.append(row)
    return rows


def _attach_images(rows: list[dict], images: list[str], handle: str, title: str) -> None:
    for i, image_src in enumerate(images):
        if i < len(rows):
            target = rows[i]
        else:
            target = _blank_row()
            target["Handle"] = handle
            rows.append(target)
        target["Image Src"] = image_src
        target["Image Position"] = str(i + 1)
        target["Image Alt Text"] = title


def build_shopify_csv(job) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_HEADERS)
    writer.writeheader()

    used_handles = set()
    for product in job.products.filter(include_in_export=True):
        handle = _unique_handle(product.title or "san-pham", used_handles)
        rows = _variant_rows(product, handle)
        _attach_images(rows, product.images or [], handle, product.title)
        for row in rows:
            writer.writerow(row)

    return buf.getvalue()
