# Launch checklist

The one list of what stands between this repository and a public launch. It
replaces `REMEDIATION_STATUS.md` (a 164 KB tracker that had stopped being true
by 2026-09-02; its history is in git) and folds in the full-code audit of
2026-09-09 — seven read-only reviews across the backend, the frontend, the eBay
integration and the infrastructure, checked against the production error feed,
the deploy logs and the health probes of the same day.

How to read it: **Fixed** is in this branch with a test. **Do before launch**
is code work that is scoped and small enough to do next. **Needs the owner**
is a deploy, a credential, a decision or a purchase that no commit can make.
**After launch** is real, known, and deliberately not on the critical path.
Every item names the file it lives in, so nobody has to re-derive it.

## Where production stood on 2026-09-09

- App healthy: `/api/ready` green, 2 GB free on the volume, R2 fine, database
  connected, the cutout model loaded; production is running `main` HEAD;
  `app.thryftshop.com` certificate issued and verified.
- The gates are green: 2757 backend tests (0 skipped), 697 frontend tests,
  ruff clean, lint clean, smoke walk clean.
- The error feed (Admin → Errors, `/api/ops/error-feed`) carried four
  recurring failures over the week. All four are addressed below.

## Fixed in this branch

Production errors, from the feed:

- [x] **`traffic_report 429` ×28 in 36 h.** The eBay traffic report (a daily
      figure) was re-fetched every two minutes; the app's whole daily
      Analytics allowance was spent by lunchtime and every dashboard load
      after that was a refused call and a WARNING row. It now has an hour-long
      cache of its own, a 429 is remembered until midnight Pacific (eBay's
      reset), a stale report stands in meanwhile, and the skip is logged as
      information. `backend/services/metrics.py`;
      `tests/test_a_spent_traffic_allowance_is_not_asked_for_again.py`.
- [x] **`revise (imported) failed … Inseam is missing` ×26.** A revise that
      touches item specifics replaces the whole set, so eBay re-validates a
      jeans listing against today's rules and refuses one made before Inseam
      was required — and because a refused revise keeps its dirty marks, every
      later edit of that listing (a price drop, a title fix) failed the same
      way. The specifics are now held back exactly as during a Best Offer
      freeze, the price goes over, and the seller is told which aspect to
      fill rather than to wait. `backend/services/ebay_trading.py`,
      `backend/marketplaces/ebay_provider.py`;
      `tests/test_a_price_edit_lands_even_when_ebay_now_wants_an_inseam.py`.
- [x] **`trading publish failed: "W" is not a valid value for Size` ×19.**
      eBay's "not a valid value for X" sentence fell into the generic bucket:
      the seller saw "eBay rejected the listing" with no field to open. It is
      now a specifics issue naming the aspect. `backend/ebay_errors.py`;
      `tests/test_ebay_error_taxonomy.py`.
- [x] **One fingerprint for every refusal.** Both publish log lines put the
      classification in their arguments, so the feed had ONE row for all
      revise refusals (×26) and one for all publish refusals (×19), each
      showing whichever message came last. The refusal's target and aspect
      are in the template now (`ebay_provider.refusal_key`), and the daily
      triage skips refusals over a field the seller was pointed at while
      still proposing fixes for the ones the app could not explain.
      `.github/scripts/triage_errors.py`;
      `tests/test_the_triage_skips_a_refusal_the_seller_was_pointed_at.py`.
- [x] **`image import: host not allowed: app.thryftshop.com` ×12.** eBay
      hands back the `/media` URL the app gave it, the store sync stores that
      as the mirror's photo URL, and adopting the mirror for editing asked the
      CDN allowlist to fetch the app's own hostname. The app's own URLs are
      now recognised (on its own origins only) and read from the volume or
      from R2. `backend/services/image_import.py`;
      `tests/test_a_photo_ebay_echoed_back_from_our_own_host_is_adopted.py`.

Money and data:

- [x] **A token purchase whose commit failed was acknowledged to Stripe** as
      "already applied" — no ledger row, no retry, seller charged. Only an
      idempotency collision is "already" now; anything else is None, which
      the webhook answers 503 so Stripe redelivers. Same for chargebacks.
      `backend/db.py`; `tests/test_a_purchase_that_did_not_commit_is_not_acknowledged.py`.
- [x] **Partial refunds computed their over-refund cap outside the row
      lock.** The account lock is now taken before the prior-refund sum.
      `backend/db.py`.
- [x] **One legacy listing id could switch off every housekeeping pass**
      (originals prune, exports prune, R2 offload, pending deletions, owed
      refunds) because the orphan sweep raised on it first. It is skipped
      and logged. `backend/main.py`; `tests/test_one_bad_listing_id_does_not_stop_housekeeping.py`.
- [x] **"Fill in details" could bill twice on a double tap** — the running
      check and the reservation were in separate lock sections. One critical
      section now, as the import route already did. `backend/main.py`.
- [x] **The store-shelf cache outlived an eBay account switch**, filing new
      drafts onto the previous store's categories for six hours. Cleared on
      disconnect and on a switch. `backend/main.py`.

Security and operator detail:

- [x] `/docs`, `/redoc`, `/openapi.json` no longer published (they listed
      every admin and ops route, under the pre-rebrand product name).
- [x] A non-ASCII byte in `x-admin-token` / `x-error-feed-token` was a 500
      and an error row; it is a 401. `tests/test_a_token_with_a_stray_byte_is_refused_not_a_crash.py`.
- [x] `/api/ebay/status` named unset environment variables anonymously;
      `/api/listings` carried the database driver's error text (the Neon host
      and role) anonymously. Both gated. `tests/test_operator_detail_is_not_served_anonymously.py`.
- [x] Seven synchronous database round trips inside `async` handlers (the
      uploads, the preflight, two image edits, and eBay's account-deletion
      notice, which arrives 1–2× a minute) moved onto the threadpool.
- [x] Least-privilege `permissions:` on every workflow; the deploy gate warns
      when the token-encryption key is not a Fly secret and when
      `FLY_API_TOKEN` / `FLY_ACCESS_TOKEN` are (they should never be app
      secrets); `claude-code-action` repinned to the commit its tag points
      at; Dependabot now covers pip and both npm lockfiles.

Frontend:

- [x] Dialogs: focus moves in on open, Tab cycles inside, focus returns on
      close, JSX titles get an accessible name, only the topmost dialog owns
      Escape, and the scroll lock is shared (a confirm over the shipping
      dialog used to close both and unlock the page). A second `confirm()`
      no longer strands the first caller. `frontend/src/components/ui/Dialog.jsx`, `Toaster.jsx`.
- [x] Logout no longer waits up to 90 s on a dead server; the store sync
      watcher has an overall deadline; delete-account tears the session down
      the same way logout does (native token included). `frontend/src/store.jsx`, `SettingsView.jsx`.
- [x] Profit lines said `$` beside currency-aware prices; the photo Edit
      control was hover-only and invisible on phones; the "Check" button had
      no accessible name on phones; an undefined `text-ink-soft` token; the
      uploader accepted 40 photos into a listing eBay caps at 24 (both caps
      now agree); seven stale `eslint-disable` directives and five dead
      exports removed.

Docs and repo hygiene:

- [x] README: the removed Pixian/Photoroom/Adobe engines, the roadmap, the
      project layout, and "pushed to eBay as a draft" corrected; `.env.example`
      renamed, the production alias names and `DATA_DIR` added, the Inventory
      API text replaced; Node stated as 22 everywhere; `.dockerignore` keeps
      `node_modules`, `dist`, the native projects and the tests out of the
      image; stale Adobe docstrings and the dead `presigned_put` gone.

From the "do before launch" list, on 2026-09-10 (every gate re-run green:
ruff, the whole backend suite with no skips, the frontend lint, unit tests and
build, the smoke walk and the reach check):

- [x] **"Restore original" never worked on an imported photo.** The import
      named its working copies `img_NN.jpg` and `source_for` reads back only
      the `img_NNN.jpg` the photo pass writes, so every restore answered
      "nothing to restore" for an original that was on the volume. Three
      digits now. `backend/services/image_import.py`;
      `tests/test_a_photo_ebay_echoed_back_from_our_own_host_is_adopted.py`.
- [x] **"Restore original" on a photo added later restored somebody else's.**
      "Add photos" keeps its originals as `add_NNN` beside the upload's
      `src_NNN`, `add_` sorts before `src_`, and the lookup counted along the
      directory by position. It reads the index off the filename first now.
      `backend/services/images.py`; `tests/test_a_photo_can_go_back_to_what_was_shot.py`.
- [x] **Login during a database outage read as "wrong password"** until the
      status cache noticed, and a wrong password after the outage read as
      "the database is down" until it noticed again. `get_user_by_email`
      raises `StorageUnavailable` like `get_user_by_id` already did, and the
      route no longer consults the cache. `backend/db.py`, `auth.py`, `main.py`;
      `tests/test_login_during_an_outage_is_not_a_wrong_password.py`.
- [x] **A failed boot-time ALTER was `pass`**, identical to the sixteen
      expected "already exists" answers. Those are told apart and anything
      else is logged with its statement. `backend/db.py`;
      `tests/test_a_failed_boot_migration_is_logged.py`.
- [x] **Raw exception text reached the client** at the edit, rotate, restore
      and delete photo routes, the field PATCH, the shelf scan, the ship-from
      catch-all and the three background jobs' status. Each says what could
      not be done and quotes the support reference; validation keeps the
      field and the rule and drops pydantic's report; the shelf scan uses the
      AI error wording; eBay's own postal-code refusal stays in eBay's words.
      `backend/main.py`; `tests/test_a_failure_the_seller_cannot_act_on_is_a_sentence.py`.
- [x] **Model output was trusted for shape.** A bare value that parsed
      crashed identify on `.get`; a title that came back as a list crashed
      it on `.strip`; a `refusal` stop was "try again". Not-an-object is now
      an unreadable answer, a field that is not text is an empty field, a
      refusal is said as one (422, with what would change it) in identify,
      refine and on the bulk card. The refine instruction is capped and the
      reverse-image leads are fenced as evidence, one bounded line each.
      `backend/services/claude_ai.py`; `tests/test_the_models_answer_is_checked_for_shape.py`,
      `tests/test_what_the_seller_types_and_what_the_web_says_are_bounded.py`.

## Do before launch (code, scoped)

Ordered by what it costs a seller.

- [ ] **The editor drops unsaved edits on every exit.** Form state lives in
      `useListingForm` and any navigation unmounts it; there is no dirty
      flag, no `beforeunload`, no save-and-stay ("Save Draft" closes the
      editor). Add dirty tracking, guard the exits (sidebar, "Back to batch",
      "Fix this" → Settings, reload), and a save that stays.
      `frontend/src/views/listing/useListingForm.js`, `NewListing.jsx`, `PublishCard.jsx`.
- [ ] **Bulk queue safety.** Per-card Publish stays live during "Publish
      all", and the loop publishes the snapshot captured at click, so a card
      the seller publishes by hand mid-run goes out twice and an edit typed
      mid-run is overwritten. Freeze the cards while the loop runs, re-read
      each item and skip non-drafts, and persist inline edits (they are
      memory-only today and vanish on "Review & List" or reload).
      `frontend/src/views/listing/BulkMode.jsx`.
- [ ] **`addImages` writes a minutes-old snapshot back over the form and the
      server** (a delete during a long add is reverted). Use functional
      `setForm` and PATCH only `images`. Same shape for `loadCategoryMeta`
      (no request sequencing, can install the wrong category's aspects) and
      for conflict resolution (updates the session but not the form).
      `useListingForm.js`, `NewListing.jsx`.
- [ ] **A background sync clobbers unpushed local price/quantity edits.**
      `_merge` overwrites `_LIVE_FIELDS` from eBay before `_reconcile` builds
      the local side, so a draft-saved price edit on an imported listing is
      replaced by eBay's number and then "pushed". Pass the prior record as
      the local side. `backend/services/listing_sync.py` (~278–314, ~1006–1012).
- [ ] **Listings are written through an unlocked whole-document upsert from
      43 sites** while `mutate_listing_data` (lock-safe) exists. The editor
      save and the status sweep are guaranteed concurrent writers; whichever
      lands second erases the other's `sold_at`/`dirty_fields`/`conflicts`.
      Route `POST /api/save/{id}` and the sweep through the locked path, or
      add a version precondition. `backend/db.py`, `main.py`, `listing_sync.py`.
- [ ] **Predictable `ebay-<itemId>` ids can be squatted.** `_assert_session_owner`
      passes when no row exists and `POST /api/save/{id}` accepts a
      caller-chosen id, so a signed-in attacker who knows a public item id
      can claim the row a victim's import will later land in. Refuse a new
      `ebay-*` id from every route that creates rows. `backend/main.py` (~2064, ~5415, ~6661).
- [ ] **Abuse controls when billing is off.** `/api/upload` is anonymous with
      no rate limit and only a per-file cap (40 × 60 MB) on a 1 GB volume;
      `/api/etsy/suggest-taxonomy` runs a Claude call with no login, no
      charge, no limit; with `TOKENS_ENABLED` off every AI route is anonymous
      spend. Require login on the bulk upload unconditionally, add a per-IP
      limit and a per-request byte cap on `/api/upload`, and charge or gate
      the Etsy suggestion. `backend/main.py`, `backend/ratelimit.py`.
- [ ] **R2 client init holds a lock across un-timed network calls** and
      `objstore.probe()` has no caller; give boto3 a `Config` with timeouts
      and probe from the startup thread. No `statement_timeout` /
      `lock_timeout` toward Neon either. `backend/objstore.py`, `db.py`.
- [ ] Smaller, each a few lines: `ImageEditor` Escape/backdrop bypass the
      AI-busy lock and its layer canvases are never released; object URLs leak
      when the uploader unmounts; `Field` wraps the selling-format buttons in
      a `<label>`; `InfoTip` explainers are `title` tooltips and do not exist
      on touch; `ListingCard`'s memo is defeated by inline callbacks;
      `SettingsView` keeps its own copy of the policies cache; the deletion
      queue has no backoff so 500 permanently failing rows starve newer ones;
      `logarchive` writes two days under a one-day key and keeps `user_id`
      outside erasure scope; `tag_crops` index shifts when a file is missing;
      `_ACCOUNT_LEVEL_FIELDS` fixed here, but `scan_shelf` still sends raw
      client bytes as JPEG (up to 8 × 60 MB) without re-encoding.

## Needs the owner (a deploy, a credential, a decision)

- [ ] **Key custody.** `TOKEN_ENCRYPTION_KEY` is not set; every seller's
      marketplace refresh token is encrypted under `SECRET_KEY`, which IS a
      Fly secret. Put that value in a password manager today; rotating it
      disconnects every seller. Consider setting `TOKEN_ENCRYPTION_KEY`
      explicitly so the two duties are separable.
- [ ] **Remove the Fly tokens from the app.** `FLY_API_TOKEN` and
      `FLY_ACCESS_TOKEN` are app secrets in production (the deploy log lists
      them). Nothing reads them; they hand full account control to anything
      that can read the process environment.
      `flyctl secrets unset FLY_API_TOKEN FLY_ACCESS_TOKEN -a listing-lfwjrg --stage`.
- [ ] **Unset the dead credentials** while there: `PHOTOROOM_API_KEY`,
      `LIGHTROOM_API_KEY`, `OPENAI_API_KEY`, `SENDGRID_API_KEY`,
      `Ebay_Sandbox_App_Id` / `Ebay_Sandbox_Cert_Id` / `Ebay_Sandbox_Dev_Id`,
      `STRIPE_API_KEY` (the near-miss; `STRIPE_API_SECRET_KEY` is the one read).
      Nothing in the tree reads any of them.
- [ ] **Backups, rehearsed once.** There is no backup/restore story anywhere
      for the volume, Neon or R2. Confirm Fly volume snapshot retention,
      Neon's point-in-time window on the current plan, and R2 versioning; do
      one restore end to end; write it down in a runbook (the README's
      operator sections should move there — see below).
- [ ] **Extend the volume to 3 GB** as the README already instructs (it is
      1 GB; the trim, page and readiness thresholds sit ~100 MB apart).
- [ ] **Alembic cutover, once, on the machine:** `alembic check`, then
      `alembic stamp head`, then `alembic upgrade head` — and only THEN wire
      `upgrade head` into the deploy. Three post-initial revisions exist now,
      so this is no longer a no-op. The order matters (an unstamped upgrade
      fails on the first CREATE TABLE mid-deploy).
- [ ] **Support address and legal identity.** The marketing site publishes
      `support@thryftshop.com`; the app's About, Privacy, Terms and Settings
      publish a personal Gmail. Pick one (on the company domain), confirm it
      receives mail, and update `frontend/public/{about,privacy-policy,terms}.html`,
      `SettingsView.jsx`, `TESTFLIGHT_CHECKLIST.md`. Counsel review of the
      two legal texts, a retention schedule and a legal address are the
      non-code half of the same item.
- [ ] **Decide the deploy outage.** Every merge takes production down for
      the machine's restart plus up to 60 s of model warm-up (`--strategy
      immediate`, one machine). ~60 merges in the last nine days were ~60
      blips. Either document it as acceptable for now, or move to a rolling
      strategy with the volume treated as a cache (R2 is already the store
      of record).
- [ ] **Branch protection.** Confirm the required checks on `main` are named
      `Gates / Lint + unit tests`, `Gates / Cutout safety`, `Gates / Frontend
      build`, `Gates / App smoke test` (the rename that the old tracker
      flagged as unverifiable).
- [ ] **Run `prune-branches.yml` once, then delete it and its list.** It has
      never run; 118 `claude/*` branches remain and the checked-in list of 33
      is itself stale.
- [ ] **`ebay_user_id` backfill** on `ebay_accounts` rows from before the
      identity scope; until a seller reconnects, that account's records fall
      back to username matching.
- [ ] **eBay Sandbox account** for the contract tests the old tracker asked
      for (P2-08); every eBay contract is pinned by XML fixtures, none has
      been exercised against a real sandbox call.
- [ ] **Non-root container** needs one local `docker build && docker run`
      to settle volume ownership at `/data` and the model cache path.
- [ ] Tidy the marketing gate: an app pull request that also touches
      `marketing/` fails "App is untouched" by design; it is not a required
      check, so it only confuses.

## After launch (known, not on the critical path)

- Split `backend/main.py` (9.5k lines; the same owner check, path-traversal
  guard and truthy parse are re-typed 6–10× each) and `cards.jsx` /
  `BulkMode.jsx` / `useListingForm.js` (1–2k lines each), with
  characterisation tests first. URL routing (deep links, back/forward).
- Normalised tables for external listings, marketplace operations and
  durable jobs (still one JSON document per listing; the publish lock is
  process-local; the enrich/import runners are threads).
- `JSON` → `JSONB` on `listings.data` with a GIN index; `admin_platform_kpis`
  and `count_foreign_listings` scan every row today.
- Etsy and Depop revises send the whole payload (no shadow, no dirty
  tracking per marketplace) — the eBay three-way merge does not exist there.
- Trading-side call budgeting: the watchers/offers walks (GetMyeBaySelling
  up to 25 pages, GetBestOffers up to 10) run on every 2-minute cache miss;
  caches keyed on `token[-12:]` churn on each refresh; `RateLimited.retry_after`
  is captured and never honoured; `probe_block_scope` fires six Verify calls
  per 240.
- Auction publishes have no SKU-based recovery for a lost response.
- Duplicate helpers across services (`_is_scope_error` ×4, `_NEVER_SENT` ×4,
  `_app_token` ×2, `delete_prefix` vs `_strict`, three `prune_*` walks,
  `EbayAccount` vs `MarketplaceAccount`); dormant flags `IDENTIFY_CHAIN=v1`
  and `RESEARCH_PASS` (~130 lines); `scripts/migrate_session_ids.py` and
  `storage.legacy_session_name` (a one-shot whose precondition never
  existed in production — run it dry once to prove 0, then delete).
- Docs: move the README's operator sections into `docs/OPERATIONS.md` (a
  runbook: topology, secrets by name, deploy/rollback, backups, playbooks
  for disk full / R2 degraded / eBay quota) and write a short
  `CONTRIBUTING.md` (branch → PR → four gates → merge is the deploy; the
  no-skip rule; where a new env var goes). Merge `TESTFLIGHT_CHECKLIST.md`
  into `MOBILE.md`; move `SMART_LIST.md` under `docs/design/`.
- Route-split the admin screens out of the 515 KB main chunk; the Google
  Fonts stylesheet blocks first paint; the native shell is hard-wired to
  the `.fly.dev` host, so that hostname cannot be retired without a release.
