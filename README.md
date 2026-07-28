# QuickFlip

**Snap it · AI writes it · list it on eBay.**

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

## Deploy to Fly.io

A `Dockerfile` and `fly.toml` are included. eBay requires **publicly reachable
HTTPS image URLs**, so deploying (vs. running on localhost) is what makes real
publishing work — the app uses its public origin for `imageUrls`.

```bash
fly launch --no-deploy        # or: fly apps create <name> ; edit fly.toml `app`
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
# eBay sandbox creds (see below):
fly secrets set EBAY_OAUTH_TOKEN=... \
  EBAY_FULFILLMENT_POLICY_ID=... EBAY_PAYMENT_POLICY_ID=... \
  EBAY_RETURN_POLICY_ID=... EBAY_MERCHANT_LOCATION_KEY=...
fly deploy
```

`EBAY_ENV=sandbox` is preset in `fly.toml`. The app listens on `$PORT` (8080)
and runs uvicorn with `--proxy-headers` so it sees Fly's HTTPS origin. Uploaded
files live on the container's ephemeral disk by default; uncomment the
`[mounts]` block in `fly.toml` (and create a volume) to persist them.

> **Sandbox keysets don't need eBay's "Alerts & Notifications" page.** The one
> notification eBay mandates — *Marketplace Account Deletion* — applies to
> **Production** keysets only, and the app now ships the endpoint for it (see
> below).

### Marketplace Account Deletion endpoint (Production keysets)

eBay refuses to enable a Production keyset until you register a validated
account-deletion notification endpoint. The app implements it at
`/api/ebay/account-deletion` (GET answers eBay's challenge, POST acks and
records the notification under `data/exports/`). To wire it up:

1. Invent a verification token, 32–80 chars of letters/digits/`_`/`-`
   (e.g. `openssl rand -hex 32`), and set it on the app:
   `fly secrets set EBAY_VERIFICATION_TOKEN=<token>`
2. On <https://developer.ebay.com/> → **Application Keys** → your Production
   keyset → **Alerts & Notifications**, choose *Marketplace Account Deletion*
   and enter the endpoint URL `https://<your-app>.fly.dev/api/ebay/account-deletion`
   plus the **same** token.
3. Hit **Save** — eBay immediately sends a `challenge_code` GET; the app
   answers with the expected SHA-256 hash and the portal shows the endpoint
   as verified.

The challenge hash covers the endpoint URL *exactly as registered*; the app
derives it from the request (correct on Fly), or set `EBAY_DELETION_ENDPOINT`
explicitly if a proxy rewrites your scheme/host.

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
| `GET`  | `/api/listings` | Current user's saved listing history |
| `GET`  | `/api/listings/{id}` | Fetch one saved listing (ownership-checked) |
| `POST` | `/api/auth/signup` · `/login` · `/logout` | Email/password auth (JWT cookie) |
| `GET`  | `/api/auth/me` | Current logged-in user (or null) |

## Database (Neon / Postgres)

Set `DATABASE_URL` (e.g. a Neon connection string) to persist every listing
draft durably and power the **My listings** view. It's optional and resilient:
if unset or unreachable, the app falls back to the local filesystem and never
errors on a DB problem. Tables are auto-created on first use.

## Roadmap (toward a real web + mobile app)

1. **Persistence** ✅ — Neon-backed listing history + My listings.
2. **Reliability & UX** ✅ — HEIC uploads, clear errors, nav.
3. **Accounts & auth** ✅ — email/password login; listings scoped per user.
4. **Brand & UX design** ✅ — QuickFlip identity: eBay palette, retro-modern,
   90s Jordan/Nike energy, cursive wordmark. (Ongoing design pass each phase.)
5. **eBay OAuth** ✅ — "Sign in with eBay" (Authorization Code flow) with
   per-user tokens + auto-fetched business policies/location. Set
   `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET`/`EBAY_RUNAME` and users click
   "Connect eBay"; publishing then uses their token, no manual secrets.
6. **Object storage for images** ✅ — optimized photos upload to Cloudflare R2
   (S3-compatible) and are served via the bucket's public URL, so they survive
   restarts and are reliably fetched by eBay. Set the `R2_*` env vars; falls
   back to local disk when unset.
7. **Mobile** — the app is API-first; a React Native / Expo client (or a PWA)
   reuses every `/api/*` endpoint.
8. **Item Identifier (mobile-only)** — double-layer identification: Claude's
   vision lens + Google Lens, cross-checked for higher-confidence item IDs.

## Project layout

```
backend/
  main.py            FastAPI app + routes
  config.py          env / settings
  models.py          Pydantic models (Listing, etc.)
  storage.py         per-session filesystem store
  services/
    images.py        photo pipeline (Pixian/Photoroom/Adobe/local + Pillow finishing)
    adobe.py         Lightroom studio preset + Photoshop Remove Background APIs
    ebay_trading.py  Trading API (XML) client — sees listings we didn't create
    listing_sync.py  bi-directional sync: import the store, push edits back
    claude_ai.py     vision identify + prompt refine
    taxonomy.py      Taxonomy API -> numeric category IDs
    ebay.py          Inventory API payloads + publish/dry-run
frontend/            React + Vite + Tailwind app (built to frontend/dist)
  src/
    styles/tokens.css  design tokens (colors, radii, shadows, dark mode)
    components/        reusable UI library (buttons, cards, dialogs, badges…)
    views/             dashboard, listing workflow, shop mode, settings
    store.jsx          app state (auth, eBay connection, listings)
```

Frontend dev with hot reload: `cd frontend && npm run dev` (proxies `/api` and
`/media` to the backend on :8000). `./run.sh` and the Dockerfile build the
production bundle automatically.

## Photo pipeline (engine picked by `BG_ENGINE`)

Background removal + studio treatment run on the engine `BG_ENGINE` selects.
Unset (auto) means: **Pixian when its keys are present, otherwise the local
model** — the paid Photoroom/Adobe engines never run in auto mode, so a
configured key can't quietly spend money per photo.

- **`local` (the free default)** — the in-process Pillow + rembg pipeline; no
  key, no per-image cost. Production runs the `isnet-general-use` model
  (`REMBG_MODEL` in fly.toml — needs the 4GB VM; smaller boxes fall back to
  the light `u2netp`).
- **`pixian` (the budget API)** — [Pixian.ai](https://pixian.ai) with
  `PIXIAN_API_ID`/`PIXIAN_API_SECRET` set: roughly a tenth of Photoroom's
  per-image price, with the local model as its in-chain fallback. Batches run
  several photos at a time (`PHOTO_BATCH_WORKERS`, default 8).
- **`photoroom`** — the Photoroom API (`PHOTOROOM_API_KEY`); best quality,
  priciest. When Adobe is also configured it backs Photoroom up.
- **`adobe`** — `ADOBE_CLIENT_ID`/`ADOBE_CLIENT_SECRET` (a server-to-server
  OAuth credential from
  [developer.adobe.com/console](https://developer.adobe.com/console) with the
  Lightroom + Photoshop APIs enabled): the **Lightroom API** applies the
  "studio" develop preset (bundled at `backend/assets/studio-preset.xmp`;
  `ADOBE_STUDIO_PRESET_URL` overrides it), then the **Photoshop Remove
  Background** service does the cutout. R2 is required for this path: Adobe's
  async APIs move files exclusively via presigned URLs.

Either way the finished images continue through the usual flow: square crop,
resize to 1600px, identify, draft, publish. A failed pro-engine call never
loses a photo: the original is kept and the reason is surfaced in the API
response (`optimize_results`) and the photo studio.

## Bi-directional eBay sync

The Sell Inventory API that publishes listings can only see listings created
*through* it, so a seller's existing store was invisible to the app. **Sync with
eBay** (on the Listings page, once eBay is connected) closes that gap using the
Trading API with the same user OAuth token — no extra credentials:

- **eBay → app.** `GetMyeBaySelling` enumerates every active listing; `GetItem`
  pulls the detail (title, price, quantity, condition, category, item specifics,
  photos, package, watch/sold counts). Imported records get the stable id
  `ebay-<itemId>`, so re-syncing updates in place instead of duplicating.
- **app → eBay.** Edits to an imported listing go back through
  `ReviseFixedPriceItem` (or `ReviseItem` for auctions), and ending one uses
  `EndItem`. Listings the app created keep using the Inventory API.

A re-sync only refreshes the fields eBay owns — price, quantity, counters,
photos — plus anything still blank locally, so a background sync never reverts
an in-app edit. Sold and ended listings are reconciled on the same pass. One run
imports up to `IMPORT_LIMIT` (300) listings; a larger store fills in across
repeated syncs. Detail fetches run a few at a time (`EBAY_SYNC_WORKERS`,
default 6) so a big store doesn't outlive the request.

## Notes & limitations

- The AI never invents serial numbers, authenticity guarantees, or unverifiable
  specs — it flags those under "missing info" for you to confirm.
- Image optimization is intentionally non-destructive of subject framing; the
  border auto-crop only triggers on clearly plain backgrounds.
- Category ID is auto-resolved via the eBay Taxonomy API when
  `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` are set; otherwise enter it manually in
  the preview. Taxonomy data in the eBay **sandbox** is limited, so category
  suggestions are most accurate against production.
