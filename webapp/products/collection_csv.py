"""Build the compact CSV produced by the whole-collection crawl screen."""

import csv
import io
from decimal import Decimal, InvalidOperation


_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CAD": "CA$",
    "AUD": "A$",
}


def _format_price(value, currency: str = "") -> str:
    if value is None or value == "":
        return ""
    # A storefront fast path can preserve the exact visible compare-at text
    # (for example "$80.00 USD"). Do not reformat that into a bare number.
    value_text = str(value).strip()
    try:
        amount = f"{Decimal(value_text):.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return value_text
    currency = (currency or "").upper()
    if not currency:
        return amount
    symbol = _CURRENCY_SYMBOLS.get(currency, "")
    return f"{symbol}{amount} {currency}".strip()


def build_collection_csv(products: list[dict]) -> str:
    """Match the supplied catalogue CSV: one row and numbered images."""
    max_images = max(
        10,
        max((len(product.get("images") or []) for product in products), default=0),
    )
    image_headers = [f"image_{index}" for index in range(1, max_images + 1)]
    headers = [
        "title",
        "price",
        "compare_at_price",
        "product_url",
        "image_count",
        *image_headers,
    ]

    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=headers, lineterminator="\r\n")
    writer.writeheader()

    for product in products:
        images = product.get("images") or []
        currency = product.get("currency") or ""
        row = {
            "title": product.get("title") or "",
            "price": _format_price(product.get("price"), currency),
            "compare_at_price": _format_price(product.get("compare_at_price"), currency),
            "product_url": product.get("product_url") or "",
            "image_count": len(images),
        }
        for index, image in enumerate(images, start=1):
            row[f"image_{index}"] = image
        writer.writerow(row)

    return buf.getvalue()
