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
| Optimize | Auto-orient, cut the background onto a white canvas with a soft contact shadow (or trim plain borders when removal is off), square-frame without cropping the item, resize to 1600px, finishing sharpen | Pillow |
| Identify | Photos sent to Claude vision; returns structured listing draft + confidence + "missing info" to verify | Anthropic API |
| Preview | Edit every field; add/remove item specifics; refine with a natural-language prompt | Web UI |
| Category | Resolves a numeric eBay leaf categoryId from the item via the Taxonomy API (auto during identify + a "Suggest categories" picker in the preview) | eBay Taxonomy API |
| Publish | Fans out to every selected marketplace — eBay (Trading API), Etsy (draft → activate), Depop — each succeeding or failing independently; dry-run payloads when not connected | eBay / Etsy / Depop APIs |

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

## Shipping an update

**Merging to `main` is the deploy.** There is no other step, and no button to
press:

```
work on a branch  →  push  →  PR  →  CI goes green  →  merge to main
                                                          ↓
                             GitHub Actions builds, ships to Fly, then polls
                             /api/health until production reports that exact
                             commit — and fails the run if it never does.
```

> ### Do not run `fly deploy` by hand
>
> `fly deploy` uploads **the files on your machine**. It does not read GitHub.
> Run it from a checkout that is behind `main` and it silently replaces the
> released code with whatever you happen to have — skipping the tests, the
> commit stamp, and the verification, with nothing anywhere reporting it.
>
> On 2026-08-27 that put a build from before Aug 24 back into production
> minutes before a seller hit publish. The eBay failure it caused was
> indistinguishable from the bug that had just been fixed, and the deploy that
> had shipped the fix twelve hours earlier had verified itself and passed.
>
> When you genuinely cannot use CI, use **`./deploy.sh`** instead. It refuses
> unless you are on `main`, clean, and exactly level with `origin/main`; it
> passes the `GIT_SHA` build arg that makes the release identifiable; and it
> runs the same verification afterwards. `./deploy.sh --check` reports what it
> would object to and changes nothing.
>
> A hand deploy that skips the stamp is caught either way: `health-watch.yml`
> compares production's build against recent `main` every two hours.

## First-time Fly setup

A `Dockerfile` and `fly.toml` are included. eBay requires **publicly reachable
HTTPS image URLs**, so deploying (vs. running on localhost) is what makes real
publishing work — the app uses its public origin for `imageUrls`.

The steps below create the app. Everything after that goes through `main`.

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
./deploy.sh                   # first release; after this, merge to main
```

> **`fly.toml` sets `EBAY_ENV=production`** — a deploy publishes REAL, fee-
> incurring, publicly visible eBay listings. For a sandbox deploy override it
> with `fly secrets set EBAY_ENV=sandbox` (a secret wins over `[env]`).

### Check what production is actually configured with

`fly volumes create --size 3` above is the instruction, not a guarantee that it
was followed — the volume on `listing-lfwjrg` is **1GB**, and a volume is
easy to create small and never revisit. The reclaim daemon keeps usage down
(`main._reclaim_loop`), so this is headroom rather than a leak, but the
alerting floor in `health-watch.yml` is 400MB free and the volume runs near
500MB. `fly volumes list -a <app>` reports the size; `fly volumes extend
<id> --size 3` raises it without a redeploy.

Everything else worth checking, production answers itself:

```bash
curl -s https://<app>.fly.dev/api/health | python3 -m json.tool
```

- **`config_warnings`** is the field to read first. It names the two
  misconfigurations that otherwise look *identical* to never having configured
  a feature at all: a secret set under a name one word off from the one the
  code reads, and an on/off flag set to a value that isn't on. It also flags a
  Stripe key that is present but isn't a *secret* key — a publishable `pk_...`
  in that slot passes every readiness check in the app and then fails at
  checkout.

  How it found the Stripe one is why the field exists. Production had
  `STRIPE_API_SECRET_KEY` deployed while the code read `STRIPE_SECRET_KEY`, so
  the paid tier was off with a key plainly visible on the Fly dashboard and
  `tokens_missing` reporting — accurately, uselessly — that the key was
  missing. **The fix went into the code, not the secret**:
  `STRIPE_API_SECRET_KEY` is now accepted as a second name for the same
  setting, exactly as `DATABASE_URL` accepts `NEON_PRODUCTION_DATABASE_URL`.
  Renaming a live secret restarts the machine, and with
  `min_machines_running = 1` that means restarting it under whatever batch is
  in flight — not a trade worth making to satisfy a spelling.
- **`bg_engines`** is the list that will actually run, in order. `["local"]`
  next to `"photoroom_configured": true` is not a contradiction — auto mode
  never spends money on its own (see the photo-pipeline section) — but it does
  mean every cutout is costing the ~107s an `isnet` inference takes on
  `shared-cpu-2x`, with a paid engine sitting configured and unused.
  [Pixian.ai](https://pixian.ai) is a pay-per-image background-removal API at
  roughly a tenth of Photoroom's price; setting `PIXIAN_API_ID` +
  `PIXIAN_API_SECRET` moves auto mode onto it with **no code change** and
  keeps the local model as its in-chain fallback. `BG_ENGINE=photoroom` opts
  into the key already there instead. Either turns ~107s per photo into a
  couple of seconds; both cost money per image, which is exactly why neither
  switches itself on.
- **`disk_free_mb`**, **`db`**, **`objstore_*`** and **`build`** cover the rest;
  `health-watch.yml` already alerts on them every two hours.

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

### Deploy credentials — and the one that must NOT be on the app

CI deploys with `FLY_API_TOKEN`, read from the **GitHub Actions secret** by
`.github/workflows/deploy.yml` and `fly-logs.yml` (`${{ secrets.FLY_API_TOKEN }}`).
That is the only place it belongs.

> **Never `fly secrets set FLY_API_TOKEN` on the app.** Nothing in this
> codebase reads it — `grep -rn FLY_ backend/` returns nothing — so it buys the
> running container no capability at all, while handing anything that can read
> the process environment full control of the Fly account: every other secret
> here (`NEON_PRODUCTION_DATABASE_URL`, `ANTHROPIC_API_KEY`, `R2_*`,
> `SENDGRID_API_KEY`), plus the ability to destroy or redeploy any app on it.
> The container also has Fly's own API proxy mounted at `/.fly/api`. If it is
> ever set, take it back off — nothing depends on it:
>
> ```bash
> fly secrets list -a <app>                       # is FLY_API_TOKEN there?
> fly secrets unset FLY_API_TOKEN -a <app> --stage # --stage: no restart now
> ```
>
> `--stage` matters on this app: it runs a single machine with
> `min_machines_running = 1`, so an unstaged `secrets set`/`unset` restarts
> production immediately and kills any in-flight photo batch. Staged changes
> apply on the next deploy instead.

Prefer an app-scoped deploy token over a personal one, so a leak cannot reach
anything else in the account:

```bash
fly tokens create deploy -a <app>   # only deploys this app; cannot read others
```

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
`AddFixedPriceItem` (or `AddItem`) request the real publish would send and
saves it to `data/exports/` so you can inspect it or push later.

**To publish for real, a seller connects their own eBay account** through
Settings → Connect eBay (OAuth). That is the only way a live listing is
created: every publish goes out on the connected seller's account, through the
Trading API. Server-side credentials do NOT publish on their own — they used
to, through the Sell Inventory API, and that engine is gone.

What the server-side settings are still for — the OAuth app itself, so the
Connect button works:

- `EBAY_ENV` — `sandbox` (recommended first) or `production`. **Anything other
  than exactly `production` is treated as sandbox**, where a real eBay sign-in
  cannot work; the app warns at boot if the value is neither.
- `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` / `EBAY_RUNAME` — from your eBay
  developer keyset. `EBAY_RUNAME` is the **RuName** (`Your_Name-Yourname-...`),
  not a URL; a URL there fails the token exchange with `invalid_grant`.

`EBAY_OAUTH_TOKEN`, `EBAY_REFRESH_TOKEN` and the `EBAY_*_POLICY_ID` /
`EBAY_MERCHANT_LOCATION_KEY` values remain read for the dry-run payload and
for local testing; they no longer make anything go live.

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
| `POST` | `/api/upload` | Upload images (multipart) → optimize → `session_id`. Add `pipeline=true` to return as soon as the files are saved and run optimize **and** identify as one background job → `job_id` |
| `POST` | `/api/identify/{session_id}` | Claude vision → listing draft (synchronous; used by Shop Mode) |
| `POST` | `/api/identify-async/{session_id}` | The same draft as a polled job → `job_id` |
| `POST` | `/api/bulk/upload` | One photo pile → many drafts, as a job → `job_id` |
| `GET`  | `/api/bulk/status/{job_id}` | Poll any of the jobs above (phase, per-photo progress, result) |
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
| `POST` | `/api/listings/{id}/relist` | Copy a settled listing into a **new draft** — sale-specific fields cleared, photos copied, the original left untouched |
| `POST` | `/api/listings/merge/preview` | Duplicate drafts merged under a chosen master, worked out but not written: the fields the drafts disagree about, and the blanks a duplicate fills in |
| `POST` | `/api/listings/merge` | Consolidate duplicate drafts into the master — photos combined, `field_choices` applied, sources deleted |
| `GET`  | `/api/insights` | Ranked "what to do next" actions across the user's listings |
| `GET`  | `/api/ebay/duplicates` | Live listings that look like the same item listed more than once |
| `POST` | `/api/ebay/promote-all` | Promote every live, unpromoted listing (a suggestion group's bulk action) |
| `POST` | `/api/ebay/lower-prices` | Lower the named listings' prices by one percentage and push each to eBay |
| `POST` | `/api/auth/signup` · `/login` · `/logout` | Email/password auth (JWT cookie) |
| `GET`  | `/api/auth/me` | Current logged-in user (or null) |
| `GET`  | `/api/tokens` | AI-token balance, feature costs, packs, next free reset |
| `POST` | `/api/tokens/checkout` | Start a Stripe Checkout for a token pack |
| `GET`  | `/api/tokens/confirm` | Post-redirect purchase credit (idempotent) |
| `POST` | `/api/tokens/webhook` | Stripe `checkout.session.completed` webhook |
| `GET`  | `/api/tokens/history` | Recent token ledger entries |

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

**Why these numbers are profitable.** A full draft runs 2–3 vision calls over
up to 8 photos (the consolidated `IDENTIFY_CHAIN=v2` chain — it was 4–6 before
the tag-read, specifics and maker passes were folded together, and the photos
now ride as ~1092px copies at roughly half the image tokens); on the default
Opus-tier vision model ($5/M input, $25/M output) that's ~$0.10 of API spend
for a typical 3-photo listing and ~$0.30 worst-case at 8 photos. At 5 tokens,
a draft brings in $0.35–$0.60 → a wider margin than the 40–60% these prices
were originally set for. The lighter features cost cents against 1–2 tokens. The free
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

- **eBay** — Trading API for everything that touches a listing: new live
  listings, revise, relist and end. Drafts stay in the app and never reach
  eBay; a dry run renders the Trading request instead of sending it. The REST
  APIs are still used for the things that are not listings — Account
  (business policies, programs, privileges), Taxonomy, Fulfillment (orders),
  and Marketing (Promoted Listings).
- **Etsy** — Etsy Open API v3 (OAuth + PKCE; set `ETSY_CLIENT_ID` +
  `ETSY_REDIRECT_URI`). Listings are created as Etsy drafts, photos uploaded,
  then activated on a live publish. Etsy requires a category (AI Suggest
  built in), who-made/when-made attribution, and a shipping profile
  (defaults per account under Settings). Note: Etsy allows only handmade,
  vintage (20+ years), and craft supplies, and rotates refresh tokens —
  both are handled. First connect stopping on Etsy's own page with *"Only
  the app owner may authorize a seller app"* is app **type**, not config: a
  Seller app is authorizable by the one Etsy account that registered the
  keystring and nobody else, so either sign in as that account, or request
  Commercial Access to let other sellers connect. Reaching that page proves
  `ETSY_CLIENT_ID` and `ETSY_REDIRECT_URI` are registered correctly — a
  wrong one fails before the consent screen. Details in `.env.example`.
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
    ebay.py          photo URLs for eBay + the legacy ad SKU
    ebay_trading.py  Trading API: publish, revise, end, read
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

**One inference at a time, but two different deadlines.** The local model is
serialized (two runs at once double peak memory and OOM the box), so callers
queue for a slot — and how long they should queue depends entirely on who is
waiting. Someone watching the photo studio's spinner wants a fast "busy, try
again" (`REMBG_WAIT_SECONDS`, default 25). A photo in a background batch has
nobody to tell and gets no retry, so giving up just saves it with its
background still on — it queues instead (`REMBG_BATCH_WAIT_SECONDS`, default
300). A single deadline served both at 25s, which is shorter than one
inference on a loaded box, so every photo queued behind another was guaranteed
to time out and keep its background.

**`REMBG_MAX_SIDE` is not a speed dial.** It caps the copy handed to the
model, and both bundled models normalize their input to a fixed tensor first
(isnet 1024x1024, u2netp 320x320) — so a smaller copy is upscaled straight
back before any convolution runs. Measured on two pinned cores, 640 and 1024
finish within 2% of each other; going below the model's own input size costs
quality and buys nothing. Production matches isnet at 1024. What the setting
genuinely controls is memory and the resolution the refinement passes run at.

What *does* cost time is contention. On the same two pinned cores one
inference takes 0.32s with nothing else running, 0.72s against one competing
worker and 0.83s against two — so `PHOTO_LOCAL_WORKERS` is sized to cores, not
to RAM, and `REMBG_THREADS` caps onnxruntime at one fewer than the visible
CPUs (uvicorn still has to answer its health check while a batch runs, and a
machine that misses those gets replaced by the platform, killing the batch).

Those are worth perhaps 1.5x. The 41s and 105s inferences in the incident that
prompted this were an order of magnitude beyond what contention explains, and
the remaining factor is the `shared-cpu` allocation itself: sustained batches
exhaust the burst allowance and get throttled to baseline. If batches need to
be genuinely fast rather than merely reliable, the lever is `performance-2x`
(dedicated cores) or a remote engine — not these settings.

**A batch survives the machine it started on.** Bulk does every photo's
background removal up front and only then starts drafting, so until the last
cutout lands nothing durable exists. A machine that goes away during that pass
(a deploy, an OOM, a platform replacement) used to take the whole batch with
it, even though the finished photos were on the volume the entire time.
Optimized outputs are now renamed into place — so a file existing means it is
complete — and the photo pass skips any it already has. On boot, a batch that
was interrupted *before it drafted anything* is re-registered under its own
job id and run again, so a browser still polling simply carries on. Batches
interrupted during `identifying` are left alone: each finished item is already
saved and already billed per item, so resuming would duplicate both.
`BULK_MAX_RESUMES` (default 2) stops a batch that keeps dying from taking the
machine with it.

## Bi-directional eBay sync

**Sync with eBay** (on the Listings page, once eBay is connected) mirrors the
seller's existing store into the app, using the Trading API with the same user
OAuth token — no extra credentials:

- **eBay → app.** `GetMyeBaySelling` enumerates every active listing; `GetItem`
  pulls the detail (title, price, quantity, condition, category, item specifics,
  photos, package, watch/sold counts). Imported records get the stable id
  `ebay-<itemId>`, so re-syncing updates in place instead of duplicating.
- **app → eBay.** Edits go back through `ReviseFixedPriceItem` (or
  `ReviseItem` for auctions), and ending one uses `EndItem` — for every
  listing, whether the app created it or imported it.

Publishing goes through the Trading API for a reason: a listing created with
the Sell **Inventory** API becomes "inventory-based", and eBay then refuses to
let the seller edit it anywhere but the tool that made it — Seller Hub answers
"Inventory-based listing management is not currently supported by this tool."
A Trading listing is an ordinary one the seller can edit in Seller Hub, the
eBay app, or here.

Saving a draft while connected used to leave an inventory item and an
unpublished offer behind on the account. It no longer does. To clear what
earlier drafts left (invisible in Seller Hub, and nothing else can remove it):

```bash
python3 scripts/purge_inventory_leftovers.py --user <user-id>          # dry run
python3 scripts/purge_inventory_leftovers.py --user <user-id> --apply
```

It only ever considers SKUs this app minted, and never deletes an item with a
published offer — that would end a live listing.

A re-sync only refreshes the fields eBay owns — price, quantity, counters,
photos — plus anything still blank locally, so a background sync never reverts
an in-app edit. Sold and ended listings are reconciled on the same pass. One run
imports up to `IMPORT_LIMIT` (300) listings; a larger store fills in across
repeated syncs. Detail fetches run a few at a time (`EBAY_SYNC_WORKERS`,
default 6) so a big store doesn't outlive the request.

The sync matches on the **eBay item id**, not just its own `ebay-<itemId>` rows,
so a listing the app published (which lives under its session id) is updated in
place instead of imported again as a second card. The read that feeds that match
covers the seller's whole store — `EBAY_SYNC_KNOWN_LIMIT`, default 10000 rows,
deliberately far above any real store, because a record the read misses is one
the dedupe can't match.

### One listing, one live listing

Creating a listing is the only step in the publish pipeline that isn't naturally
idempotent: call `AddFixedPriceItem` twice and the seller has two live listings
and an eBay policy problem. A publish takes tens of seconds (eBay ingests every
photo), which is long enough for a seller to reload and press the button again —
and a reload resets the browser's own double-submit guard. Three defences, in
`services/publish_guard.py`:

- **Publishes of one listing are serialized** (a per-listing lock), so two
  overlapping requests can't both decide to create.
- **The item id comes from the stored record, never the submitted payload.** A
  payload assembled before the first publish carries no `ebay_listing_id` and no
  `source`, and believing it reads as "never listed".
- **The create carries an idempotency key** — as `UUID`, and (fixed-price) as
  `InventoryTrackingNumber`, which is separately queryable. eBay refuses a
  second create under the same key even when the two attempts never meet in one
  process, and the app then adopts the listing that already exists rather than
  posting a twin. A relist keys on the item it replaces, so an intentional
  relist still goes through while a retried one doesn't double-list.

### Finding the duplicates already out there

The guard above stops new ones; it can't undo the pairs an earlier race left on
the account, because those are two real eBay listings and only the seller can
say which to end. `services/duplicates.py` finds the likely ones — live
listings sharing a normalized title but holding **different** eBay item ids —
and ranks them by the evidence:

- **Listed seconds apart** is the strongest tell. A seller listing two copies of
  something does it deliberately; a double-publish mints both at once.
- **Same price**, and **one row created here alongside one pulled back from the
  store** — the exact shape the publish race left behind.

It is deliberately called *possible*. A reseller can legitimately have two live
listings with the same title, so the reasons it might be fine (listed months
apart, different prices, one auction and one Buy It Now) are shown with equal
weight, an auction/Buy-It-Now pair is never ranked high, and two rows for the
SAME item id are never flagged — that's a sync artifact, and telling a seller to
end it would cost them their only listing. The Dashboard card hides itself when
there's nothing to report, and nothing is ever ended automatically: each End is
one listing, behind a confirm, through the usual `/api/ebay/end-listing`.

## Suggested actions (and applying them in bulk)

The Dashboard's **Suggested actions** card is `services/recommender.py` over the
signals the app already has — listing status, age, price, photo count, promotion
state, plus eBay views/watchers when the scope is granted. Rules turn a store
into a short ranked list: finish a draft, relist an ended item, promote a live
one, drop a stale price, add photos, fill in specifics. Suggestions are grouped
by kind and collapsed ("Lower prices · 12"), keeping one strongest action per
listing so the list spans the portfolio instead of piling onto one item.

A group whose edit makes sense across every listing in it gets a **bulk action**
in its header, because repeating one edit twelve times by hand is the whole
problem:

- **Lower prices → "Lower all…"** opens an amount field (*lower every price in
  this group by X %*) with its own submit. Each listing is repriced and pushed to
  eBay through the same revise path a single edit uses.
- **Promote listings → "Promote all"** promotes every live, unpromoted listing at
  eBay's recommended ad rate.

Photos, specifics, finish and relist deliberately have none: the first two need a
human looking at each item, and the last two create listings, which isn't
something to put behind a single button. The rules bulk runs follow —
`services/bulk_actions.py`:

- **Scope is never implicit.** The client sends the group's own listing ids, so a
  group of twelve can't turn into the whole store.
- **A listing that can't take the change is skipped with a reason**, not failed —
  a group computed a while ago will contain items that have since sold or ended.
- **One listing's failure never stops the run**, and the response reports per
  listing, so the seller sees "lowered 11 · 1 skipped" rather than a bare OK.
- **The run is bounded** (`BULK_PRICE_CAP`, default 40) because each listing is
  its own serial eBay revise; the remainder comes back as `deferred` for another
  pass instead of the request outliving the gateway.

## What a sale actually made

`price` on a listing is the **asking** price and keeps that meaning after the
sale. When an item goes for less — an accepted Best Offer, an auction close, a
markdown — eBay reports the real amount only on the *transaction*, and never
moves the listing's own price to match. A sold record built from `GetItem`
alone therefore showed what the seller hoped for, not what the buyer paid.

So the sync reads `GetMyeBaySelling`'s **SoldList transactions** (the same
paged call that already named the sold items, so no extra eBay quota) and
stamps two fields on the record:

- `sold_price` — the per-unit amount the buyer actually paid.
- `sold_at` — eBay's transaction date. A record's `updated_at` can't stand in
  for it: an imported listing carries its eBay *start* time.

Both survive re-syncs, and the sale price is editable under **Sale figures**
in the sold listing's archive view for a sale eBay never reported (one older
than its ~90-day window). Where `sold_price` is unknown, the UI falls back to
the asking price and marks the number approximate (`≈ $30.00`) rather than
claiming it as the take.

Everything downstream reads that: the sold card shows what it went for with
the asking price struck through and how far under it landed, the Inactive
tab's profit line measures against the real amount, and the dashboard's
**Sold** tile totals it over a window the seller picks (24 hours / 7 days /
30 days / 90 days, defaulting to a week and remembered across visits).

## A sold listing is an archive, not a draft

Selling ends the listing. The record left behind is the app's only memory of
what that sale was, so it stops behaving like something still on its way to
eBay:

- It files under **Inactive** with the ended-without-selling ones — one
  archive of everything finished — and is hidden from **Active** and **All**,
  where a seller looks for things they can still act on.
- Opening it gives the archive view (`views/listing/SoldArchive.jsx`), not the
  publish workflow: what it went for, against the ask and the cost basis, how
  it was listed, and a link to the sold listing on eBay. Before this, a
  finished sale opened as a full editor reading *"Ready to publish"*, one tap
  from re-listing the item that had already gone.
- `POST /api/publish` **refuses** a record whose stored status is `sold`. The
  UI no longer offers it, and the server no longer allows it — republishing in
  place would overwrite the sale's history with a second listing's life, and
  for an imported item it asks eBay to revise an item that has already ended.

Two things stay possible, because an archive needs them:

- **The sale's own numbers** (`sold_price`, `purchase_price`) remain editable —
  they are what the profit totals are made of, and eBay doesn't always report
  a sale amount. Saving them can't move the record off `sold` (`_sticky_status`).
- **Relist as new listing** (`POST /api/listings/{id}/relist`) copies the
  listing into a **brand-new draft**: the copy, the specifics and whatever
  photos survive, with every field describing the finished sale (item id, SKU,
  sale price, sale date, per-marketplace state) cleared. The sold record is
  left untouched. Photos are *copied, not moved* — though a sale purges the
  session's images to reclaim storage, so an app-created listing usually
  relists with none and the response says so (`photos: 0`); an imported
  listing's eBay-hosted `image_urls` carry over as they are.

An **ended** listing is unchanged: it still relists in place from Inactive,
because nothing about it is finished history.

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
