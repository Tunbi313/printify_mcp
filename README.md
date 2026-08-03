# Printify MCP Server — Sunfade Co

A small, purpose-built MCP server that lets Claude create and publish
print-on-demand products on Printify from reusable SKU templates.

The whole idea: **you never hand-assemble Printify's 200-line product JSON.**
Claude calls `create_from_template` with ~5 fields and the server expands it.

---

## 1. Install

```bash
cd printify-mcp
pip install -r requirements.txt
```

## 2. Set your token (never hard-code it)

Create your token at: Printify → Account → Connections → API → Generate.
Scopes needed: `shops.read`, `products.read`, `products.write`,
`uploads.read`, `uploads.write`, `print_providers.read`.

```bash
export PRINTIFY_TOKEN="your_token_here"
export PRINTIFY_SHOP_ID="21788377"   # optional default shop
```

On Windows PowerShell:
```powershell
$env:PRINTIFY_TOKEN="your_token_here"
$env:PRINTIFY_SHOP_ID="21788377"
```

## 3. Run standalone (sanity test)

```bash
python server.py
```

## 4. Connect to Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "printify-sunfade": {
      "command": "python",
      "args": ["/ABSOLUTE/PATH/printify-mcp/server.py"],
      "env": {
        "PRINTIFY_TOKEN": "your_token_here",
        "PRINTIFY_SHOP_ID": "21788377"
      }
    }
  }
}
```

Restart Claude Desktop. The tools appear automatically.

---

## Tools

| Tool | What it does |
|------|--------------|
| `list_shops` | List connected shops (id, title, channel) |
| `list_templates` | Show available SKU templates + artwork spec |
| `upload_artwork(image_url, file_name)` | Upload a PNG from a public URL → image_id |
| `upload_artwork_file(file_path, file_name)` | Upload a PNG from a **local file** (base64) → image_id |
| `create_from_template(...)` | **Core.** Make one product from a template (draft) |
| `batch_create(template_name, items)` | Make many products from already-uploaded images (paced) |
| `scan_folder(folder_path)` | List local image files in a folder, without uploading |
| `batch_from_folder(template_name, items)` | Upload local files **and** create products in one call (paced) |
| `publish_product(product_id)` | Push a product to the connected storefront |
| `get_product(product_id)` | Verify a product |
| `list_print_providers(blueprint_id)` | For adding new SKU templates |

### Typical flow

1. Claude generates artwork → gets a public PNG URL (or saves a local file)
2. `upload_artwork(url, "Yosemite.png")` or `upload_artwork_file(path, "Yosemite.png")` → `image_id`
3. `create_from_template("poster_matte_2x3", image_id, title, desc, tags)` → draft
4. Review with `get_product`
5. `publish_product(product_id)` when ready

`batch_create` chains step 3 for a list of already-uploaded designs.
`scan_folder` + `batch_from_folder` chain steps 2–3 for a whole folder of local files.

---

## Current templates

| Template | Product | Blueprint / Provider | Sizes / Price | Artwork spec |
|----------|---------|----------------------|----------------|--------------|
| `poster_matte_2x3` | Satin & Archival Matte Poster | 1309 / 234 (Today's Graphics) | 12×18 $29, 20×30 $42, 24×36 $52 | 7200×10800 px PNG, 2:3, sRGB, full-bleed |
| `canvas_2x3` | Classic Stretched Canvas (1.5") | 1238 / 48 (Colorway) | 12×18 $69 … 24×36 $129 | 7200×10800 px PNG, 2:3, sRGB, full-bleed (same file as poster) |
| `sticker` | Square Sticker (die-cut, UV) | 384 / 1 (SPOKE) | 2×2 $5.99 … 6×6 $9.99 | 1800×1800 px PNG, 1:1, transparent bg |
| `mug` | Ceramic Mug 11oz (dye-sub) | 68 / 1 (SPOKE) | 11oz $21.99 | 2700×1119 px PNG, wraparound |
| `tshirt` | Unisex Softstyle Tee (Gildan 64000) | 145 / 99 | 5 colors × 5 sizes, $29.99–$32.99 | 3852×4398 px PNG, transparent, front/back |
| `hat` | Trucker Hat (DTF) | 1128 / 41 (Duplium) | 5 colors, $28.99 | 1650×750 px PNG, 2.2:1, transparent |
| `hat_vintage_a` | Embroidery Dad Hat | 1729 / 99 | 3 colors, $31.99 | Text-only embroidery, front 1650×600, back 600×300 |
| `hat_vintage_b` | Embroidery Vintage Cap | 1736 / 99 | 4 colors, $35.99 | Text-only embroidery, front 1650×600, back 600×300 |
| `candle` | Scented Soy Candle 9oz | 1048 / 219 | 3 scents, $26.99 | 900×600 px PNG, 3:2 label |
| `tumbler` | SPOKE Tumbler 20oz | 353 / 1 | 20oz $40.83 | 2795×2100 px PNG, wraparound |
| `stein_mug` | Ceramic Stein Mug 22oz (dye-sub) | 891 / 41 (Duplium) | 22oz $25.50 | 2250×900 px PNG, 2.5:1 wraparound, full-bleed |
| `pillow_damask` | Outdoor Pillow, double-sided | 1007 / 10 | 16×16/18×18/20×20 $33.99 | 1254–3225 px square PNG, full-bleed, no transparency |
| `pillow_woven` | Boho Woven Pillow | 1650 / 10 | 17×18 $47.99 | 1728×1824 px, bold color-blocks only (no gradients/fine detail) |
| `pillow_body` | Body Pillow 20×54, double-sided | 2758 / 10 | Microfiber $95.43, Fleece $108.23 | 8325×3225 px JPEG, ~2.58:1, pattern spans full width |
| `can_glass_engraved` | Can Glass 16oz (laser engraving) | 2759 / 228 | 16oz $24.25 | 1200×1800 px PNG, transparent = not engraved, line art only |

Full spec per template (finish options, decoration method, placeholder transform,
style notes) lives in the `TEMPLATES` dict in [`server.py`](server.py).

---

## Adding a new SKU template (mug, sticker, etc.)

1. Create ONE product of that type manually in Printify, enable only the
   variants you want, save as draft.
2. GET its JSON:
   ```bash
   curl.exe -H "Authorization: Bearer $TOKEN" \
     "https://api.printify.com/v1/shops/SHOP_ID/products/PRODUCT_ID.json" > sku.json
   ```
3. From the JSON, copy: `blueprint_id`, `print_provider_id`, the enabled
   variant `id`s, and the `print_areas[0].placeholders[0].images[0]`
   transform (x, y, scale, angle).
4. Add an entry to `TEMPLATES` in `server.py` following the poster example.
5. Restart the server.

---

## Notes / gotchas

- **Prices are in cents** (2900 = $29.00).
- Products are created as **drafts** (unpublished). Nothing goes live until
  you call `publish_product`.
- `publish_product` only works against a **real connected storefront**
  (Shopify), not the API `custom_integration` placeholder shop. Connect
  Sunfade Co in Printify → Add store → Shopify first.
- Rate limits: global 600 req/min; publish 200/30min. `batch_create` paces
  at ~1/sec, well under both. 429s are retried with backoff automatically.
- Base costs shown are Free-plan. Printify Premium (~20% off) improves margins.
