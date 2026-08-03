# Printify Web — Sunfade Co

A small Django web app for creating Printify products from the browser:
pick a template, upload artwork, fill in title/description/tags, preview
the mockups, then publish to the store or delete it.

This is standalone — it does not import or depend on `../server.py`
(the Claude MCP server). They share the same SKU template data by
convention, kept in two separate files.

---

## 1. Install

```bash
cd webapp
pip install -r requirements.txt
```

## 2. Set your token

Create your token at: Printify → Account → Connections → API → Generate.
Scopes needed: `shops.read`, `products.read`, `products.write`,
`uploads.read`, `uploads.write`.

```bash
cp .env.example .env
```

Edit `.env`:

```
PRINTIFY_TOKEN=your_token_here
PRINTIFY_SHOP_ID=your_shop_id_here
```

`.env` is gitignored — never commit real tokens (only `.env.example`
with placeholders is tracked).

## 3. Run migrations (first time only)

```bash
python manage.py migrate
```

## 4. Start the server

```bash
python manage.py runserver
```

Open **http://127.0.0.1:8000/**

---

## Using the web app

### Create a product

1. On the home page, choose a **template** from the dropdown — sizes and
   the required artwork spec appear underneath.
2. Upload the design file matching that spec (PNG/JPG).
3. Fill in **Title**, **Description**, and **Tags** (comma-separated).
4. Click **"Tạo draft trên Printify"**. The button locks itself while the
   upload + product creation is in progress — this prevents accidentally
   creating duplicate products from repeated clicks.
5. You're redirected to the product's detail page.

### Preview

Printify renders mockup images asynchronously, so they may not show up
immediately. If the images are missing, click **"Tải lại"** a few
seconds later to refresh.

The product is created as a **draft** — nothing is visible on your store
yet at this point.

### Publish or delete

On the detail page:

- **Publish lên store** — pushes the product live to the connected sales
  channel (Shopify). Requires confirmation. Once published, a link to
  the live product page appears.
- **Xóa** — permanently deletes the product from Printify (and from the
  store if it was already published). Requires confirmation. Use this if
  you don't like how it turned out.

Both actions lock their button after confirmation to avoid double
submission.

---

## Notes / gotchas

- Prices and variant IDs come from the same `TEMPLATES` catalog as the
  MCP server (`products/templates_catalog.py`), kept manually in sync
  with `../server.py`. Add new SKUs to both files if needed.
- `PRINTIFY_TOKEN` is only ever read server-side (env var) — it is never
  sent to the browser.
- Uploads up to 25 MB are accepted (see `DATA_UPLOAD_MAX_MEMORY_SIZE` in
  `printify_web/settings.py`); large poster/canvas files (7200×10800 px)
  fit comfortably under this.
- This app is for local/personal use — `DEBUG = True` and no auth are
  set up. Don't expose it to the public internet as-is.
