# 📦 eBay Listing Generator

Turn product photos into a complete, ready-to-publish eBay listing.

Upload one or more images → the app **optimizes** them for eBay, uses Claude's
vision **"lens"** to identify the item, **generates** a full listing (title,
description, item specifics, suggested price/category), shows an **editable
preview** where you can tweak fields manually or with a prompt, then **pushes**
the result to eBay as a draft or live listing (or generates the exact API
payload when you don't have eBay credentials yet).

## Pipeline

```
 Upload images ──▶ Optimize (Pillow) ──▶ Identify (Claude vision) ──▶
 Editable preview (manual edits + prompt refine) ──▶ Publish (eBay API / dry-run)
```

| Stage | What happens | Tech |
|-------|--------------|------|
| Optimize | Auto-orient, trim plain borders, pad to square on a clean canvas, upscale to 1600px, mild enhance | Pillow |
| Identify | Photos sent to Claude vision; returns structured listing draft + confidence + "missing info" to verify | Anthropic API |
| Preview | Edit every field; add/remove item specifics; refine with a natural-language prompt | Web UI |
| Category | Resolves a numeric eBay leaf categoryId from the item via the Taxonomy API (auto during identify + a "Suggest categories" picker in the preview) | eBay Taxonomy API |
| Publish | Builds eBay Inventory API payloads and (if configured) creates an offer / publishes it | eBay Sell API |

## Quick start

```bash
# 1. Add your Anthropic key
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=...

# 2. Run it
./run.sh
# or manually:
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -r requirements.txt
#   uvicorn backend.main:app --reload

# 3. Open http://localhost:8000
```

Only `ANTHROPIC_API_KEY` is required to get the full upload → identify →
optimize → preview flow working. eBay credentials are optional.

## eBay credentials (optional)

Without eBay credentials the app runs in **dry-run mode**: it builds the exact
`createOrReplaceInventoryItem` / `createOffer` / `publishOffer` payloads and
saves them to `data/exports/` so you can inspect them or push later.

To publish for real, create a developer app at
<https://developer.ebay.com/> and fill these in `.env`:

- `EBAY_ENV` — `sandbox` (recommended first) or `production`
- A **user** OAuth access token (`EBAY_OAUTH_TOKEN`) or the
  `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` / `EBAY_REFRESH_TOKEN` trio
  (scope `https://api.ebay.com/oauth/api_scope/sell.inventory`)
- Business policy IDs: `EBAY_FULFILLMENT_POLICY_ID`, `EBAY_PAYMENT_POLICY_ID`,
  `EBAY_RETURN_POLICY_ID`, and `EBAY_MERCHANT_LOCATION_KEY`

### Automatic category IDs (lighter requirements)

Resolving a numeric category ID uses the **Taxonomy API**, which only needs an
*application* token — i.e. just `EBAY_CLIENT_ID` and `EBAY_CLIENT_SECRET` (no
user login or seller policies). Set those two and the app will:

- auto-fill `category_id` during identification, and
- show a **"Suggest eBay categories"** picker in the preview so you can choose
  the best-matching category (root → leaf path + numeric ID).

Without them, you can still type a category ID manually in the preview.

> **Note on images:** eBay requires publicly reachable image URLs. In local
> dry-run mode the payload references `http://localhost:8000/media/...`. For real
> publishing, deploy the app on a public host (or swap in an image CDN) so eBay
> can fetch the optimized photos.

## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/health` | Config status (AI / eBay) |
| `POST` | `/api/upload` | Upload images (multipart) → optimize → `session_id` |
| `POST` | `/api/identify/{session_id}` | Claude vision → listing draft |
| `POST` | `/api/refine` | Refine the draft from a prompt |
| `POST` | `/api/save/{session_id}` | Persist manual edits |
| `POST` | `/api/category-suggestions` | Ranked eBay category IDs for a query (Taxonomy API) |
| `POST` | `/api/publish` | Push to eBay (draft/live) or dry-run |

## Project layout

```
backend/
  main.py            FastAPI app + routes
  config.py          env / settings
  models.py          Pydantic models (Listing, etc.)
  storage.py         per-session filesystem store
  services/
    images.py        Pillow optimization
    claude_ai.py     vision identify + prompt refine
    taxonomy.py      Taxonomy API -> numeric category IDs
    ebay.py          Inventory API payloads + publish/dry-run
frontend/
  index.html         upload + editable preview UI
  app.js             client logic
  style.css          styling
```

## Notes & limitations

- The AI never invents serial numbers, authenticity guarantees, or unverifiable
  specs — it flags those under "missing info" for you to confirm.
- Image optimization is intentionally non-destructive of subject framing; the
  border auto-crop only triggers on clearly plain backgrounds.
- Category ID is auto-resolved via the eBay Taxonomy API when
  `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` are set; otherwise enter it manually in
  the preview. Taxonomy data in the eBay **sandbox** is limited, so category
  suggestions are most accurate against production.
