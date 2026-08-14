from django.core.cache import cache

from . import printify_client as pc


def shop_context(request):
    """Makes the list of connected Printify shops and which one is
    currently active (session-scoped) available in every template, so the
    shop switcher in the sidebar works without every view wiring it up."""
    shops = cache.get("printify_shops")
    if shops is None:
        try:
            shops = pc.list_shops()
        except Exception:
            shops = []
        cache.set("printify_shops", shops, 300)

    current_id = request.session.get("shop_id")
    if not current_id:
        try:
            current_id = pc.default_shop_id()
        except Exception:
            current_id = None

    return {
        "available_shops": shops,
        "current_shop_id": str(current_id) if current_id else None,
    }
