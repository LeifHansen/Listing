# Thryft Shop

**Snap it · AI writes it · list it everywhere.**

Turn product photos into a complete, ready-to-publish listing — on eBay,
Etsy, and Depop, individually or all at once.

Upload one or more images → the app **optimizes** them for eBay, **asks you
what each item is** (one optional box per item, with its photos on screen),
uses a vision **"lens"** to identify the item, **generates** a full
listing (title, description, item specifics, suggested price/category), shows
an **editable preview** where you can tweak fields manually or with a prompt,
then **publishes** it live on eBay (and Etsy / Depop) through the seller's own
connected account. Drafts stay in the app until you publish them; with no
marketplace connected the app writes the exact API payload it would have sent.

Getting ready to ship? Start with [`LAUNCH_CHECKLIST.md`](LAUNCH_CHECKLIST.md).

## Pipeline

```
 Upload images ──▶ Optimize (Pillow) ──▶ [Group into items (bulk)] ──▶
 Ask the seller (one box per item) ──▶ Identify (Gemini or Claude vision) ──▶
 Editable preview (manual edits + prompt refine) ──▶ Publish (eBay / Etsy / Depop, or dry-run)
```

| Stage | What happens | Tech |
|-------|--------------|------|
| Optimize | Honour the camera's EXIF, turn the item upright when it was shot lying sideways or on its head (a vision pass built for objects as much as clothing, applied only when two looks agree), cut the background onto a white canvas with a soft contact shadow (when removal is on) — never on a close-up of a tag or a label, where there is no background to take off, and never by the model on a PAINTING, PRINT or POSTER, which is cut to its own outer border or left alone — resize to 1600px, strip the metadata, and keep the pre-cutout frame for the passes that read the item | Pillow + Anthropic API |
| Identify | Photos sent to a vision model — the frame as shot, not the cutout, so a background removal can never cost the pass the tag it has to read; returns structured listing draft (keyword-ordered title, and a long SEO description in labelled sections — overview, key details, condition, measurements, why you'll love it) + an overall confidence (low / medium / high) that is stamped onto the draft and shown on its card + "missing info" to verify | Google Gemini API (`GOOGLE_API_KEY`) or Anthropic API |
| Price | The draft's price is the one thing the photos cannot answer, so a second pass looks it up. On the Google backend it runs with **Google Search grounding**: the model searches for what comparable items actually sold for and answers with the pages it read, and those URLs — the ones the API vouches for, not the ones the model typed — are what the finding is cited with. Never lowers a price and never demotes an item; it may raise a price to the bottom of the researched range, name a hedged item, and flag the more valuable variant the photos cannot rule out | Google Search grounding, or Claude's web search |
| Hints | Optional "Notes for the AI" on the uploader — the seller's own comma-separated list (`one vintage ralph lauren polo, two lacoste polos different size color`). Read as a strong prior by the draft, and as the expected inventory by bulk grouping; the photos still decide the facts. Saved with the session, so "Start over" re-drafts with them | Anthropic API |
| Ask | The pipeline **stops before it drafts** and asks, once per item, with that item's optimized photos on screen and the grouping's own guess beside them (`awaiting_notes` on the job status; `POST /api/bulk/notes/{job_id}` answers it). Every box is optional and one button moves on — all blank is byte-identical to a run without the step. What is typed outranks the pile-wide hints for that item, since it was written looking at these photos. Nothing is drafted or charged while it waits, the worker returns instead of holding a thread, and the pause survives a restart, so a seller can answer after lunch. Saved per item, so "Start over" keeps it | Anthropic API |
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

### Identifying on Google instead

Set `GOOGLE_API_KEY` ([AI Studio](https://aistudio.google.com/apikey)) and two
passes move to Gemini: identifying the item, and pricing it. The key is the
whole switch — there is no second setting to remember, `/api/health` reports
which backend answered as `identify_provider`, and everything else in the app
(refine, the item-specifics fills, the art lookup) still runs on Claude.

Pricing is the pass that earns the move. Asking a model what a jacket is
worth gets you the model's memory of jacket prices; on the Google backend the
lookup runs with **Google Search grounding**, so it searches before it answers
and hands back the pages it used. A price with a source attached is a
different object from a price.

| Setting | Default | What it does |
|---------|---------|--------------|
| `GOOGLE_API_KEY` | — | The switch. Also read as `GEMINI_API_KEY` / `GOOGLE_AI_API_KEY` |
| `GOOGLE_VISION_MODEL` | `gemini-pro-latest` | Which Gemini looks at the photos. A rolling alias by default — Google retires model ids, and a pinned default eventually 404s every draft. If the configured model does not exist, the app lists what the key can call, picks the newest of the same tier, and logs the swap |
| `IDENTIFY_PROVIDER` | `auto` | `auto` reads as google when a Google key is set. Pin to `google` / `anthropic` to force one; a pin to a backend with no key falls back rather than taking identification down, and says so in the config warnings |
| `GOOGLE_THINKING_BUDGET` | `-1` | How hard the model may think, if you want to pin it. Takes a number (a 2.5-style token budget) or a word (a 3.x-style `low`/`high` level); `-1` sends neither, which is right on every model |
| `RESEARCH_PASS` | follows the backend | The grounded pricing lookup: `auto` on Google, `off` on Claude. Claude's version is a server-tool loop that searches sequentially inside the identify request and adds a minute or more per item — Gemini's is one call that searches server-side, which is what makes running it by default affordable. Pin it to `off` / `auto` / `always` either way |

## Reading production errors

`flyctl logs` used to be the telemetry, and `.github/workflows/fly-logs.yml`
said so. Failures are now recorded as they happen, so there is something to
read that outlives Fly's retained window and can be queried.

**One row per distinct failure, with a count** — not one per occurrence. A bug
hit ten thousand times is one row that says ten thousand. That is what keeps
the list readable during an incident, and what bounds the table by how many
things are broken rather than by traffic. The collapsing is done by a
`fingerprint` built from the module, the function, the exception type and the
message TEMPLATE — deliberately not the line number and not the release, so a
refactor or a deploy does not make every open bug look new.

**Where to look:**

| | |
|---|---|
| The console | **Admin → Errors.** Newest-seen first, click a row for the traceback. |
| A specific complaint | The 8-character reference the app showed the seller is the row's `reference`, and the `X-Request-Id` on the response. One value, three places. |
| Programmatically | `curl -H "x-error-feed-token: $ERROR_FEED_TOKEN" $SITE/api/ops/error-feed` |
| Months later | `ops/errors/YYYY/MM/DD.jsonl.gz` in R2 — written daily, before the table is pruned. |

Capture starts at **WARNING**, not ERROR, because this codebase fails soft:
there are ~240 `log.warning` calls against 7 `log.error`, so the real failures
are at warning level. "Is this serious" is answered by a derived `severity`
instead. A warning logged inside an `except` block gets the live traceback
attached automatically, so those rows are actionable without their call sites
being touched.

Everything recorded is scrubbed — Stripe keys, JWTs, emails, IPs, OAuth codes
in URLs — on the way to stdout as well as into the table. The shape survives so
the line stays readable (`sk_live_<redacted>`, not nothing). The support
reference is deliberately *not* redacted: it identifies nothing on its own and
is the only join between a complaint and a cause.

`ERROR_CAPTURE_ENABLED=0` turns the whole thing off without a code change.

### The daily triage job

`.github/workflows/error-triage.yml` runs at 09:41 UTC, reads the feed, and
picks the failures worth a fix using `.github/scripts/triage_errors.py` — where
the thresholds live so they are reviewable in a diff.

**It ships inert.** It collects, triages and writes a run summary; it opens
nothing. To let it propose fixes:

1. `fly secrets set --stage ERROR_FEED_TOKEN="$(openssl rand -hex 32)"` and add
   the same value as the GitHub Actions secret `ERROR_FEED_TOKEN`. (`--stage`
   because this app runs a single machine — an unstaged set restarts
   production immediately.)
2. Add `ANTHROPIC_API_KEY` as an Actions secret. It exists today only as a Fly
   app secret.
3. Add `AUTOFIX_GITHUB_TOKEN` — a fine-grained PAT or GitHub App token with
   contents and pull-requests write. **This one is not optional.** A pull
   request opened with the default `GITHUB_TOKEN` does not trigger
   `pull_request` workflows, so `ci.yml` never runs and the four required
   `Gates / …` checks sit unstarted forever. An autofix PR that looks green
   because nothing ran is worse than no autofix at all.
4. Set the repository **variable** `ERROR_AUTOFIX_ENABLED` to `1`.

Run it once by hand from the Actions tab before trusting the schedule.

Two properties are deliberate and worth not "fixing":

**A clean day is silent and green.** It opens nothing and notifies nobody. The
only thing that fails the run is being unable to *read* the feed — "I could not
look" and "I looked and it was clean" are different outcomes. This is the
opposite of `health-watch.yml`, which fails to alert, and the difference is on
purpose: a job that goes red every morning that production has a bug is a
notification nobody reads by the second week.

**The agent gets no network and no Fly token.** Error text comes from
production, and some of it from people's browsers, so it is attacker-controlled
input. The workflow is split in two jobs for that reason alone: `collect` holds
the credentials, `fix` reads the text. Its pull requests are drafts.

To silence a failure permanently, add its fingerprint to
`.github/known-errors.json`. A checked-in file, so muting an alarm shows up in
a diff.

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

### What the gates actually check

Four jobs, defined once in `.github/workflows/gates.yml` and called by both
`ci.yml` (on a pull request) and `deploy.yml` (before shipping), so what runs
before a merge and what runs before production are the same definition rather
than two copies that drift.

| Job | ~time | What only it can prove |
|-----|------:|------------------------|
| Lint + unit tests | 35s | The modules with no heavy dependencies really have none — `listing_prompt.py` and `barcodes.py` hold rules that must stay testable without the SDK installed |
| Cutout safety | 10s | The photo pass works on **Pillow alone**: no rembg, no model download |
| Frontend build | 60s | The only thing that type-checks the JSX, plus lint and the 500-odd component tests |
| App smoke test | 4m | The whole backend suite against the app's **real** dependencies, then a browser walking every screen of the built app against a booted server |

**Every backend test runs, and nothing may skip.** Two thirds of this suite's
files `importorskip` fastapi, Pillow or anthropic, and the smoke job is the
only one that has all three — so it runs `pytest backend/tests` over the whole
directory and **fails if a single test skips**.

That guard is there because the alternative was tried. The job used to name
its files one by one, and 44 of them — a fifth of the suite — were in no job's
list at all: they skipped in the fast job, were absent from the smoke job, and
reported nothing anywhere. In [#250](https://github.com/LeifHansen/Listing/pull/250)
a function signature changed, the test doubles in one of those 44 files went
stale, every price comp lookup started raising `TypeError` — which the caller
swallows as "eBay is down" — and all four gates went green over it.

So there is no list: the directory is the list, exactly as the smoke job's
dependencies are `requirements.txt` rather than a second hand-written copy of
it. If a test ever genuinely cannot run in CI, **deselect it** in the workflow
with a marker rather than letting it skip — deselecting is visible in a diff,
and skipping buys the whole problem back.

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

Everything else worth checking, production answers itself — but mind **which**
endpoint, because that changed. `/api/health` is anonymous and unrate-limited,
so it was cut back to liveness plus the capability flags the UI reads; it no
longer carries a single field below. The operator detail moved behind
`ADMIN_TOKEN`, and the operational numbers are on the public readiness probe:

```bash
# Disk, object storage and the database. Public — no token, and what the
# health-watch alarm reads.
curl -s https://<app>.fly.dev/api/ready | python3 -m json.tool

# Everything else: config_warnings, bg_engines, the R2 bucket and the missing
# variables by name, tokens/Stripe, the raw db and objstore errors.
curl -s -H "x-admin-token: $ADMIN_TOKEN" \
  https://<app>.fly.dev/api/admin/diagnostics | python3 -m json.tool
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
- **`image_engine`** on `/api/ready` is the on-server background-removal
  model (`isnet-general-use`, baked into the image), whether it has loaded,
  how long the last inference took, and **`chain`** — the engines that cut a
  background out, in the order they are tried. There is always a remote engine
  ahead of `local` when its key is set, and `local` is always the last entry,
  because it is the only engine that needs no credentials: an app with no keys
  reads `["local"]` and behaves exactly as it did before any of this.
  `BG_ENGINE` picks the front of that chain (`auto`, `removebg`, `leonardo`,
  `local`); see `.env.example` for the whole story, including that remove.bg's
  standalone API shuts down on 2026-12-01 and `leonardo` is its successor.
  Reading `chain` is how an **expired or mistyped key gets noticed**: without
  it, the only symptom is sellers' cutouts quietly going back to the slow
  built-in model, and the studio only says so per-cutout (the `degraded` flag
  on `/api/image/remove-bg`). The older Pixian / Photoroom / Adobe engines are
  still gone — they were removed in #246 — so a `PHOTOROOM_API_KEY` or
  `LIGHTROOM_API_KEY` still set on the app remains a dead credential and can
  be unset.
- **`disk_free_mb`**, **`checks`** and **`object_storage`** on `/api/ready`
  cover the rest, with **`build`** on `/api/health`; `health-watch.yml` alerts
  on them every two hours. It reads `/api/ready`, not `/api/health` — pointing
  it at the latter is what left it failing on every schedule for four days
  against a production that was entirely healthy, and an alarm that is always
  red cannot report the real thing. `object_storage` there is two booleans on
  purpose (`configured`, `degraded`): the bucket name and the raw reason name
  the R2 account, so they stay on the diagnostics endpoint.

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
> `STRIPE_*`), plus the ability to destroy or redeploy any app on it.
> The deploy workflow now prints a warning when it finds `FLY_API_TOKEN` or
> `FLY_ACCESS_TOKEN` among the app's secrets.
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

### The seller's own store shelf

eBay's category says what an item **is**. A **store category** is a shelf in
the seller's own storefront nav — "Vintage Tees", "Beanie Babies" — invented
by them, numbered per account, and what a returning buyer browses. Only a
seller with an eBay **Store** subscription has any, and every listing this app
published before landed at the store's top level, because nothing ever sent
one.

Nothing can suggest a shelf from the outside (nobody but the seller knows what
theirs mean), so the draft is matched against the store's own tree, read once
per account per six hours from `GetStore`:

- eBay's resolved category **path** is the strongest signal, then the title
  and brand, then the item specifics — which reinforce a match and never make
  one on their own;
- a shelf earns it by having its **whole name** seen ("Vintage Tees" needs
  both words), so the more specific shelf beats the general one it sits under;
- a catch-all shelf ("Other", "More Items") is never matched into, because
  every listing matches it equally well; and
- below that bar the answer is **nothing**. An unfiled listing is one dropdown
  away from filed; a wrongly filed one is invisible on the shelf it belonged
  on.

The Category card shows the shelf as a picker — the store's own list, by the
path the seller reads in their menu — and draws nothing at all for an account
with no Store. `Storefront.StoreCategoryID` rides the publish and the revise
(only when the seller moved it), comes back on every sync, and eBay's `0`
("no store category") is read as no shelf rather than as shelf number zero.

> **Note on images:** eBay requires publicly reachable image URLs. In local
> dry-run mode the payload references `http://localhost:8000/media/...`. For real
> publishing, deploy the app on a public host (or swap in an image CDN) so eBay
> can fetch the optimized photos.

### Selling abroad without posting abroad (eBay International Shipping)

eBay International Shipping is eBay's own export programme for US sellers:
the seller posts every sale to eBay's US shipping hub with an ordinary
domestic label, and eBay carries it the rest of the way — the international
leg, customs, and any return from overseas are eBay's, and the buyer pays for
that leg. Turning it on changes who a listing is sold to, so it is a switch
the seller flips once, under Settings → *International shipping*, and it is
**off** until they do.

The switch reaches the two places eBay reads it:

- **The shipping policy the app creates.** "Create my policies" sends
  `globalShipping: true` on the fulfillment policy — the flag eBay kept from
  the Global Shipping Program when eIS replaced it — and the terms dialog says
  so first (*Where you post to: The United States, and worldwide through eBay
  International Shipping*), because a policy is a promise the seller reads
  before it is made. A domestic policy the seller already has is not reused
  for it: eBay refuses a second policy under a name in use, so the worldwide
  one gets its own (`USPS Ground Advantage + eBay Intl Shipping (Thryft
  Shop)`), and the policy picker labels which of two otherwise identical
  policies ships abroad.
- **Every new listing.** The publish carries
  `ShippingDetails.GlobalShipping=true`, the Trading API's per-listing opt-in,
  so a listing under a policy the seller made elsewhere says so too. The
  dry-run payload shows it.

Same rules as "Allow offers", because it is the same kind of switch: an absent
or unreadable preference is a no; a revise never carries it (the switch says
*new* listings, and flipping it must not walk back through a live store); and
**off sends nothing rather than an opt-out** — a seller eBay enrolled on its
own had listings going abroad before this switch existed, and must not lose
that to a toggle they never touched. eBay applies the programme only to
sellers enrolled in it, and enrolling is done on eBay, not here.

### Filling eBay's item specifics — and going back for the blanks

Item specifics are the fields buyers filter by, so an empty one is a search
the listing never appears in. The fill (`claude_ai.fill_aspects_combined`) is
handed the whole aspect list eBay publishes for the listing's category and
asked to fill what it honestly can, matching fixed-choice values verbatim
against eBay's own allowed list and ticking every box that applies on the
multi-select ones.

What it does with an aspect it is unsure of is **silently nothing**, and for a
long time nothing downstream asked how many were left. That is how a listing
written here reached eBay with *Subject*, *Era*, *Occasion*, *Packaging* and
*Character* blank while eBay's own suggester — same photos, same title —
offered all five on the listing form the seller opened next.

So the blanks now get a second, narrower ask (`claude_ai.fill_missing_aspects`,
one vision call over four photos, `SPECIFICS_COVERAGE=0` to turn off). It is a
different question from the first pass — not *read this item* but *you have
already read it; what is it obviously about* — over a short list instead of
thirty, carrying the finished title, description and filled specifics as
context so its answers agree with them. Answers merge through the same path as
the first pass's, so nothing overwrites what the seller wrote and a value that
is illegal for its aspect is still dropped.

**Identifiers are not on the list it is shown.** A UPC, EAN, ISBN, MPN, serial
or anything else shaped like a code (`taxonomy.is_identifier_aspect`) is read
off the item or it is wrong, and *"prefer a defensible inference to a blank"*
in the same prompt as an empty UPC box is how a model talks itself into twelve
digits that belong to somebody else's product.

**The checkboxes, and eBay's own suggestions.** Two properties of an eBay
aspect vary *independently*, and running them together is what left the
tick-box specifics half-filled:

- **Cardinality** is the shape of the answer. `MULTI` is what eBay draws as
  checkboxes — *Features*, *Style*, *Occasion*, *Season*.
- **Mode** is what the aspect's value list *means*. `SELECTION_ONLY` makes it
  law: a value not on it is refused at publish. `FREE_TEXT` makes it eBay's own
  **suggestions** — the values its listing form offers under the box, which the
  Taxonomy lookup returns for a great many free-text aspects.

Every checkbox path used to test for `SELECTION_ONLY` *and* `MULTI`, so the
tick-box aspects eBay reports as free text fell through all of them: described
to the model as one plain text box, drawn in the editor as a single input, and
eBay's suggested values fetched on every lookup and shown to nobody. Now
cardinality alone decides the shape — checkboxes in the editor, "tick every
value that applies" in the prompt — and mode alone decides whether the list is
quoted as *allowed values* or as *eBay suggests* (with an **add your own** box
beside an open list, and a `datalist` of the same suggestions on single-value
free-text fields, since a publish can be refused over wording eBay would have
handed us).

**One ticked box is not an answered aspect.** A jacket whose *Features* says
only "Pockets" is missing Breathable, Lined and Water Resistant, and each is a
filter it never appears in — but holding any value at all read as *answered*,
so the coverage pass was never shown the aspect. It now tops up partly-ticked
multi-selects (`fillable_blanks(..., top_up_multi=True)`), told which boxes are
already ticked so it adds rather than repeats. An aspect the **seller** typed or
confirmed is never topped up, and the dashboard's "how many specifics are
blank" count deliberately does not ask this wider question — answering it there
would tell a seller a finished listing is unfinished.
### Reading the stickers: branding in any language, and the barcode

The most valuable thing in most photos is not the item — it is the sticker on
it. A UPC names the **exact product** in eBay's own catalogue, which is a far
better comp search than any title this app can write. A Japanese neck tag, a
Cyrillic factory stamp, an importer label or a `Fabriqué en France` line names
the market the item was sold in and usually the decade. A licence line
(`© 1998 Sanrio`) dates it to the year. All of it is printed, in frame, and
free to read.

The scan now looks for it explicitly (`listing_prompt.STICKER_AND_BARCODE_RULE`,
shared by the identify pass, the tag locator and the zoom-and-transcribe pass,
so all three read under one set of rules rather than three paraphrases):

* **every sticker, in any script.** Importer and licensee stickers, foil and
  hologram seals, backstamps, hallmarks, model and serial plates, copyright
  lines, retail price stickers. Text in Japanese, Korean, Chinese, Cyrillic,
  Greek, Arabic, Hebrew, Thai, Devanagari or accented Latin is transcribed
  **verbatim in its own script**, then romanized, then given its English
  equivalent (`ユニクロ` = UNIQLO, `日本製` = Made in Japan) — and answers the
  item specifics in the English form eBay expects. A script the model cannot
  read is **described**, never translated into a brand it does not say.
* **barcodes get a crop of their own**, even with no other label near them —
  the tag locator used to draw a box only where a garment tag was.

**Then the check digit decides what is believed.** A model reading twelve
digits off 6-point type misreads them, and a misread UPC is the most expensive
mistake this app can make: eBay matches it against its *catalogue*, so a wrong
one does not bounce — it succeeds, and quietly attaches another company's
product page, photos and price history to the listing. Every GTIN (UPC-A,
EAN-13, EAN-8, GTIN-14) and ISBN-10 carries its own check digit, so
`services/barcodes.py` verifies each read before anything is written:

| Read | What happens |
|------|--------------|
| Check digit agrees | Written as `UPC` / `EAN` / `ISBN` at confidence **high** — it was read, not inferred |
| Check digit fails | Never touches the listing. Becomes a *"confirm the barcode number"* note the seller answers in five seconds with the item in their hand |
| Model says a digit is obscured | Dropped outright — a code with a digit missing is not a code |
| MPN / model number | No checksum exists to agree with, so it goes on at **medium**: the editor's review flag |

The same guard runs again in `taxonomy.sanitize_specifics`, the last thing that
touches a listing before eBay does, over a code from **any** pass — but never
over one the seller entered or confirmed themselves (`confidence == ""`). They
are holding the item; a rule that overrides them is a rule that deletes their
work and says nothing.

**And a verified UPC prices the item.** `_price_against_comps` asks eBay Browse
by `gtin` before it asks by keywords. That is a different question with a much
better answer: a keyword search matches listings that *sound* like this one —
and a good title, packed with artist, edition and size, often matches nothing
at all — while a UPC matches the same product, so the median it returns is this
item's price. An EAN or ISBN searches as digits instead (Browse documents
`gtin` as taking a UPC), with an ISBN-10 converted to its ISBN-13 form first,
because everything printed since 2007 carries the 13-digit one.

### The tag that is still on it: new, and what new is worth

A Scotch & Soda shirt was photographed with the brand's own swing ticket still
attached, **$130** printed on it. The draft came back **"Pre-owned - Good", $49**.

Nothing had misread the photo. Three rules that were each defensible alone
produced it between them, and `listing_prompt.RETAIL_TAG_RULE` and its server
side now hold each one:

| What went wrong | The fix |
|-----------------|---------|
| The condition instruction was "grade the **wear** you can see" — which against a garment nobody has worn finds none, and returns the middle of the used ladder. Nothing said an attached tag *ends* that question. | An attached hang tag, a sewn price ticket, a bagged spare-button packet, an unbroken seal or a factory poly bag is direct evidence of **NEW**, and it **outranks** the absence of visible wear: a fresh garment and a gently worn one look identical at photo resolution, and the tag is the thing that tells them apart. |
| eBay's enum for "new with tags" is the bare `NEW` (condition id **1000**). A model looking at a tagged shirt writes the words everyone uses — `NEW_WITH_TAGS`, `NWT` — which is not on eBay's list, and the server's fallback for an unrecognised grade was `USED_EXCELLENT`: id **3000**, which eBay labels **"Pre-owned - Good"** in apparel. That is where the word "good" came from, and it was silent. | `claude_ai._condition_enum` maps the wordings onto the enum, and its fallback **never crosses the new/used line**: an answer that plainly says new lands on `NEW`, `NEW_OTHER` or `NEW_WITH_DEFECTS` instead of being flattened into a used grade. It stays one-way — `like new`, `near new`, `looks new but worn` and anything unreadable all stay on the used ladder, because a used item relabelled new is a return and a defect. |
| The $130 was defined as `purchase_price` ("what it costs to buy this item right now") and the sticker rule said a price tag fills that field *"never the resale price"*. So the best price evidence on the item was thrown away — **and** the profit report was told the seller had spent $130 on it. | Two fields, because they are opposite facts. `retail_price` is the MSRP off the **brand's own** tag, box or blister card; `purchase_price` is a **resale** sticker — thrift, consignment, outlet, price-gun, handwritten — which is what the seller actually paid. Both are read, both are shown in the price card, and the editor says what percentage of the tag the listing price is. |

**And the price gets a floor.** `_price_against_comps` only overrules a draft
an order of magnitude under the comps (`UNDERPRICE_RATIO`, 0.6) — the right bar
when the gap could be honest: a rough item, a quick-flip strategy. None of
those excuses survives a tag that is still attached, and comps are frequently
silent anyway (no eBay credentials, no comparable listings, a keyword query
that matched nothing). So `_price_against_retail` runs after it: a **NEW**-family
item priced under `RETAIL_FLOOR_RATIO` (0.45) of its own printed retail price
is raised to that floor, and the seller is told in `missing_info` rather than
handed a different confident-looking number in silence.

It cannot become a machine for inflating listings. The floor is **capped at
what comparable listings actually ask** (the comps' 75th percentile, passed out
of `_price_against_comps` via `market_out` — which reports the market even when
the draft's own number stood, a fact the return value cannot express). Real
listings for the real item beat arithmetic on an MSRP; the tag only gets to say
the draft was under them. And a **used** item is never touched: a worn shirt
genuinely does sell for a fifth of its tag, and the tag is not evidence about it.

**The brand gets a second reading too.** The identify pass reads a hang tag at
whole-photo resolution, where a brand is a smudge it half-recognises — and once
it wrote *anything* into `brand`, nothing downstream looked again: the maker
hunt ran only on a blank. A wrong brand was permanent, in the field eBay's
search weights most heavily, while a zoomed and readable crop of that same tag
was already being passed to the very next call for something else. The maker is
now asked for whenever there are tag crops in the call (the same call — it costs
the tail of a prompt), and a reading that **disagrees** with the drafted brand
goes to the adversarial verifier. Confirmed at **high** confidence, it replaces
the brand and is carried into the title and the Brand specific; `_same_maker`
folds away `&`/`and`, apostrophes, legal suffixes and the city a brand prints on
its own tag, so "Scotch & Soda Amsterdam" and "Scotch and Soda" are one answer
rather than a dispute. Medium confidence still only fills a blank, and a brand
the **seller** entered is never overwritten.

### Vintage denim: the selvedge edge, the red tab, the patch and the lot code

A pair of Levi's 501s is a $30 listing or a $3,000 one, and the same handful
of details decides which — the lettering on the red tab, the edge of the
fabric along the outseam, the wording on the patch, the row of numbers on the
care tag, the stamp on the back of the top button, the rivets inside the back
pockets. Every one is in frame in an ordinary set of photos, and every one is
searched for **by name** by the buyers who pay the premium: "Big E", "redline
selvedge", "501XX", "hidden rivets", "single stitch". A draft that says
"Vintage Levi's 501 jeans" about a Big E redline pair has left most of its
price in the pocket.

The sticker rule says "read every tag". What it could not say is what to do
with a fabric edge, which is not a tag and is not text: a selvedge outseam is
a woven detail visible only where a hem is turned up, and a model not told to
look there reports the hem as a hem. `listing_prompt.VINTAGE_DENIM_RULE` names
each place to look, what each looks like, and the era each supports — the
facts every collector's guide prints, stated with the ranges those guides agree
on rather than to the year:

| Marker | Where | What it says |
|--------|-------|--------------|
| Selvedge edge | The outseam, at a turned-up hem or turned-out leg (also the coin pocket edge) | A clean self-finished band, red thread on Levi's ("redline"), against an overlocked non-selvedge edge. Phased out on 501s in the early-to-mid 1980s, so redline + USA = before about 1986. Not Levi's-only and not vintage-only: LVC reproductions and Japanese makers use it, so the brand still comes off the patch |
| Red tab | Right back pocket, since 1936 | Three independent facts. **Lettering**: `LEVI'S` in capitals ("Big E") until 1971, `Levi's` with a lowercase e since — but LVC and, since 2018, Levi's Premium use a Big E too, so the inside of the garment (a modern care tag, an MMYY code, Made in Japan, post-2003) decides whether it is one or a reproduction of one. **Faces**: lettered on ONE side only from 1936 to the early 1950s — the earliest tab there is — double-sided from about 1951–54; single-sided came back in the mid-1980s, so it only dates a pair when the rest agrees. **The ®**: arrived with the double-sided change, so `LEVI'S` with no ® is earlier than `LEVI'S®`; a tab with the ® and no name is a modern trademark-only tab, and the one tab LVC never uses |
| Tab colour | The line, never the era | Orange = the fashion line, 1960s until 1999 (646, 517), a different line, not a lesser one. White = corduroy and "Levi's for Gals", the first women's line, in the 1960s–70s — **not** a late pair. Silver = the loose/baggy "SilverTab" line, late 1980s through the 1990s, searched by name |
| Patch | Back waistband | Real leather until the mid-1950s, leather-look card after. The lot: `501`, `501XX`, `505`, `517`, `646`. `XX` = the 1966–68 pairs or earlier (LVC put it back on reproductions from 1987). W/L here is the **tag** size |
| Care tag | Inside waistband or pocket seam | None = before about 1971–73. The row is four facts: the lot-and-finish code (`501-0115` — the four digits after the dash are the finish: `0000` rigid Shrink-to-Fit, `0115` stonewash, `0660` black — never a size or a date), the tag size, `WPL 423`, and the production code (a month and year digit in the 70s–80s, a four-digit MMYY from about 1993: `0496` = April 1996). `MADE IN U.S.A.` = 2003 or earlier |
| Button back | Reverse of the top button | A factory stamp that should match the care tag's: 555 Valencia Street SF (also 1990s–2002 LVC), 524 El Paso, 554 San Antonio, 553 North Carolina. Confirmation of the rest, never a build year alone |
| Back pockets | Inside out | Hidden rivets 1937 to about 1966, then bar tacks; exposed rivets before 1937. Single-needle arcuate before about 1947; painted arcuate = WWII (1942–47) |
| Fly | Top button and zip pull | 501 is button fly; a zip is a different lot (505, from 1967). A V-stitch beside the top button is roughly pre-1970. Talon, Scovill and Gripper pulls date a pair |

The rule rides with the identify pass (appended to `LISTING_SCHEMA` after the
sticker rule), the tag locator (`DENIM_TAG_SCAN_RULE`, which adds `tab`,
`patch`, `button` and `selvedge` box kinds so the edge at a hem gets a crop of
its own), the zoom-and-transcribe pass (`DENIM_TRANSCRIBE_LINES`: one `RED
TAB:` / `LOT:` / `CARE TAG ROW:` / `BUTTON BACK:` / `SELVEDGE:` line per marker,
so the specifics fill quotes them the way it quotes a barcode) and the
specifics fill itself, which maps them onto eBay's aspects — Model / Product
Line from the lot, Closure, Fabric Type or Features with the value that says
selvedge, Country/Region, Era. One text, one home, so the passes cannot drift.

Two things the rule forbids matter as much as what it asks for:

* **A marker not in the photos is never written.** Each is a price claim a
  buyer checks on arrival: "selvedge" on a pair whose hem was never turned up
  is a return, not a guess. A hem not turned up makes "turn up the hem and
  photograph the outseam edge" a `missing_info` item; markers that disagree
  (a Big E tab beside a 1990s care tag is a reproduction or a swapped tab) are
  said so and put the era in `missing_info` rather than resolved.
* **The tag size is not the size.** Shrink-to-Fit denim has shrunk one to
  three inches. The draft gives "Tag size W32 L34", and the measured size only
  from a tape in the photos — otherwise "measured waist, inseam and rise" is a
  `missing_info` item. Collectors buy on measurements.

### Denim in a pile: the front and the back of one pair

Denim arrives in bulk mode in a fixed shape: laid flat, one pair at a time,
the front and then the same pair turned over. Six pairs is twelve photos — and
bulk mode read it as twelve items, every pair listed twice, once for its front
and once for its back, with the halves scattered so a front sat under one
draft and its own back under another. Two live eBay listings for one pair of
jeans is the worst outcome bulk mode has, and the seller has to end one by
hand.

Every rule the grouping pass had pushed it there. "Identity evidence outranks
looks" is right, and on denim every identity mark — red tab, leather patch,
lot, W/L size — is on the **back**, so a front and its own back read as one
photo with marks and one without. "Count the tags and patches you can see and
expect at least that many items" counted the tab, the patch and the care tag
of *one* pair as three. Nothing said what the front of a pair is supposed to
look like, so the pass inferred it was a different garment.

`listing_prompt.DENIM_FRONT_AND_BACK_RULE` tells the grouping passes the shape
of the upload instead, and rides with all three of them — the pass that
**groups** (`_GROUP_SCHEMA`), the one that **merges** an over-split pile
(`_GROUP_VERIFY_SCHEMA`) and the one that **splits** a group that is really
two items (`_GROUP_SPLIT_SCHEMA`), since each can make this mistake on its
own:

* **The two views look nothing alike by design.** The front is the fly, the
  top button, the coin pocket; the back is the yoke, the arcuate stitching,
  the tab and the patch. That difference is what one pair looks like from both
  sides, not evidence of two items.
* **Pairs are counted by backs, not by markers.** The number of pairs is the
  number of back views (equivalently, of distinct patches) — one pair shows a
  tab *and* a patch *and* a care tag and is still one pair. A photo with no
  patch in it is a **front**, not an item whose tag went missing.
* **A second pair is a second back that reads differently**, quoted: "501 W32
  L34" against "505 W34 L32", or a plainly different wash, fade or repair. Two
  backs that read the same, or two nobody can read, stay one listing — a spare
  photo is a drag away, a duplicate live listing is not.
* **The merge pass is told which way its evidence runs.** It sees one photo
  per group and can never read a patch, so a group whose photo is a *back*
  belongs with the group holding the front it was shot with — but several
  groups each showing a *front* are several pairs. Never merge two fronts
  because neither shows a patch.
* **A pair keeps its own order and no photo moves between pairs.** The back
  that follows a front is the back *of* that front. A swapped back puts the
  wrong lot, era and W/L size on two listings at once, and neither the seller
  nor the buyer can see from the photos that it went wrong.

The grouping pass's own counting line was corrected with it: it counts tags
and patches that **read differently**, rather than every mark in frame.

The shuffle has a second half no prompt can fix, because the answer is free to
list a group's photos in any order and does. `_lead_then_upload_order` keeps
the lead photo the model chose — the draft's cover image, the one thing it is
actually asked to pick — and puts everything behind it back into the order the
seller uploaded it in. It runs on the first-pass answer and on the split
check's, and the group's order is the listing's: `indices` are copied out to
`img_000.jpg`, `img_001.jpg`, … in exactly that sequence.

### Art: the signature, the edition number, the chop and the surface

A print is a $15 listing or a $1,500 one, and the difference is written in
pencil in the bottom margin — a hand signature at the lower right, an edition
fraction at the lower left, an embossed chop beside them. A painting is an
original or a canvas print, and the difference is a surface the camera can
see. Every one of those marks is in frame in an ordinary set of photos, and
every one is searched for **by name**: "hand signed", "numbered", "artist
proof", "original oil". A hand-signed, numbered lithograph was drafted as
"Vintage Art Print" — no artist, no "signed", no edition — because a pencil
signature is thirty pixels tall in a whole-frame photo and nothing told any
pass that the small grey scrawl under the picture is the most valuable thing
in it. `listing_prompt.ART_RULE` names each place to look, what each mark
looks like, and what each one establishes:

| Mark | Where | What it says |
|------|-------|--------------|
| Signature | Bottom margin, lower right first; a lower corner of a painting; the base of a sculpture; the back | A **hand** signature sits on the paper — pencil with a graphite sheen, ink, paint — and does not share the printed image's dots. One **in the plate** is part of the printed picture and means the artist signed the original, not this sheet: never "hand signed". Read letter by letter; the letters name the artist for the title, the brand and the Artist specific |
| Edition number | Lower left of the margin | `84/250` = this sheet over the edition size (Edition Size 250): a numbered limited edition. `A/P`, `E/A`, `H/C`, `P/P`, `T/P`, `B.A.T.` are proofs, searched by name, sometimes with their own count (`A/P 3/20` — the 20 is how many proofs, not the edition). A missing fraction is a missing photo, never an open edition |
| Title on the sheet | Centre of the lower margin, in pencil | The work's name, right after the artist in the title |
| Chop / blind stamp | A lower corner of the margin, embossed and colourless | The printer's, publisher's or artist's mark: Tamarind, Gemini G.E.L., Mourlot, ULAE, Tyler Graphics date and authenticate an edition. An ink stamp on the back is an estate, gallery or collection stamp |
| Plate mark | A rectangular indentation a few millimetres outside the image | An intaglio print (etching, engraving, aquatint, drypoint) pressed into damp paper. Lithographs, screenprints and offset reproductions have none; a plate mark around halftone dots is a fake one |
| Surface | Any photo taken close | Brushstrokes with relief, impasto and canvas weave = original painting; pooled washes and paper tooth = original watercolor or drawing; flat raised ink layers = serigraph; grainy crayon texture with no dots = hand-pulled lithograph; a random spray of tiny dots = giclée; a regular halftone rosette everywhere, signature included = offset poster or open edition; a uniform sheet with printed "strokes" or a mirrored wrap = canvas print, not a painting |
| Printed lines along the edge | Under the image | A credit line (`© 1987 Artist / Publisher`, "Printed in Italy", a museum and exhibition dates) names the publisher and the year of *this* printing and marks an open edition or exhibition poster — unless a pencil signature and number are also there. The publisher is never the brand |
| The back and the frame | Verso, frame back | Gallery, framer, exhibition and auction labels, a certificate in a sleeve, an inscription, a publisher's stamp — read verbatim. Deckled edges, a watermark (Arches, Rives BFK, Fabriano), foxing and toning say something about the edition and the age |

The rule rides with the identify pass (appended to `LISTING_SCHEMA` after the
denim rule), the tag locator (`ART_TAG_SCAN_RULE`, which adds `signature`,
`edition`, `stamp`, `caption` and `label` box kinds and says to box the whole
bottom margin when the writing is too faint to place), the zoom-and-transcribe
pass (`ART_TRANSCRIBE_LINES`: one `SIGNATURE:` / `EDITION:` / `TITLE ON SHEET:`
/ `STAMP:` / `CAPTION:` / `LABEL:` / `PLATE MARK:` / `SURFACE:` line per mark,
so the specifics fill quotes them the way it quotes a barcode), the specifics
fill (Artist, Signed, Signed By, Edition Type, Edition Size, Print Type,
Original/Licensed Reprint, Year Produced, Features) and the art lookup. One
text, one home, so the passes cannot drift.

The art lookup (`claude_ai.identify_artwork`, run by `_lookup_artwork` on any
art draft whose title does not yet name its artist) is now handed the zoomed
crops of those boxes beside its four whole frames — that is where the pencil
is legible — plus the specifics the zoom pass already read off the margin, and
it answers how the piece is signed and numbered as well as who made it. The
server folds that in the way it folds the artist: Signed, Signed By, Edition
Type and Edition Size are written where blank; "Hand Signed" and "Numbered
84/250" (or "Artist Proof") go on the end of the title when the 80 characters
allow; a year written on the piece fills a year row the category already put
on the draft. A brand of "Unknown artist" or "Unsigned" now counts as blank,
so the lookup runs instead of standing down over it, and an artist the zoom
pass read into the Artist specific reaches the brand the title and search use.

Two things the rule forbids matter as much as what it asks for:

* **A mark not in the photos is never written — and never denied.** "Hand
  signed", "numbered", "artist proof", "original" and a chop's name are each
  a price claim a buyer checks on arrival; "unsigned", "open edition",
  "poster" and "reproduction" about a margin under the mat, a back never
  photographed or a surface not seen up close are the same false claim in the
  cheaper direction. Nothing in the server ever writes "No", "Open Edition"
  or "Reproduction" from a reading's absence; a mark out of frame becomes a
  `missing_info` item naming the photo to take ("photograph the lower margin
  close up, both corners, and the back").
* **A signature is read, never completed.** The letters are transcribed as
  they appear; when they spell a name, that is the artist; when they spell
  part of one, the reading the letters, the hand and the image point to goes
  in `raw_observations` and `missing_info` as a reading to confirm — never in
  the title. Naming the wrong artist is the one error a buyer never forgives,
  and hedging the right one into "style of" costs the seller most of the
  price; both are avoided the same way.

### How sure the AI was, on the card

The identify pass grades its own draft — `low`, `medium` or `high` — and that
grade used to reach exactly one screen: the editor's header, for as long as the
session that made the draft stayed open. It rode on the identify *response*,
never on the listing, so the record every card is drawn from never had it, and
a seller triaging forty bulk drafts had no way to tell the one the AI was
guessing at from the ones it read off a label, short of opening each.

Every drafting path — the uploader's job, "Start over", the bulk worker, Shop
Mode's synchronous route — now stamps the grade onto the draft itself
(`Listing.ai_confidence`), and the cards read it from there: an **AI: low /
medium / high** chip on the drafts strip, the dashboard, the listings manager
and the bulk queue, with a tooltip naming what the grade is about (the title,
the brand and the price — the three fields a wrong answer costs most on). It is
a different fact from the per-specific ✓/⚠ flags, which say which *fields*
want a glance; this says whether the AI knew what the item *was*.

Three rules keep it honest. It is server-owned (`SERVER_OWNED_FIELDS`): a
refine rebuilds the listing from the model's echo and would drop it, so the
save path restores the stored value the way it does `enriched_at`. A relist
starts without one — the copy is of a listing the seller already reviewed,
published and sold. And it draws only on drafts: once a listing is live the
seller has stood behind it, and the AI's doubts about the first draft are not a
fact about the listing that is selling. A listing the AI never drafted (an
import, a hand-made one, a stub saved when the AI failed) carries `""`, which
is *no verdict*, not "medium".

### The price, on the card — and where an auction opens

Price was the last thing on a draft card that could only be changed by opening
the editor. Category and format had already moved onto the card face, for a
reason that applies to price more than to either of them: a seller reviewing a
batch of fresh drafts is reading a grid of numbers the AI chose, and the whole
job is changing the two or three it got wrong. Each one cost a trip into the
editor and back.

It sits under the format picker on every draft card there is — the drafts
strip, the dashboard's recent cards and the listings manager, in both the grid
and the list layout — and it asks for **whichever number the format actually
uses** (`lib/listingFormat`): a price on a Buy It Now, a starting bid on an
auction, both on an auction carrying a Buy It Now. A plain auction gets no
price box at all, because eBay does not read that field for that format and a
box for it invites a number that never leaves the app. Typing saves on blur —
one `PATCH /api/listings/{id}` per number, naming that field alone, never the
summary the card is holding (see `main.patch_listing`). **Drafts only**, like
the two controls above it: a live listing's price *is* revisable, but only
through a revise, and a number changed here would leave this app and eBay
disagreeing with nothing on either screen saying so. Repricing a live listing
keeps its own routes — the editor's save, and the dashboard's "Lower prices"
group — both of which push the change to eBay.

**An auction is started, not priced,** and that is a different number rather
than the same one relabelled. "Check market price" answers the Buy It Now
question — the median of comparable listings — and on a plain auction that
answer went into `price`, the field the format does not use. So the one field
an auction needs was the one the market data never reached, and the seller
typed a guess.

`pricing.auction_start` reads the **same eBay comps** from the other end of the
sale, so the recommendation costs no extra call and rides along on every
`/api/price-suggestions` answer as `auction`. Both failure modes it has to
avoid are expensive:

- open **at** the market price and it is a Buy It Now with extra steps — no
  bids, no sale, relist;
- open at a dollar on an item nobody is hunting for and it *sells* for a
  dollar, because a no-reserve auction that draws one bidder ends at the floor.

Which of those an item is in is exactly what the comps already measure. The
comp **count** is the only evidence this app has that bidders will turn up at
all, so it decides how far under the market it is safe to open: past
`DEEP_MARKET` the opener drops well below it, and a thin market opens close to
what comparable items fetch. The account's pricing strategy (Quick Flip /
Median / Long Sale) then says how much of the item's value the seller will put
at risk to attract bidding — the same question it already answers for a Buy It
Now, asked about the other end. The number lands on a charm point *at or below*
where that lands it (`_charm_floor`, not `money.charm_price`: rounding to the
nearest would raise the floor under an auction the seller asked to open below
the market), and never under eBay's $0.99 minimum.

It reaches the seller in two places, both with the measurement it came from
written beside it — a number that can be overruled on purpose is one whose
evidence is on screen:

- **on the card**, behind one press ("Suggest an opening bid"), which applies
  it to the starting bid and, on a format that has one, the market price to the
  Buy It Now. On request rather than on render: every press is a live eBay call
  against an allowance shared by every seller (`main._taxonomy_guard`), and a
  grid of thirty cards that each asked on sight would spend it in one screen;
- **in the editor's price card**, as the top row under "Check market price" —
  and on a plain auction the comp rows below it stop being buttons, because
  there is no `price` field for them to be applied to. They still say what the
  item is worth; they just no longer offer to write it somewhere eBay ignores.
  A live auction gets no row at all: eBay does not revise `StartPrice`, and
  bids may already be against it.

A lookup that failed is still not a market with nothing in it — the card runs
the answer through the same `priceView` split the editor and Shop Mode use, so
"we couldn't check" never arrives as "no comparable listings, try a simpler
title".


## API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/health` | Liveness, the build sha, and the capability flags the UI reads (AI / eBay / taxonomy). Public, and nothing else — the operator detail is on `/api/admin/diagnostics` |
| `GET`  | `/api/ready` | Can this machine do photo work right now: storage, disk, database, object storage. **503** when not. Public; what `health-watch.yml` alerts on |
| `GET`  | `/api/admin/diagnostics` | Every integration's state, the missing variables by name, config warnings, backlogs. Needs `x-admin-token`; fails closed when `ADMIN_TOKEN` is unset |
| `POST` | `/api/upload` | Upload images (multipart) → optimize → `session_id`. Add `pipeline=true` to return as soon as the files are saved and run optimize **and** identify as one background job → `job_id`. `notes=` carries the seller's comma-separated hints, saved with the session so every later re-draft still has them |
| `POST` | `/api/identify/{session_id}` | Vision → listing draft (synchronous; used by Shop Mode). Which model looks at the photos is `config.identify_provider()`'s call — Gemini where `GOOGLE_API_KEY` is set, Claude otherwise |
| `POST` | `/api/identify-async/{session_id}` | The same draft as a polled job → `job_id` |
| `POST` | `/api/bulk/upload` | One photo pile → many drafts, as a job → `job_id`. Takes the same `notes=` hints, which tell the grouping pass how many separate items to expect |
| `GET`  | `/api/bulk/status/{job_id}` | Poll any of the jobs above (phase, per-photo progress, result) |
| `POST` | `/api/refine` | Refine the draft from a prompt |
| `POST` | `/api/save/{session_id}` | Persist manual edits |
| `POST` | `/api/listings/{id}/video` | Attach the listing's video (multipart, one `.mp4`). Streamed to disk, checked against eBay's size/duration/container rules, then pushed to eBay's Media API behind the request. Refuses a second video — eBay allows one |
| `GET`  | `/api/listings/{id}/video` | The listing's video and where eBay's moderation got to, with a sentence for the seller. Asks eBay only about videos eBay has not finished with |
| `DELETE` | `/api/listings/{id}/video/{name}` | Take the video off the listing, the volume and the bucket |
| `POST` | `/api/category-suggestions` | Ranked eBay category IDs for a query (Taxonomy API) |
| `POST` | `/api/price-suggestions` | What comparable items are asking (Browse) or sold for (Marketplace Insights, where approved), as a price to **list** at — and, off the same measurement, where to **open** an auction. `checked` says whether anything got to look, so a failed lookup never reports itself as an empty market |
| `GET`  | `/api/ebay/store-categories` | The seller's OWN eBay Store shelves, flattened with their paths. Answers `store: false` for an account without a Store and `checked: false` when eBay could not be asked — different things, and the picker treats them differently |
| `POST` | `/api/publish` | Publish (draft/live). Add `marketplaces: ["ebay","etsy","depop"]` to fan out; omit for the legacy eBay-only behavior |
| `GET`  | `/api/marketplaces` | Every marketplace + connection state (drives Settings & publish chips) |
| `GET`  | `/api/{marketplace}/connect` · `/callback` | OAuth connect flow (eBay, Etsy, Depop) |
| `POST` | `/api/{marketplace}/end-listing` | End one marketplace's live listing |
| `GET/POST` | `/api/etsy/settings-options` | Etsy shipping-profile / return-policy defaults |
| `GET`  | `/api/listings` | Current user's saved listing history |
| `GET`  | `/api/listings/export.csv` | The **whole store as a spreadsheet**: every listing on the account in every state, with a link to every photo. Streamed and keyset-paged, so a big store costs one page of memory rather than one store; `X-Export-Total` says how many listings there are, so a download that was cut can be told from a complete one |
| `GET`  | `/api/listings/{id}` | Fetch one saved listing (ownership-checked) |
| `POST` | `/api/listings/{id}/relist` | Copy a settled listing into a **new draft** — sale-specific fields cleared, photos copied, the original left untouched |
| `POST` | `/api/listings/merge/preview` | Duplicate drafts merged under a chosen master, worked out but not written: the fields the drafts disagree about, and the blanks a duplicate fills in |
| `POST` | `/api/listings/merge` | Consolidate duplicate drafts into the master — photos combined, `field_choices` applied, sources deleted |
| `GET`  | `/api/insights` | Ranked "what to do next" actions across the user's listings |
| `GET`  | `/api/messages` | Unified buyer inbox: conversations merged across marketplaces, plus `sources` for the marketplace filter. Person-to-person only |
| `GET`  | `/api/messages/{id}` | One conversation's messages, oldest first |
| `POST` | `/api/messages/send` | Reply into a conversation |
| `POST` | `/api/messages/read` | Mark one conversation read |
| `GET`  | `/api/ebay/orders` | Orders awaiting shipment (Fulfillment API) with ship-to, the listing's package and any label bought here; when empty, eBay's 90-day order count, the connected username and the environment, so the empty state can say which kind of empty it is |
| `GET`  | `/api/ebay/orders/for-listing/{id}` | The awaiting order for one of our listing records (a sold notification's "Ship it"), plus the labels bought for it once it has shipped |
| `POST` | `/api/ebay/mark-shipped` | Attach a tracking number to an order (`createShippingFulfillment`) — the retry when eBay refused it at purchase time |
| `GET`  | `/api/easypost/status` | Whether this seller has connected EasyPost (test mode, last four of the key — never the key) |
| `POST` | `/api/easypost/connect` · `/disconnect` | Store the seller's own EasyPost API key after proving it works; forget it |
| `POST` | `/api/easypost/rates` | Live rates for one order's package from the seller's EasyPost account (creates a Shipment; buys nothing) |
| `POST` | `/api/easypost/label` | Buy the chosen rate, record it, post the tracking to eBay. Never buys twice for one order; settles a lost answer against EasyPost first |
| `POST` | `/api/easypost/label/{shipment_id}/refund` | Void an unused label (scoped to the seller's own record of it) |
| `GET`  | `/api/ebay/duplicates` | Live listings that look like the same item listed more than once, minus the pairs the seller has already waved away |
| `POST` | `/api/ebay/duplicates/dismiss` | Stop reminding the seller about the pairs they've looked at. Ends nothing; holds only while each pair stands as they left it |
| `POST` | `/api/ebay/lower-prices` | Lower the named listings' prices by one percentage and push each to eBay |
| `POST` | `/api/listings/enrich` | Fill in the named listings' item specifics from their photos and push each to eBay — returns a `job_id` to poll |
| `POST` | `/api/enrich/{session_id}` | Fill ONE listing's blanks from its own photos — category if missing, the category's item specifics, the maker. The last step of the editor before Publish; fills blanks only, never overwrites |
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
| AI listing draft | 5 | identify (incl. sticker/barcode read) + category + item specifics + tag read + maker check; same per item in a bulk batch (photo grouping bundled) |
| AI refine instruction | 1 | free-form "make it..." edits |
| Autofill item specifics | 2 | the standalone button (bundled free inside a draft) |
| Shop Mode shelf scan | 2 | one video's frames |
| AI photo tool | 1 / photo | background removal, auto-clean, smart crop |

**Packs** (edit `PACKS` in `backend/services/tokens.py`): Starter 50/$5.99 ·
Plus 120/$11.99 · Pro 300/$24.99 · Power 1000/$69.99 — $0.12 down to
$0.07/token as packs grow.

**Why these numbers are profitable.** A full draft runs 3–4 vision calls over
up to 8 photos (the consolidated `IDENTIFY_CHAIN=v2` chain — it was 4–6 before
the tag-read, specifics and maker passes were folded together, and the photos
now ride as ~1092px copies at roughly half the image tokens; the specifics
**coverage pass** below adds one more, over four photos rather than eight); on the default
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
  Marketing (Promoted Listings) and Negotiation (**Send offers** — a private
  discount to the buyers watching a listing; the asking price never moves).
  Negotiation runs on the `sell.inventory` scope the app already asks for, so
  no seller reconnects for it.
- **Etsy** — Etsy Open API v3 (OAuth + PKCE; set `ETSY_CLIENT_ID` +
  `ETSY_REDIRECT_URI`). Listings are created as Etsy drafts, photos uploaded,
  then activated on a live publish. Etsy requires a category (AI Suggest
  built in), who-made/when-made attribution, and a shipping profile
  (defaults per account under Settings). Note: Etsy allows only handmade,
  vintage (20+ years), and craft supplies, and rotates refresh tokens —
  both are handled. First connect stopping on Etsy's own page with *"Only
  the app owner may authorize a seller app"* is app **type**, not config: a
  Seller app is authorizable by the one Etsy account that registered the
  keystring and nobody else. Opening it up is three tiers, not two, and
  Commercial Access cannot be requested straight from a Seller app —
  **Seller** (your shop) → **Personal** (yours plus a handful more, Etsy
  documents 4; deeper review) → **Commercial Access** (unlimited, and only
  on an *approved* Personal app). Reaching that page proves
  `ETSY_CLIENT_ID` and `ETSY_REDIRECT_URI` are registered correctly — a
  wrong one fails before the consent screen. `ETSY_ACCESS_TIER` records
  which tier you're on (`seller` if unset, and an unreadable value reads the
  same, so a typo can't hand Connect Etsy to sellers Etsy will refuse).
  **This app is on `personal`: Etsy approved it 2026-08-31**, so the seats
  are real and `ETSY_OWNER_EMAILS` is now the beta roster rather than a list
  of one — the app logins (not the Etsy ones; they're matched against the
  account record) of the shops you're onboarding. They connect; every other
  seller gets a "Pending approval" card that says which wait they're in,
  instead of being sent to Etsy to be refused. Naming **more sellers than
  Etsy seats** puts the overflow back in front of that refusal, so
  `config_warnings()` counts the roster against the tier's ceiling
  (`ETSY_APP_SEATS` overrides it if Etsy moves it). Setting the tier to
  `commercial` — or the older `ETSY_COMMERCIAL_ACCESS=true` — retires the
  gate the day Etsy grants it. Details in `.env.example`.
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
4. **Brand & UX design** ✅ — Thryft Shop identity: eBay palette, retro-modern,
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
   `R2_PUBLIC_BASE_URL`. Falls back to local disk when unset;
   `/api/admin/diagnostics` shows `objstore_missing` when partially
   configured, and `/api/ready` says `object_storage.configured` without a
   token.
7. **Mobile** ✅ — the web build wrapped in Capacitor for iOS (see
   [`MOBILE.md`](MOBILE.md) and the TestFlight checklist), talking to the
   same `/api/*` endpoints with a bearer token.
8. **Item Identifier** — reverse image search (Google Lens via SerpApi)
   confirms art prints and maker marks; a full cross-checked identify pass
   for every item is still ahead.
9. **Smart List** — *planned.* Finished drafts are posted for the seller at
   the hours buyers are browsing, spaced out over days instead of all at
   once, and the app learns which of the seller's own slots drew the most
   views. The design — data model, scheduler, readiness gate, learning loop,
   tests — is in [`SMART_LIST.md`](SMART_LIST.md).

## Project layout

```
backend/
  main.py            FastAPI app + routes
  config.py          env / settings
  db.py              SQLAlchemy models + every database read and write
  models.py          Pydantic models (Listing, etc.)
  storage.py         per-session filesystem store (the /data volume)
  objstore.py        Cloudflare R2: photo offload, restore, presigned URLs
  auth.py            sessions, bearer tokens, password hashing
  ebay_auth.py       eBay OAuth + business policies / inventory location
  ebay_errors.py     one taxonomy for eBay's refusals, in seller language
  marketplaces/      one provider per marketplace (eBay, Etsy, Depop) behind
                     a shared publish contract, plus the shared state model
  services/
    images.py        photo pipeline (EXIF, upright, local cutout, resize)
    orient.py        the vision pass that turns an item upright
    claude_ai.py     vision identify, refine, specifics fill, maker lookup —
                     and the router that sends identify + the pricing lookup
                     to whichever backend is configured
    google_ai.py     the same identify and the same pricing lookup on Gemini,
                     the second one grounded in Google Search
    listing_prompt.py the prompts, kept testable without the SDK
    ebay_trading.py  Trading API (XML): publish, revise, end, read the store
    listing_sync.py  bi-directional sync: import the store, push edits back
    listing_export.py the store as a CSV: the columns, and the guard that
                     stops a spreadsheet running what it opens
    sync_merge.py    the three-way merge behind the sync
    taxonomy.py      Taxonomy API -> categories, aspects, the Size rules
    metrics.py       views / watchers / offers / bids per listing
    promotions.py    Promoted Listings
    ebay_offers.py   Negotiation API: offers to the buyers already watching
    ebay_orders.py, easypost.py   sold notifications and shipping labels
    ebay_messages.py, messages.py buyer messages and the unified inbox
    errorlog.py      the production error feed (Admin -> Errors, /api/ops)
    tokens.py        the AI-token ledger and Stripe checkout
    jobstore.py      durable job status for long photo/import work
alembic/             schema revisions (shipped; the cutover is a checklist item)
scripts/             one-shot operator tools (see each file's docstring)
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

## Photo pipeline

Per photo, the pass does four things and nothing else: it honours the
camera's EXIF orientation, it turns the item upright when it was photographed
lying sideways or on its head, it takes the background off when the seller
asked (one run of the local rembg model, the matte hardened a little, the item
composited on white), and it sizes the result for eBay -- the longest side to
1600px, never upscaled -- saved as a JPEG with no metadata, so the GPS of the
seller's home never rides along to a listing. The frame the seller composed is
the frame that ships; cropping and fixing a cutout the model got wrong are the
seller's, in the photo studio.

**A photo may hold more than one thing.** The cutout asks whether what the
matte kept is the PRODUCT, and for a long time that meant one solid blob --
which is false for a great deal of what resells. A seller photographing a pair
of shoes, two paintings, earrings, or an item beside its box got a perfect
matte thrown away for having two pieces in it, and the photo came back
untouched with no explanation that fitted. A few compact objects now count as
the product, provided they are few, are nearly all of what was kept, and each
fills its own bounding box. What still refuses is the case the guard was built
for: a photo OF a picture, where the model finds the tree or the boat the
picture depicts and deletes the artwork around it. Those pieces sit in
opposite corners and fill the box between them at 0.10-0.22, where two
canvases side by side fill it at 0.87.

**A hedged matte is repaired, not refused.** The model is confident where
there is contrast and unsure where there is not, so a pale item on a pale
backdrop -- a white shirt on white foamboard, which is what sellers are told
to shoot on -- comes back with its collar label at full strength and the
fabric between somewhere in the middle. Shipped as alpha that is a shirt
composited at a third of its opacity, i.e. a ghost. The pass now makes the
item's INTERIOR opaque before hardening: a pixel inside the item is the item,
whatever the model's confidence, because there is nothing else it could be.
Interior means cells wholly covered by the item, eroded back from the edge,
so a soft boundary stays soft; and it is gated at the same threshold that
separates background from an edge, so the hole through a ring, the gap under
a mug's handle and the backdrop between a pair of boots all stay holes. A
cutout is refused now only when there is no item in the frame to separate.

The item's orientation is the one thing EXIF cannot tell, so a vision model is
asked (`backend/services/orient.py`). The first version of that pass was
written around a shirt laid flat and was wrong about most other things; the
rebuilt one starts from how the item sits in the frame -- standing, hanging,
worn, a garment laid flat, anything else laid flat, or a close-up -- with
readable text winning over everything, and it never turns a flat-lay or a
close-up that carries no text, because those have no wrong way up. A proposed
turn ships only after a second, different question: the photo at all four
quarter-turns, and the model must pick the proposal. Best-effort and bounded
(`ORIENT_BUDGET_SECONDS`); anything it cannot answer for stays as shot, a
wrong turn is one tap of the rotate button on the tile or the card, and
Restore original goes back to the photo as shot. `AUTO_ORIENT=off` disables
it.

### A picture is never cut into: the artwork path

A Marcia Alpert gouache, "Baby in a Basket", came back from the pass with the
**baby cut out of the painting** — lifted off the teal water and the patchwork
quilt she painted it on, floating on white. Another photo kept the turtle and
the signature and deleted the rest. The item the listing was for had been
erased from the photos of it.

Every guard above passed, and each was right to. They read the **alpha** and
ask whether what survived looks like a product: one connected piece, filling
its own bounding box, solid through the middle. A baby lifted out of a
painting is all three — arithmetically indistinguishable from a perfect cutout
of a figurine. The fact that separates them is not in the matte at all: it is
that the item **is itself an image**, and a salient-object model handed a
picture answers the only question it knows, *which part of this is the
subject*. For a painting there is no honest answer to that question.

So a picture does not go to the model. `services/orient` — the only pass that
looks at a photo before the cutout runs, and one already being made and paid
for — is asked one more question per photo: is the item a painting, print,
poster, drawing or photograph? The test it is given is whether the thing's own
front surface *is* an image, which is what keeps a mug with a boat printed on
it a mug. A yes routes the photo to `services/artwork` and `images.art_cutout`
instead, where the rule is geometric:

> **Find the outer border. Keep everything inside it, whole. Never ask what is
> interesting within it.**

That makes the guarantee structural rather than statistical: `artwork.mask`
returns a **filled rectangle**, so no code path on the art side can remove a
pixel from the middle of a painting.

Finding the border is two steps, both Pillow-only. A content mask (colour
distance from the surround, ORed with local contrast) locates the picture and
proves it is one — a picture **fills its own bounding box**, which is what
still refuses the tree-at-one-edge-and-boat-at-the-other shape. Then each side
is scanned **inward from the edge of the photo** to the first line that is not
surround. Inward, because a white-mounted print leaves eighty pixels of blank
paper between the printed area and the sheet's own edge, and a scan starting
at the artwork has nothing in that gap to grow along; one starting at the wall
crosses it without ever standing on it.

**And when there is no clear border, nothing happens.** A picture bleeding off
the frame, a shape that is not a rectangle, something too small to be the
piece — each returns None, and None means *keep the photo exactly as shot*,
reported in `bg_error` so the seller is told and the charge comes back. It
never means "cut to what the model kept", because that is the bug. The error
directions are not close: a cutout wrongly refused costs one photo an opt-in
feature, and a cutout wrongly shipped destroys the item the listing is for —
which is also why the prompt tells the model to answer **true** when it is
torn about whether something is a picture.

**A model may say where a picture is. It may never say what to keep inside
one.** Before giving up, the scan takes a second opinion: a segmentation matte
is fitted to its best rectangle, and if it *is* a rectangle at some angle
(`artwork.quad_from_alpha`, floor 0.9 — a round subject scores 0.785 however
it is turned) its four corners become the border and are filled solid. Only
the outer shape is ever read, so the guarantee above is untouched: what ships
is still a solid quad, and a baby lifted out of a painting is refused because
it is not a rectangle, not because of which model drew it.

That second look was written for a paid engine and, for a while, only a paid
engine could answer it — which meant it never ran, because nothing configures
one (`BG_ENGINE` is commented out in fly.toml with no key beside it). The
local model answers it now (`ART_LOCAL_BORDER=off` restores the old
behaviour), and it costs one inference on exactly the photos that were
otherwise getting nothing. Before this, a seller's grid had shirts cut out on
white beside framed prints and printed trays still sitting on the floor they
were shot on, and the reason was "no paid API key".

**A square object that is not a picture gets the same geometry.** A tray, a
sign, a plaque, a boxed set, a record sleeve: the screen is right not to call
these art — a tray with a map on it is a tray — so they go to the model like
any other object, and the model drops them or keeps only the picture printed
on their face. When nothing it returns survives the guards, `artwork.border()`
is asked last, and the photo is cut to the rectangle if there is one
(`bg_engine: "border"`). Only after the model has declined, so no photo that
gets a cutout today changes, and `border()` answers None for anything that is
not a rectangle, so a garment or a close-up is still kept as shot. The studio's
**Remove background** button does the same thing, which matters most there:
that is the button a seller presses *after* a batch left the photo as shot.

One limit, stated plainly: a print with a blank white mount, on a white
surface, under flat light with no shadow, has no detectable outer edge — there
is nothing in the photo that distinguishes the paper from the table. The
border then lands on the printed area and trims blank mount. The painted or
printed image itself is never cut into, which is the property the tests hold.

Production runs the `isnet-general-use` model (`REMBG_MODEL` in fly.toml,
baked into the image; needs the 4GB VM). `u2netp` is the 4MB fallback for a
smaller box. Both are free per photo; there is no remote engine.

**One inference at a time, but two different deadlines.** The model is
serialized (two runs at once double peak memory and OOM the box), so callers
queue for a slot -- and how long they should queue depends on who is waiting.
Someone watching the photo studio's spinner wants a fast "busy, try again"
(`REMBG_WAIT_SECONDS`, default 25). A photo in a batch has nobody to tell and
gets no retry, so it queues (`REMBG_BATCH_WAIT_SECONDS`, default 300) and, if
even that runs out, is saved as shot with the reason rather than lost.

**Photos one at a time, drafts several.** The two halves of a batch have
opposite shapes, so they are run in opposite ways. Background removal is
inference on this machine, single-flight and memory-bound -- two at once
double peak memory and OOM the box -- so the photo pass stays strictly serial.
Drafting is five to nine Anthropic calls and a handful of eBay lookups per
item, nearly all of it spent waiting on somebody else's server, so items are
drafted `BULK_DRAFT_WORKERS` at a time (default 3, in fly.toml; 1 is the off
switch). The ceiling is the machine rather than any API: each worker holds a
listing's photos as base64 for its vision calls and crops the full-size
originals for the tag close-ups, beside the 176MB cutout model.

Every guarantee the serial loop gave still holds, and they are what the change
actually cost. An item is charged for once and refunded if its AI fails. An
item that fails takes only itself down. The queue stays in the order the
seller shot the pile, however the drafts land -- each item says which GROUP it
is rather than relying on its position, which is also what lets a restart tell
apart the several items that were in flight. And because a prompt cache entry
is only readable once the request that wrote it starts answering, a batch
warms the identify prompt once before the fan-out; without that, three workers
starting together would each miss it and each pay to write the same several
thousand tokens.

**A batch survives the machine it started on.** Bulk does every photo's
background removal up front and only then starts drafting, so until the last
cutout lands nothing durable exists. Optimized outputs are renamed into place
-- so a file existing means it is complete -- and the photo pass skips any it
already has. On boot, a batch that was interrupted *before it drafted
anything* is re-registered under its own job id and run again, so a browser
still polling simply carries on. A batch interrupted while `identifying`
carries on too, from its written-down plan: every group with no draft is
drafted, and the items that were charged for and never delivered are finished
in the sessions that already hold their photos rather than charged again. The
gap several workers leave is not a suffix -- a batch can die with group 4 in
flight while 5 and 6 are saved -- so the plan records each item's group, and a
mirror written before that is still read the old way, by position, which is
what makes it safe for a deploy to land mid-batch. `BULK_MAX_RESUMES` (default 2) stops a batch
that keeps dying from taking the machine with it.

**A batch is reviewed in the drafts grid, not in a grid of its own.** The
batch screen used to draw its own cards -- the title and the price as text
boxes, its own columns, its own Publish / Delete / Merge buttons, its own
selection -- beside the Sell screen's drafts grid, which draws the same
listings as photo tiles with the AI's confidence, the review count and the
price badge. Two grids of the same drafts, and the trip a seller actually
takes goes through both: open one item from a batch, save it, and the app
handed back the *other* grid. So `BulkQueue` renders `DraftsStrip` scoped to
the batch's own session ids, and what stays on the batch screen is what
belongs to the run rather than to a draft -- the progress bar and stop
switch, the duplicate and blocked-count notes, the receipt of what went live,
and the items the AI could not identify at all (those have no draft for the
grid to show). The cards read the SAVED drafts rather than the job's own copy
of them, so an edit made anywhere shows up on the batch card; a clean draft
save closes the editor back onto the screen it was opened from; and
`Publish all` is the one thing the grid grew for this screen.

## Listing video

A listing can carry a **video** — one of them, because that is what eBay
allows. Upload only: pick an `.mp4`, it goes up, eBay reviews it. There is no
trimming, no thumbnail picking and no re-encode, deliberately — eBay builds
its own renditions (240p/360p/480p/720p) from whatever it is handed, so a pass
here would cost the seller quality and produce something eBay throws away.

**A video is not a photo, and almost nothing about the photo pipeline
applies.** Photos are *pulled*: the publish hands eBay a `<PictureURL>` and
eBay fetches it from R2 or `/media`. There is no `<VideoURL>`. A video is
*pushed* through eBay's Media API — `apim.ebay.com/commerce/media/v1_beta`,
which is the one eBay API that does not live on `api.ebay.com` — and the
listing then names it by the id eBay minted:

```
POST {media}/video                   -> 201, Location: …/video/{id}
POST {media}/video/{id}/upload       -> 200   (application/octet-stream)
GET  {media}/video/{id}              -> PENDING_UPLOAD | PROCESSING
                                        | LIVE | BLOCKED | PROCESSING_FAILED
<Item><VideoDetails><VideoID>{id}</VideoID></VideoDetails></Item>
```

It runs on the `sell.inventory` scope, which every connected seller already
granted — **nobody has to reconnect**.

Three consequences, and they are what the code is shaped around:

- **The upload does not wait for eBay.** The file is streamed to disk a
  megabyte at a time (150MB awaited into a `bytes` on a 4GB box that is also
  holding a 176MB cutout model is an OOM waiting for two sellers at once), the
  listing gains the video, and the request returns. The push to eBay and the
  R2 offload run behind it — the same rule "Add photos" follows for its
  optimize pass.
- **eBay moderates it, in hours to 48 of them.** Nothing waits for `LIVE`: a
  publish goes out with a `PROCESSING` video exactly as eBay intends, and the
  status is polled and shown on the card. A seller who publishes and sees no
  video on their listing has done nothing wrong, and the card is the only
  place that can say so before they start again.
- **A rejection lands days after the seller stopped looking.** So the three
  things a local read can be *sure* of are checked before the file is
  accepted: the size, the duration, and the container — read from the file's
  own `ftyp` brand rather than its extension, because a `.mov` renamed to
  `.mp4` is the usual way to fail eBay's format rule. Anything the reader
  cannot parse is left to eBay: refusing a video eBay would have taken is the
  worse of the two errors.

eBay's limits, each a named constant (`models.MAX_VIDEOS`,
`services/ebay_video`): **1 per listing**, **150MB**, **MP4 (MPEG-4 Part 10 /
AVC)**, about **a minute**, and never on a multi-variation listing. The
plumbing either side carries a *list* — the Trading schema repeats
`<VideoID>` — so the day eBay raises the ceiling is a one-line change. Past
the limit eBay does not refuse the extras, it **ignores** them, which is why
the app refuses them itself: an upload that succeeds onto a listing with no
video is the worst way to learn the rule.

Storage mirrors the photo path: `sessions/<id>/video/` on the volume and under
the same R2 prefix, so the erasure and the orphan sweep already reach it, and
the reclaim pass frees the local copy once the bucket has it (one video is a
sixth of the 1GB volume — a pass that walked only `optimized/` could sweep
every photo on the box and still leave it full). A publish re-checks that the
video reached eBay and uploads it if it did not — for a video added before
eBay was connected, or an upload that failed — and **never fails the publish
over it**: a listing with no video sells, a listing that will not publish is
the seller's afternoon.

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

That match is deliberately wider than the account check the rest of the sync
uses (`listing_sync.matchable` vs `owns`). `owns` refuses whatever it cannot
prove — a record stamped with eBay's immutable account id when the connected
account could not read its own identity, and the `previous account` sentinel a
reconnect stamps — and both of those sit on listings the seller made here. Left
out of the match they were imported again as `ebay-<itemId>` on every sync, and
the seller watched a listing they created turn into an eBay import (the origin
badge is the record's id, so a second row IS a different badge). An item id
names one listing on one account and these ids come out of the connected
account's own selling lists, so the id settles what the label cannot; the write
then stamps the account it came back from, which repairs the record and lets the
stale-mirror cleanup drop the duplicate on the same pass. A **provable**
disagreement — a record stamped with one immutable id, a caller holding another
— is still refused. The per-record status sweep stays strict either way: it
calls `GetItem`, which answers for any seller's item, so nothing there may lean
on an id the seller's own store never returned.

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
  `SKU` with `InventoryTrackingMethod=SKU`, which makes the listing findable by
  `GetItem` afterwards. eBay refuses a second create under the same `UUID` even
  when the two attempts never meet in one process, answering error 488 with the
  item id the first attempt produced, and the app adopts that listing rather
  than posting a twin. A relist keys on the item it replaces, so an intentional
  relist still goes through while a retried one doesn't double-list.
  (This previously sent `InventoryTrackingNumber`, which is not an element of
  eBay's `ItemType` — it was ignored, and the `GetItem` lookup built on it could
  never succeed. See <https://developer.ebay.com/support/kb-article?KBid=1462>.)

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

**Dismiss all**, because often the honest answer is that nothing needs doing.
The scan re-runs on every Dashboard load and there is no press that finishes it
the way *Enrich all* finishes a suggestion, so without this a seller who has
already thought about a pair is asked about it again on every visit. The danger
in remembering a dismissal is the opposite one — a silent "never show me this
again" that hides a real duplicate for the life of the account — so the
dismissal is pinned to the pair **as the seller judged it**: a short digest of
which eBay items are in the group, what each one costs, and how each one sells
(`duplicates.fingerprint`). Edit a price, switch one to an auction, relist one,
or let a third turn up under the same title, and the digest moves and the group
is back. Everything the *sync* moves on its own — watch counts, view urls,
`updated_at`, whether a row is the app's or the store sweep's mirror — is
deliberately left out, or eBay reporting a new watcher would re-raise every
settled pair within a day.

The press goes through a confirm that says both of those things, and sends the
digests the card had **on screen**, so a pair that appeared between the load and
the press is never waved away unseen. The card also says how many it is holding
back, because a scan that quietly drops what you dismissed reads exactly like a
scan that stopped finding anything. The ledger lives in a reserved key on the
user's `prefs` JSON, unreachable from `POST /api/prefs` (which stores only its
own whitelist), and digests for pairs that no longer exist are dropped as new
ones are written, so it tracks the account rather than growing for the life of
it.

## Suggested actions (and applying them in bulk)

The Dashboard's **Suggested actions** card is `services/recommender.py` over the
signals the app already has — listing status, age, price, photo count, plus eBay
views/watchers when the scope is granted. Rules turn a store into a short ranked
list: offer a discount to the buyers watching an item, finish a draft, drop a
stale price, add photos, fill in specifics. An ended listing earns nothing:
relisting is done by hand, and the ended bucket picks up sold items.
Suggestions are grouped by kind and collapsed ("Lower prices · 12"), keeping
one strongest action per listing so the list spans the portfolio instead of
piling onto one item — with the two kinds that both mean "this listing's
details aren't finished" grouped as one row (see **Finish details** below).

A group whose edit makes sense across every listing in it gets a **bulk action**
in its header, because repeating one edit twelve times by hand is the whole
problem:

- **Send offers → "Send offers…"** opens an amount field (*offer everyone
  watching these listings X % off*) and sends eBay a private offer per listing
  through the **Negotiation API** (`services/ebay_offers.py`). This is the one
  group ranked above a price drop, and the reason is that no price moves: the
  discount reaches only the buyers already watching the item, and the rest of
  eBay goes on seeing the listing at what it has always cost. A listing earns
  the suggestion on its watch count, with three gates that each match
  something eBay refuses — an auction has bids rather than a Buy It Now price
  to discount, a listing with a buyer's Best Offer already waiting refuses a
  seller offer (and the seller has an answer to give there instead), and a
  listing nobody is watching has nobody to offer it to.

  **eBay decides who is eligible, not this app.** One `find_eligible_items`
  sweep per run says which listings currently have interested buyers, so the
  send visits only those instead of spending a call per listing to be told
  there is nobody there. A sweep that cannot be *read*
  is not a store with no interested buyers: the run goes ahead and lets eBay
  refuse what it will not carry, because reporting every listing as skipped
  would look exactly like nobody is watching anything.

  **The group clears once the offer is out** (`Listing.offer_sent_at`, and
  `recommender.OFFER_QUIET_DAYS`) — the same lesson as the price stamp below,
  applied before it could be reported a third time: sending an offer does not
  move the watch count that suggested it, so without the stamp the group would
  come straight back with the same listings and the same count. eBay agrees
  from the other side, refusing a second seller offer while the first is live.
  The quiet period outlasts eBay's own offer window (4 days on `EBAY_US` and
  `EBAY_GB`, 2 on most other sites), and after it the nudge returns on its own
  if the discount did not work.

  Three details of eBay's contract are pinned by
  `test_an_offer_reaches_the_buyers_watching.py`, because each fails the
  same way from the outside — a refusal on one listing, mid-run, with nothing
  on screen to say why. eBay takes exactly **one listing per call**, so a bulk
  send is a loop (`BULK_OFFER_CAP`, remainder `deferred`); the offer
  **duration is site-specific** and eBay refuses any other value, so none is
  sent and each marketplace applies its own; and **counter-offers** are not in
  this release of eBay's API, so `allowCounterOffer` is sent explicitly false
  rather than left to a default. The floor of 5% off is eBay's and is enforced
  on the input; the 50% ceiling is this app's, and lower than the price drop's
  75% because a buyer takes one of these with a single tap.

  A single row sends its own offer rather than opening the listing. Every
  other group's rows are a way into the editor, because that is where another
  photo or a new price gets made — there is nothing in the editor that sends
  an offer, so that row would be a button that leads nowhere.
- **Lower prices → "Lower all…"** opens an amount field (*lower every price in
  this group by X %*) with its own submit. Each listing is repriced and pushed to
  eBay through the same revise path a single edit uses.

  **The group clears once the cut is made** (`Listing.price_lowered_at`), and
  until it did, this was the same broken-looking button as "Enrich all" below.
  Both rules that put a listing in this group are computed from signals a price
  cut does not move: the age heuristic counts from `created_at`, which never
  changes, and the traffic one reads eBay's **view** count, which is cumulative
  for the life of the listing — the thirty views that earned *"buyers are
  looking; the price may be high"* are still thirty views the second after the
  price comes down, and stay so for good. So the seller pressed "Lower all…",
  sat through a dozen serial eBay revises, was told *"Lowered 12 prices by
  10%"*, and the group came back in the same slot with the same twelve
  listings and the same count. Reported, reasonably, as the button not
  working.

  The stamp is what ends it. A cut buys `recommender.PRICE_QUIET_DAYS` (the
  same three weeks a listing gets before it is called stale in the first
  place) before either rule may ask again, and after that the age rule runs
  its clock **from the last cut** rather than from the listing's birthday —
  *"still here 24 days after the last price drop"*, which is what the rule was
  always about. Every way a price can come down stamps it: the bulk button,
  the card's quick edit, an editor save, and a markdown the seller made in
  Seller Hub or the eBay app, which reaches the record through the sync.
  A relist starts without one.

  It is **derived, not sent** (`recommender.price_drop_stamp`): every write
  works it out from the price already stored, so the payload's copy is never
  read — a second tab cannot blank it, and a body cannot mint one to silence
  advice a listing has earned. That is also why it is deliberately *not* in
  `SERVER_OWNED_FIELDS`, whose rule is that the **stored** value wins: under
  that rule the stamp could never move forward on the one write entitled to
  move it.
- **Finish details → "Enrich all"** fills every listing on the list in one
  pass: eBay's required and recommended item specifics for that listing's
  category, read off its own photos (the same enrichment a fresh AI draft
  gets, `_enrich_listing`), merged in **without** overwriting anything the
  seller wrote, then pushed to the live listing. Notes in `missing_info` that
  the fill actually answered are dropped; a note nothing filled is kept, and
  that listing is reported as one that still needs a human. Because a vision
  pass per listing takes minutes, this one runs as a **background job**
  (`POST /api/listings/finish-all` → `job_id`, polled on
  `/api/bulk/status/{id}`), one per account at a time. It spends AI tokens per
  listing, so the button confirms the count and the cost first, and a listing
  it can't reach (no category, photos gone, eBay not connected) is skipped
  **before** it is charged for.

  **"All" means all of them.** It used to mean "up to `BULK_ENRICH_CAP` of the
  rows this screen happens to be holding": the client named the ids and
  `/api/insights` ships at most 50 per type, so a list of 70 was three presses
  — and in an environment where that cap was set to **1**, the progress line
  read *"1 of 1 · 69 more after this run"*, which is a button that does one
  listing per tap while calling itself Enrich all. The press sends no ids and
  has no cap now: the server works the set out from the same ranking the
  screen is rendered from (`_finish_all_set`), so the number on the badge is
  the number that runs. `POST /api/listings/enrich` is still there — ids in,
  capped, deferring the rest — for a caller that genuinely means a selection.
  Nothing in the app does.

  **It is one group, not two.** "Fill in details" and "Check details" are two
  rec types (`specifics` is a fill the AI can make; `verify` is a note only a
  person can settle) and they rendered as two stacked groups with two counts
  and a button on one of them. The seller, looking at *"Fill in details · 70"*
  over *"Check details · 149"*, asked for one. The split is real to the engine
  and is not a decision anyone has to make at the top of a dashboard: both say
  *this listing's details aren't finished*, and the press that finishes them
  has always covered both. So the two collapse into **Finish details**, one
  badge adding both halves up, one button — and the rows behind the chevron,
  each keeping its own verb, for a seller who would rather work through them
  one at a time. The section header carries nothing: a second copy of the
  button, naming the same number as the group beneath it, is how a seller
  comes to distrust both.

  **What decides the group** is item specifics, never the free-text
  `missing_info` notes beside them — a note is evidence the fill has *already*
  failed to answer something, so building the group from notes made the button
  a permanent no-op. Two counts answer that question at different prices, and
  a third fact ends it:

  - `recommender.filled_specifics` — the cheap proxy: how many specifics the
    listing carries a value for. Never wrong in the direction that matters (a
    listing with nothing filled is always one the fill can help), and blind in
    one: Material, Type and Brand filled clears it while *Subject*, *Era*,
    *Occasion*, *Packaging* and *Character* sit blank.
  - `taxonomy.fillable_blanks` — the truth: how many of the aspects eBay
    publishes for **this listing's category** it holds no value for, counted
    per listing in `/api/insights` (`_blank_specifics_by_id`). It needs eBay's
    aspect list, so it is budgeted: cached six hours and read for free, with at
    most a dozen live Taxonomy lookups per dashboard load (that API runs on one
    allowance shared by every seller of the app), biggest categories first. Past
    the budget the proxy above stands.
  - `Listing.enriched_at` — set whenever the fill actually **ran**, including
    the run that added nothing. Neither count can end the group on its own: a
    listing whose photos genuinely cannot answer its category has blank
    specifics before the fill and blank specifics after it, so it sat there
    forever and was charged for on every press. What is left for the seller
    afterwards is to *look*, which is the **Check details** suggestion instead.

  **Check details waits a day** (`recommender.VERIFY_QUIET_DAYS`). It used not
  to, and that turned a working button into a broken-looking one: the seller
  pressed "Enrich all" on twelve listings, waited several minutes while the AI
  read their photos and pushed the new specifics to eBay, and the group they
  had just cleared was replaced *in the same slot* by "Check details · 12" —
  the same twelve listings, still flagged, and (back when it was its own
  group) with no button on it at all, just a list to open one at a time. From
  outside, that is
  indistinguishable from the button having done nothing, and it was reported
  as exactly that. The notes behind it are real, but they are by construction
  the things the fill has just declined to invent, so they are not a chore to
  hand back in the same minute. After the quiet period they return unchanged.

Photos, finish and relist deliberately have none: photos need a human holding
the item, and the last two create listings, which isn't something to put behind
a single button. The rules bulk runs follow — `services/bulk_actions.py`:

- **Scope is never implicit.** The client sends the group's own listing ids, so a
  group of twelve can't turn into the whole store.
- **A listing that can't take the change is skipped with a reason**, not failed —
  a group computed a while ago will contain items that have since sold or ended.
- **One listing's failure never stops the run**, and the response reports per
  listing, so the seller sees "lowered 11 · 1 skipped" rather than a bare OK.
- **A run that names ids is bounded** (`BULK_PRICE_CAP`, default 40;
  `BULK_OFFER_CAP`, default 40; `BULK_ENRICH_CAP`, default 25) because each
  listing is its own serial eBay call — a revise, or, for an offer, the one
  listing eBay's Negotiation API takes per request; the remainder comes back
  as `deferred` for another pass instead of the request outliving the
  gateway. The press that finishes the details list
  names none, so it has no remainder to defer — it is a job from the first
  moment, and the client polls it rather than holding a request open.

Every row also carries a **dismiss** (×). The engine rebuilds this list from
scratch on every load, so advice the seller has already considered and decided
against otherwise comes back for good — and a to-do list that will not shrink
stops being read. A dismissal is per listing **and** per suggestion kind, kept
in the browser (`lib/dismissedRecs.js`, bounded so it can't grow without
limit), and undone in one tap from **Restore N dismissed** on the section
header — which stays on screen even when every suggestion has been dismissed,
so the X is never a one-way door.

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

- It files under **Inactive**, the archive tab, and is hidden from **Active**
  and **All**, where a seller looks for things they can still act on. (Ended
  listings are hidden from those two now as well — see below.)
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

## An ended listing does not pile up

A sale is history worth keeping. A listing that finished **without** selling
is worth keeping only for as long as the seller might relist it — and one
that was never theirs to begin with is not worth keeping at all.

It used to be kept for ever, whichever it was. Every ending — the seller
pressing End, a listing expiring on eBay, and eBay's whole unsold list
mirrored in on each store import — became a record with status `ended`, filed
under the archive tab. The result was a pile: one card per listing that ever
finished, sitting in **All** beside the live ones, and blank, because eBay
stops serving the photos of an item that ended a few weeks ago and an imported
listing has no other copy of them. The seller's report was three words:
*these should be removed automatically*.

So an ending now settles the record, on a clock that depends on whose work is
in it (`listing_sync.settle_ended`):

| The record | On ending |
| --- | --- |
| A **mirror** the store sync made (`ebay-<item>`, no photos added here) | Removed at once, photos and all. Nothing in it is not still on eBay. |
| A listing **this app created** (or a mirror the seller added photos to) | Kept as `ended`, stamped `ended_at`, and removed by the sweep once `EBAY_ENDED_GRACE_DAYS` (default **30**) have passed. |
| A **sale** | Archived under Inactive for good, exactly as before. |

Every door an ending can come through agrees on that:

- `POST /api/ebay/end-listing` (and the generic `POST /api/{marketplace}/end-listing`,
  once nothing is live anywhere) settles the record and answers `removed`
  — true when the row went — so the client knows whether to drop the card or
  file it under Inactive.
- The status sweep (`listing_sync.refresh_statuses`, reached on every sync via
  the cheap finished-list pass) settles a record eBay reports as ended.
- The store import (`listing_sync.import_active`) no longer reads eBay's
  unsold list at all — importing an ended listing so a later sweep can delete
  it is work in a circle — and sweeps the ended records it passes.
- `listing_sync.clear_ended`, run by `POST /api/ebay/sync-listings`, is the
  sweep: it removes ended mirrors and any of the seller's own past the grace
  period, including the backlog from before this rule existed. It reads ended
  rows only, so a store with none pays one empty query and no eBay calls.

An ended listing is also hidden from **Active** and **All** while it is here
(`ARCHIVED_STATUSES`), so a finished card never sits among the ones still
running — that was the report — and the dashboard's *Recent listings* strip
drops it for the same reason.

### Why the removal has to be sure of itself

It takes the **photos** with it: the row, the working copies on the volume,
and the R2 objects they were offloaded to (`listing_sync.drop_ended`). So
every caller must have a *definitive* ending — eBay's own answer for that item,
or an `EndItem` this app just made. A status probe that could not tell answers
`None` and changes nothing, so a rate limit or an API blip can never remove a
listing that is still live.

`ended_at` is server-owned (`SERVER_OWNED_FIELDS`) for a sharper reason than
most of that list: a client that could set it could backdate a listing into
being deleted on the next sweep, or forward-date one to keep it for ever. It
is written once, at the moment the app files the listing as ended, and never
moved by a later re-file. A record that predates the field falls back to its
`updated_at`; one with no readable date at all is **kept**, because "we could
not tell how old this is" is not a reason to delete somebody's photos.

The grace period is published on `/api/health` as `ended_grace_days`, so the
End dialog promises the number the sweep actually measures against rather than
a month hardcoded in the client.

### Two things deliberately not settled

- **A sale.** Ending can discover that the item already sold; that is archived
  with the same notification and storage reclaim as any other sale.
- **A record that was never on eBay.** `not_live` with no status means there is
  no item id — nothing there can have ended — so it goes back to being a
  **draft**, with its photos. Removing it would destroy a seller's work over a
  mis-press on a card that should not have offered End in the first place.

## Sold notifications & shipping labels

When a sync notices a listing flipped to **sold**, the seller gets an in-app
notification (the bell in the top bar, polled every minute) with a one-tap
jump into the shipping dialog. Notifications are deduplicated in the database
(one per sale, no matter how many sync paths spot it), and backfilling an
existing store's historical sales stays silent.

The shipping dialog (also reachable via **Ship orders** on the Listings page)
reads the orders still awaiting shipment through the Fulfillment API — buyer
address included — pre-fills the package weight/dims from the matching
listing, and buys the label through **EasyPost**:

- **The seller's own EasyPost account.** Each seller pastes their EasyPost
  API key once under Settings → *EasyPost shipping labels*. The key is proved
  to work with one EasyPost read before it is stored, held Fernet-encrypted in
  `marketplace_accounts` like every other marketplace credential, erased with
  the account, and only ever shown back as its last four characters. Postage
  is billed to that account's wallet. There is deliberately **no app-wide
  EasyPost key** and no server setting: the operator never pays for a
  seller's postage.
- **Rates → buy → print.** `POST /api/easypost/rates` creates a Shipment on
  the seller's account (free; nothing is bought) and returns the rates
  cheapest first; `POST /api/easypost/label` buys the chosen one and then
  posts the tracking number to the eBay order via `createShippingFulfillment`,
  which flips it to shipped and emails the buyer. The label PDF opens from
  EasyPost's own URL, which works identically on the web and inside the iOS
  shell (a same-origin download is exactly what the native webview cannot
  authenticate). A test key (`EZTK…`) buys free sample labels; a production
  key (`EZAK…`) buys real postage.
- **Every purchase is on record** (`shipping_labels`). The row is written
  *before* the money moves, so a lost answer from EasyPost leaves a row that
  says "we may have paid for this" and the next open of the order settles it
  against EasyPost instead of buying again; an order that already has a label
  opens on that label; and "Ship it" on a notification for an order that has
  already gone shows the tracking and the PDF rather than a shrug. The same
  record scopes a shipment id to its owner — a label carries the buyer's name
  and address, so another seller's id is a 404 here, never a lookup at
  EasyPost.
- **eBay refusing the tracking never hides the label.** A purchase that
  succeeds but whose tracking eBay rejects comes back as the label with
  `ebay_marked: false` and the reason; the dialog offers **Retry marking
  shipped** (`POST /api/ebay/mark-shipped`), and says to check the order on
  eBay first when the refusal was itself a lost answer.
- **Void** (`POST /api/easypost/label/{shipment_id}/refund`) asks the carrier
  to refund an unused label. EasyPost answers "submitted" and settles it
  asynchronously; eBay has no edit-tracking call, so a voided-then-rebought
  label leaves the old number on the order until it is changed in Seller Hub,
  and the dialog says so.

**An empty order list explains itself.** "No orders are waiting to ship" used
to be the whole answer whether every order in eBay's 90-day window was already
shipped, a different eBay account than the one that sold was connected, or the
server was on eBay's sandbox (which has no real orders; `EBAY_ENV` defaults to
`sandbox`). An empty pile now costs one extra read — eBay's own count of every
order in the window — and the dialog names the account, the count, and shouts
if the server is on sandbox. A full pile costs nothing extra.

Reading orders and posting tracking needs the `sell.fulfillment` OAuth scope;
sellers who connected eBay before it was added reconnect once to grant it
(same as every scope addition).

**Trying it with a test key.** Connect an EasyPost *test* key in Settings,
open Ship orders, pick an order, Get rates, Buy the cheapest. The PDF is a
sample and the tracking number is fake, so verify against a sandbox eBay
order — or, on a real order, void immediately rather than let a fake number
reach the buyer. Swap in the production key for the first real label.

## Buyer messages (the unified inbox)

The inbox icon in the top bar is for **messages from people** — a buyer asking
whether the lens fits their camera. It is deliberately not the bell next to it,
which carries the app's own "your item sold" alerts. Mixing the two is how a
seller learns to ignore both, so they stay separate surfaces with separate
badges (blue for a conversation, red for something needing action).

**Marketplace system mail never appears here.** eBay's Message API splits a
seller's mail into `FROM_EBAY` (order notices, policy mail, marketing) and
`FROM_MEMBERS`, so every read asks for `FROM_MEMBERS` and the exclusion happens
at the source rather than by guessing at senders. The service filters the
result on the same field a second time, because the promise shouldn't depend on
eBay honouring a query parameter forever.

One inbox, many marketplaces. `backend/marketplaces/messaging.py` holds the
contract — five methods a provider implements, all or nothing — and the
namespaced conversation id (`ebay:1234`) that routes a click back to the
marketplace that owns the thread. `backend/services/messages.py` fans out,
merges by recency and reports each marketplace in `sources`, so one being down
never blanks the others and the UI's filter can say "Etsy · soon" honestly.
Adding a marketplace is five methods on its provider; nothing else changes.

Clicking a conversation opens the full **Messages** screen: conversations on
the left, the thread on the right, one column on mobile. Replies send through
the marketplace, with the sent bubble appearing immediately and the server's
version replacing it — a failed send marks the bubble rather than discarding
what was typed.

Nothing is stored locally. The marketplace owns these messages, they change in
its own app constantly, and mirroring them would put buyer PII into this app's
delete-my-account obligations for what is only a cache. A 60-second per-user
TTL means several open tabs cost one upstream call.

**Turning it on.** The Message API needs the `commerce.message` OAuth scope,
which is limited-release: eBay approves it per keyset, and requesting it
unapproved fails the *whole* consent screen — nobody could connect eBay, and
publishing would stop with it. So it is opt-in:
set `EBAY_MESSAGING_ENABLED=1` once eBay has approved the app, and connected
sellers reconnect once to grant it. Until then the icon simply isn't there.
Flipping the flag can't disturb existing connections — the refresh grant
deliberately omits `scope` — so rolling back is an env change, not a deploy.

## Taking the whole store with you (CSV export)

**Export CSV**, on the Sell screen beside *Sync with eBay*, downloads every
listing on the account as a spreadsheet. It is the answer to "it's my
inventory, let me have it": a backup, an insurance schedule, the file an
accountant asks for, and the thing a seller leaves with.

**Every listing, not the tab you are looking at.** The button sits above a tab
strip that filters, and the file deliberately ignores it — drafts, live, sold,
ended and Shop Mode finds alike. It ignores the *page* too: the grid caps what
it downloads because it renders full JSON records on a phone, and the export
walks the store with the same keyset cursor the grid pages with instead of
mirroring that cap. A file of the first 200 listings looks exactly like a
complete one, which is why that distinction is a test rather than a comment.

**A link to every photo, in one column.** The photos are the part of a listing
this app made, and a row naming an item with no way to see it is a row about
nothing. Listings created here store filenames; what goes in the file is the
public URL that serves each one — the *same* URL eBay is handed at publish
(`services/ebay._image_urls` builds it the same way), so a link in the
spreadsheet and a link on the live listing can be checked against each other.
Listings imported from eBay have no local files and carry eBay's own absolute
URLs; both kinds land in `image_urls`, pipe-separated, with `image_count`
beside them.

**The record as recorded.** Money keeps the currency it was stored in and the
currency is a column. `price` stays the *asking* price after a sale with
`sold_price` beside it rather than folded into it — copying one into the other
would overstate the take on every accepted offer and auction close in the file.
An empty cell means the record is empty, never "we didn't work it out", so a
blank cost basis cannot average in as a zero. Nothing is shortened. The sync
ledger (`remote_shadow`, `dirty_fields`) is left out for the same reason
`GET /api/listings` drops it: it is the server's bookkeeping, not an inventory.

**A spreadsheet runs what it opens.** Every title and description in the store
was written by an AI draft or imported from eBay, and a cell beginning `=`,
`+`, `-`, `@` or a control character is a *formula* to Excel, Numbers and
Sheets — `=HYPERLINK(...)` and `=cmd|...` are the documented attack on exactly
this kind of file. Those cells are prefixed with an apostrophe so they arrive
as text, and the guard takes care not to fire on a negative number: a cost
basis of `-4.50` is something the seller wants to sum. The file opens with a
UTF-8 byte-order mark, because without one Excel on Windows reads it as the
system codepage and every accented title arrives as mojibake.

**Columns are appended to, never inserted into.** A spreadsheet somebody built
a formula against is a file format; a column added in the middle silently moves
every column after it in files that already exist.

Size is bounded by streaming rather than by trimming: the response is generated
a page at a time, so the seller's whole store is never in memory at once.
`LISTING_EXPORT_CAP` (default 25,000) is a resource guard on top of that, and
when it bites the answer says so in `X-Export-Truncated` — the seller is told
the download was cut instead of discovering it by counting rows.

## Notes & limitations

- The AI never invents serial numbers, authenticity guarantees, or unverifiable
  specs — it flags those under "missing info" for you to confirm.
- **A publish is never sent against no rules.** `sanitize_specifics` is the last
  pass before eBay — it turns a tag's `W33 L34` into the Size eBay lists, an
  inch mark into a number, and a colour into eBay's own spelling — and it used
  to give up entirely when the Taxonomy lookup failed, sending every value as
  typed. eBay then refused the listing after the photos had uploaded, and the
  same values went through on the next press (by which time the lookup had
  answered). The aspect list eBay last gave us for a category is now kept with
  no expiry and stands in when the live call won't answer; where there has
  never been one, the corrections that need no list still run.
- **Views and watchers: a measured nought is a nought.** eBay's traffic report
  lists what happened, not what didn't, so a listing nobody viewed is absent
  from it. Absent used to reach the card as "no numbers", which drew no views
  row at all beside listings that showed "0 views" — the same fact, two
  different cards. Where the report (or the watch-count call) actually
  answered, every live listing it covered gets the nought it earned; where the
  call failed, nothing is filled, so an outage never reads as a store nobody
  visited.
- **A pending offer is a badge on the card.** eBay gives a Best Offer 48 hours;
  miss it and the sale is lost without the seller having declined anything. A
  live card carried views and watchers — both of which keep — and said nothing
  about the one number attached to a person waiting, so the offer chip sits
  beside the status badge, filled rather than tinted, naming the money on the
  table (`Offer $45.00`, or `3 offers · $52.50` with the best of them). It is
  read-only: accepting, countering and declining happen in eBay's own flow, and
  the tooltip says so along with when the first offer runs out. It is drawn
  from ONE unscoped `GetBestOffers`, which answers "who is waiting on you right
  now" for the whole account in a single call, filtered on `Pending` — the
  status field is what states a buyer is waiting, and the request filter is not
  evidence of it. Asked per listing instead, the question needs a shortlist to
  keep the call count sane, and the only field available to build one from is
  eBay's `BestOfferCount` — which counts offers *received*, settled ones
  included. **That shortlist is what hid a real offer.** A listing whose nine
  offers were all declined months ago scores 9 forever; the listing with one
  offer waiting right now scores 1 and sorts last, so on a store that haggles
  the per-sweep budget was spent entirely on listings whose offers were settled
  and the one with money on the table was never asked about — reporting
  nothing, which is honest and still leaves the seller unaware. Asking once,
  unscoped, removes the shortlist and the ranking with it; the per-listing form
  survives only as the fallback for a reply that can't be read. Same honesty
  rule as the nought above, and it decides that fallback: the account-wide
  answer is trusted to mean "nobody is waiting" only when eBay's reply was
  recognisably a Best Offers response at all, and a lookup that failed leaves
  the count ABSENT and draws no badge rather than telling a seller nobody is
  waiting. Whether the badge can go ON a card at all is this; when it comes
  back OFF is a separate question, answered by re-reading the overlay on the
  way back into the app (`METRICS_FRESH_MS` in `frontend/src/store.jsx`, with
  the deliberate "Sync with eBay" press reading past the server's cache) —
  answering an offer happens in eBay, so nothing here can learn of it except
  by asking again.
- **A buyer waiting outranks recency — on the grid and on the dashboard.** A
  bid or a pending offer is money waiting on an answer, and it used to reach
  the seller as a chip on a card that sat wherever its last write had put it.
  Both readers of the store now sort in tiers (`orderListings` and
  `recentListings`, `frontend/src/lib/listingsView.js`): the listings a buyer
  has acted on, then everything else still in play by `updated_at`, then the
  archive — newest first within each — so the card that has to be looked at is
  not the one the seller has to scroll for. It counts for most on the
  dashboard's *Recent listings* strip, which is four cards wide, where fifth
  place is off the panel entirely: `updated_at` records what was written last,
  not what is worth looking at, and the jobs that write are bulk ones — an
  enrich pass stamps every listing it fills, a relist stamps the one it renews
  — so four of those were four cards, and the auction with a bid on it was
  walked off the strip by four listings nobody had bid on. The lift is applied
  BEFORE the four are taken rather than after, for the same reason the sold
  ones are dropped before it: fifth place has already been dropped by the time
  anything downstream could lift it. A count that is ABSENT still lifts
  nothing — "we couldn't ask" is not "nobody" — so an unreadable metrics call
  leaves the strip in the recency order it always had.
- **Every price the app chooses ends in `.99`** (`backend/money.py` →
  `charm_price`, mirrored for the browser in `frontend/src/lib/charmPrice.js`):
  the AI's drafted price, the market number that overrules a draft priced far
  under the comps, the floor a high-value lookup raises a draft to, the
  headline comp suggestion, a comp row tapped in the price card, and a bulk
  percentage cut. It moves to the NEAREST charm point rather than always down
  — $25.00 → $24.99, $22.50 → $22.99 — so it is never more than half a dollar
  either way, and it is floored at $0.99. What the seller TYPES is theirs and
  is never rewritten; neither is what they paid (`purchase_price`, read off a
  resale sticker), what the tag says it retailed for (`retail_price`), nor the
  measured market range shown beside a suggestion.
- Image optimization never zooms. A photo that keeps its background is framed
  with the largest square the frame holds, slid over the item, so the backdrop
  you composed stays in the shot and nothing gets clipped in the gallery
  thumbnail; an item too big for that square keeps the whole photo, padded out
  to square. Tighter framing is yours to make — Crop and Smart crop in the
  photo studio.
- Category ID is auto-resolved via the eBay Taxonomy API when
  `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET` are set; otherwise enter it manually in
  the preview. Taxonomy data in the eBay **sandbox** is limited, so category
  suggestions are most accurate against production.
