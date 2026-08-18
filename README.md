# QuickFlip

**Snap it · AI writes it · list it everywhere.**

Turn product photos into a complete, ready-to-publish listing — on eBay,
Etsy, and Depop, individually or all at once.

Upload one or more images → the app **optimizes** them for eBay, uses Claude's
vision **"lens"** to identify the item, **generates** a full listing (title,
description, item specifics, suggested price/category), shows an **editable
preview** where you can tweak fields manually or with a prompt, then **pushes**
the result to eBay as a draft or live listing (or generates the exact API
payload when you don't have eBay credentials yet).

## Pipeline

```
 Upload images ──▶ Optimize (Pillow) ──▶ Identify (Claude vision) ──▶
 Editable preview (manual edits + prompt refine) ──▶ Publish (eBay / Etsy / Depop, or dry-run)
```

| Stage | What happens | Tech |
|-------|--------------|------|
| Optimize | Auto-orient, trim plain borders, pad to square on a clean canvas, upscale to 1600px, mild enhance | Pillow |
| Identify | Photos sent to Claude vision; returns structured listing draft + confidence + "missing info" to verify | Anthropic API |
| Preview | Edit every field; add/remove item specifics; refine with a natural-language prompt | Web UI |
| Category | Resolves a numeric eBay leaf categoryId from the item via the Taxonomy API (auto during identify + a "Suggest categories" picker in the preview) | eBay Taxonomy API |
| Publish | Fans out to every selected marketplace — eBay (Trading/Inventory), Etsy (draft → activate), Depop — each succeeding or failing independently; dry-run payloads when not connected | eBay / Etsy / Depop APIs |

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
# The /data volume in fly.toml's [mounts] must exist before the first deploy:
fly volumes create data --size 3 --region sjc
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
# Optional server-level eBay creds (see below). Most sellers instead connect
# their own account through the in-app OAuth flow, which needs none of these:
fly secrets set EBAY_OAUTH_TOKEN=... \
  EBAY_FULFILLMENT_POLICY_ID=... EBAY_PAYMENT_POLICY_ID=... \
  EBAY_RETURN_POLICY_ID=... EBAY_MERCHANT_LOCATION_KEY=...
fly deploy
```

> **`fly.toml` sets `EBAY_ENV=production`** — a deploy publishes REAL, fee-
> incurring, publicly visible eBay listings. For a sandbox deploy override it
> with `fly secrets set EBAY_ENV=sandbox` (a secret wins over `[env]`).

The app listens on `$PORT` (8080) and runs uvicorn with `--proxy-headers` so it
sees Fly's HTTPS origin. The `[mounts]` block is active and required, not
optional: photos are served from `/data` and eBay fetches those URLs at publish
time, so on the container's ephemeral disk a restart turns every in-flight
listing's images into eBay's opaque 25001 error. Fly health-checks
`/api/health` every 15s.

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
| `POST` | `/api/publish` | Publish (draft/live). Add `marketplaces: ["ebay","etsy","depop"]` to fan out; omit for the legacy eBay-only behavior |
| `GET`  | `/api/marketplaces` | Every marketplace + connection state (drives Settings & publish chips) |
| `GET`  | `/api/{marketplace}/connect` · `/callback` | OAuth connect flow (eBay, Etsy, Depop) |
| `POST` | `/api/{marketplace}/end-listing` | End one marketplace's live listing |
| `GET/POST` | `/api/etsy/settings-options` | Etsy shipping-profile / return-policy defaults |
| `GET`  | `/api/listings` | Current user's saved listing history |
| `GET`  | `/api/listings/{id}` | Fetch one saved listing (ownership-checked) |
| `POST` | `/api/auth/signup` · `/login` · `/logout` | Email/password auth (JWT cookie) |
| `GET`  | `/api/auth/me` | Current logged-in user (or null) |
| `GET`  | `/api/tokens` | AI-token balance, feature costs, packs, next free reset |
| `POST` | `/api/tokens/checkout` | Start a Stripe Checkout for a token pack |
| `GET`  | `/api/tokens/confirm` | Post-redirect purchase credit (idempotent) |
| `POST` | `/api/tokens/webhook` | Stripe `checkout.session.completed` webhook |
| `GET`  | `/api/tokens/history` | Recent token ledger entries |
| `GET`  | `/api/forum/meta` | Board sections, composer limits, and totals |
| `GET`  | `/api/forum/posts` | Board page (`category`, `q`, `sort`, `mine`, `limit`, `offset`) |
| `POST` | `/api/forum/posts` | Start a thread (optionally attaching one of your listings) |
| `GET`  | `/api/forum/posts/{id}` | One thread with its replies |
| `PATCH`/`DELETE` | `/api/forum/posts/{id}` | Edit / delete your own thread |
| `POST` | `/api/forum/posts/{id}/replies` | Reply to a thread |
| `PATCH`/`DELETE` | `/api/forum/replies/{id}` | Edit / delete your own reply |
| `POST` | `/api/forum/{posts\|replies}/{id}/vote` | Cast or withdraw one upvote (idempotent) |

## Monetization: AI tokens

The app is **free**; AI features spend **tokens**. Every account gets
`FREE_TOKENS_PER_MONTH` (default **50**) each calendar month — the allowance
resets on the 1st (UTC) and does **not** roll over. When it runs out, users
buy a token pack via Stripe Checkout; **purchased tokens never expire** and
are spent only after the free ones. Failed AI calls are refunded
automatically ("only pay for AI that worked"). Billing is entirely opt-in:
with `TOKENS_ENABLED` unset (or no `DATABASE_URL`), every AI feature stays
free — the right default for local dev and self-hosters.

**What features cost** (defaults; override per deployment with
`TOKENS_COST_*` env vars):

| Feature | Tokens | Notes |
|---------|-------:|-------|
| AI listing draft | 5 | identify + category + item specifics + tag read + maker check; same per item in a bulk batch (photo grouping bundled) |
| AI refine instruction | 1 | free-form "make it..." edits |
| Autofill item specifics | 2 | the standalone button (bundled free inside a draft) |
| Shop Mode shelf scan | 2 | one video's frames |
| AI photo tool | 1 / photo | background removal, auto-clean, smart crop |

**Packs** (edit `PACKS` in `backend/services/tokens.py`): Starter 50/$5.99 ·
Plus 120/$11.99 · Pro 300/$24.99 · Power 1000/$69.99 — $0.12 down to
$0.07/token as packs grow.

**Why these numbers are profitable.** A full draft runs 4–6 vision calls over
up to 8 photos; on the default Opus-tier vision model ($5/M input, $25/M
output) that's ~$0.25 of API spend for a typical 3-photo listing and ~$0.80
worst-case at 8 photos. At 5 tokens, a draft brings in $0.35–$0.60 → roughly
40–60% gross margin in the typical case, still positive at worst case on the
mid packs. The lighter features cost cents against 1–2 tokens. The free
allowance caps the operator's giveaway at ~$2.50/user/month. Pointing
`VISION_MODEL` at a cheaper tier widens every margin; re-tune the
`TOKENS_COST_*` numbers if you do.

**Mechanics.** Balances live in Postgres (`token_accounts` +
an append-only `token_ledger` audit trail). Spends are atomic (row-locked)
and split free-first; the monthly reset is lazy (no cron). Purchases credit
idempotently by Stripe session id, so the webhook and the post-redirect
confirm can race safely — configure the webhook
(`checkout.session.completed` → `/api/tokens/webhook`) so credits land even
when the buyer closes the tab. When billing is on, AI endpoints require a
login (balances are per-account) and return **402** with an "out of tokens"
message the UI turns into the buy dialog; a bulk batch that runs dry saves
the remaining items as photo-only stub drafts so nothing is lost.

## Marketplaces (eBay · Etsy · Depop)

Every marketplace is a provider behind one interface (`backend/marketplaces/`):
OAuth connect, per-user credentials, preflight, publish, and end. `POST
/api/publish` without a `marketplaces` field keeps the original eBay-only
behavior byte-for-byte; with one, each marketplace publishes independently —
one failing never rolls back the others — and per-marketplace state
(listing id, URL, status, last error) lives on the listing record.

- **eBay** — unchanged: Trading API for new live listings, Inventory for
  drafts/dry-runs, imported-listing revise/relist, Promoted Listings.
- **Etsy** — Etsy Open API v3 (OAuth + PKCE; set `ETSY_CLIENT_ID` +
  `ETSY_REDIRECT_URI`). Listings are created as Etsy drafts, photos uploaded,
  then activated on a live publish. Etsy requires a category (AI Suggest
  built in), who-made/when-made attribution, and a shipping profile
  (defaults per account under Settings). Note: Etsy allows only handmade,
  vintage (20+ years), and craft supplies, and rotates refresh tokens —
  both are handled.
- **Depop** — official Selling API, which is **partner-gated**: apply via
  Depop partnerships, then set the five `DEPOP_*` vars from onboarding
  (`.env.example`). Until then Depop simply stays hidden. No drafts or
  auctions on Depop; titles are word-boundary-truncated to its limit and
  conditions translated.

Adding marketplace N+1 = one provider module + one import in
`backend/marketplaces/__init__.py`.

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
   (S3-compatible) so they survive restarts and are reliably fetched by eBay.
   Only `R2_ACCOUNT_ID` + `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` are
   needed — the bucket is auto-created (override with `R2_BUCKET`) and photos
   are served via presigned URLs, or straight from the bucket if you set
   `R2_PUBLIC_BASE_URL`. Falls back to local disk when unset; `/api/health`
   shows `objstore_missing` when partially configured.
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
    forum.py         community board: sections + what a post may say
frontend/            React + Vite + Tailwind app (built to frontend/dist)
  src/
    styles/tokens.css  design tokens (colors, radii, shadows, dark mode)
    components/        reusable UI library (buttons, cards, dialogs, badges…)
    views/             dashboard, listing workflow, shop mode, community, settings
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

**Interior-hole repair.** Every engine's matte goes through the same fix-up
before it's composited: matting models regularly call a printed graphic, a
bright panel or a glossy face *inside* the item "background", which is what
punches white holes through the middle of a product. A removed region that is
completely sealed in by subject is either a genuine see-through gap (a mug
handle) — which shows the very backdrop we just removed — or item the model
ate, which doesn't. So the backdrop is flooded inward from the frame border and
every stranded region that doesn't match it is put back, with the pixels taken
from the original photo. The same repaired mask drives the photo studio's
auto-clean, residue highlight, and smart crop. Tunable with
`BG_HOLE_TOLERANCE` (default 45) and `BG_FILL_HOLES=off`.

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

## Community forum

A board where sellers answer the questions the AI can't: what's this worth,
does this brand move, who ships a 40lb mirror. Six sections (General, Price
checks, Sourcing, Shipping, Platforms, Wins), threads with replies, and one
upvote per person per item.

- **Reading is open to everyone**; posting, replying, and voting need an
  account. A community nobody can read until they sign up never gets its
  first post.
- **Price checks can carry a listing.** Attaching one of your own listings
  posts a *snapshot* — title, price, one photo, as they are right now — so
  the thread still reads correctly after the item is edited or sold. The
  server refuses any listing you don't own.
- **Votes are idempotent**: the client sends the state it wants, not a
  toggle, and a unique constraint on `(user_id, target_type, target_id)`
  makes a double tap or a retried request settle rather than double-count.
- **Deleting your account erases your forum footprint** — threads, replies,
  votes, and the counts your votes propped up (see `db.delete_user`).
- **Needs `DATABASE_URL`.** Threads are rows or they are nothing, so with no
  database configured the API reports `available: false` and the UI says so
  plainly instead of showing an empty board.

Posting is free (unlike the AI paths, which the token gate meters), so writes
are rate-limited per client — see `FORUM_WRITE_LIMIT` in `backend/main.py`.

## Sold notifications & shipping labels

When a sync notices a listing flipped to **sold**, the seller gets an in-app
notification (the bell in the top bar, polled every minute) with a one-tap
jump into the shipping dialog. Notifications are deduplicated in the database
(one per sale, no matter how many sync paths spot it), and backfilling an
existing store's historical sales stays silent.

The shipping dialog (also reachable via **Ship orders** on the Listings page)
reads the orders still awaiting shipment through the Fulfillment API — buyer
address included — pre-fills the package weight/dims from the matching
listing, and offers two label workflows:

- **eBay labels** (Logistics API): live eBay-negotiated rates → buy → print,
  with tracking uploaded to the order automatically. The Logistics API is
  **limited-release** — eBay must enable it for your keyset; set
  `EBAY_LOGISTICS_ENABLED=1` once approved and its scope joins the connect
  flow. Until then the option explains itself and defers to Pirate Ship.
- **Pirate Ship**: no public API exists, so the app exports the order(s) as a
  CSV shaped for Pirate Ship's spreadsheet importer (recipient address +
  per-row weight/dims), links the seller there, and takes the tracking number
  back — marking the order shipped on eBay via `createShippingFulfillment`,
  which emails the buyer.

Reading orders and posting tracking needs the `sell.fulfillment` OAuth scope;
sellers who connected eBay before it was added reconnect once to grant it
(same as every scope addition).

## Notes & limitations

- The AI never invents serial numbers, authenticity guarantees, or unverifiable
  specs — it flags those under "missing info" for you to confirm.
- Image optimization is intentionally non-destructive of subject framing; the
  border auto-crop only triggers on clearly plain backgrounds.
- Category ID is auto-resolved via the eBay Taxonomy API when
  `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` are set; otherwise enter it manually in
  the preview. Taxonomy data in the eBay **sandbox** is limited, so category
  suggestions are most accurate against production.
