# Enterprise beta remediation — status and agenda

Working branch: `claude/get-to-work-1qorht` · PR: <https://github.com/LeifHansen/Listing/pull/202>

This file is the resume point. It records what is done, what is not, and the
exact next steps, so work can continue in a fresh session without re-deriving
any of it. Delete it when the remediation is finished.

Source documents: `Listing_Enterprise_Beta_Audit_20260829.md` and
`Listing_Coding_Agent_Remediation_Prompt.md` (supplied by the owner; not in
the repo).

## Ground truth about the baseline

The audit was written at `bb9990a`. **That is only 2 commits behind where this
branch started** (`c819fd7`), so the audit is essentially current, not stale.
The number 47 that appears in early notes is the distance from `origin/main` —
and `main` lags **45 commits behind the audited commit**. Do not repeat the
"47 commits past the audit" framing; it is wrong.

Baseline measured before any change, at `c819fd7`:

| Check | Result |
| --- | --- |
| Backend Ruff | Passed |
| Backend pytest | 961 passed, 2 failed |
| Frontend ESLint | Passed |
| Frontend Vitest | 7 files, 99 tests passed |
| Frontend production build | Passed |
| npm audit (production) | 0 vulnerabilities |

The audit expected 944; the +17 is the tests added by the 2 intervening
commits (`#200`, `#201`).

**The 2 failures are environmental, not defects.** `Dockerfile:28` bakes the
~176MB `isnet-general-use` rembg model into the image. This container has no
`~/.u2net` cache, so the first inference in `test_readiness.py` blocks on a
model download past the test's 5s window and leaves `_INFER_LOCK` held, which
fails the second test too. They fail identically with none of these changes
applied. Do not attribute them to the remediation.

Environment setup in a fresh container:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest ruff pillow numpy scipy
cd frontend && npm ci && npm run build   # backend tests need frontend/dist
```

Fast backend loop (skips the slow image suites):

```sh
.venv/bin/python -m pytest backend/tests -q \
  --ignore=backend/tests/test_readiness.py \
  --ignore=backend/tests/test_cutout_regression.py \
  --ignore=backend/tests/test_matte.py --ignore=backend/tests/test_bg_border.py \
  --ignore=backend/tests/test_bg_holes.py --ignore=backend/tests/test_dark_backdrop.py
```

Currently **922 passed** on that subset, Ruff clean. Full suite: **1100+ passed, the 2 environmental failures above**.

## Done — all 8 P0 release blockers

Each has a regression test written before the fix and verified to fail against
the pre-fix code. No existing safety test was weakened or deleted; five that
encoded wrong behaviour were corrected in place, each saying why.

| P0 | Commit | What it was |
| --- | --- | --- |
| P0-01 session/media id aliases | `dacf6ed` | Storage stripped non-alphanumerics from ids while the DB kept them raw, so `abc123` and `abc123-` were different rows and the same directory. Ownership asked the DB, the file operation asked storage. **Reproduced**: an anonymous caller read a victim's photo through `/media` and overwrote their listing (`{"saved":true}`). `safe_session_name` now accepts or rejects and never rewrites, making the mapping injective. |
| P0-02 quantity restock | `34cae1e` | `Item.Quantity` is total-listed, not remaining; import read it straight across and revise re-sent it on every edit, so a title change put sold units back on sale. `max(1, …)` made sold-out unrepresentable. |
| P0-03 deletion endpoint | `5d551c3` | Returned 200 to anything: no signature check, `userId` ignored, nothing deleted. Now verifies ECDSA over raw bytes (412), records durably before acknowledging (503 if that fails), then erases. |
| P0-04 mutable ownership | `0fce892` | Ownership keyed on the seller's changeable username, and returned true when *either* side was blank — so an unidentified account matched every record. Immutable `userId` now decides. |
| P0-05 marketplace wipe | `34cae1e` | eBay release dropped the whole `marketplaces` map, taking Etsy and Depop with it. |
| P0-06 false success | `bc14631` | Repository commands swallowed failures and returned `None`, so OAuth said "connected" and Settings said `{"ok": true}` on writes that never landed. |
| P0-07 publish idempotency | `57eed10` | Guarded by `InventoryTrackingNumber`, which is not an eBay `ItemType` element — ignored on write, and the `GetItem` lookup built on it could never succeed. Now `SKU` + `InventoryTrackingMethod=SKU`. Also **added 488** (the real duplicate-UUID code) and **removed 21916884/21916885**, which are eBay's item-*condition* codes — a fixable condition rejection was being reported as "already published". The audit missed this one. |
| P0-08 three-way sync | `66fcb16` | Inbound refused newer remote content; outbound sent the whole payload. A price edit overwrote a title fixed in Seller Hub. Added `remote_shadow` as the base, conflict records, and minimal revise payloads. |

### Traps already hit and fixed — do not reintroduce

1. **The read-side legacy fallback (P0-01).** A first draft of `session_dir`
   fell back to the old stripped name when the canonical directory was
   missing. That re-opened the whole vulnerability: `3aaeb40637a1-` is a
   valid id with no directory of its own, so the fallback handed the caller
   the victim's photos through the front door. There is deliberately **no
   read fallback**; migration is driven by database rows instead.
2. **The migration itself (P0-01).** `scripts/migrate_session_ids.py` first
   moved the victim's directory into a lookalike's name when handed
   `3aaeb40637a1-` — and saving a draft needs no account, so that row is
   creatable. A legacy name that is itself a live session id, or that two ids
   claim, is now refused and reported.
3. **The no-shadow rule (P0-08).** An earlier draft took the remote copy when
   no shadow existed. Every record in the database has no shadow, so that
   would have overwritten every seller's local work on the first sync after
   deploy. An existing test caught it. With no shadow, nothing is reconciled.
4. **Minimal revise needs every writer to declare itself (P0-08).** "Lower
   prices" sets `listing.price` and publishes with no save in between, so
   there is no diff to find — it went out as an EMPTY revise, telling eBay
   nothing while reporting success. Fixed in `dc8d195`/`a8d8f0e`; any new
   path that mutates a listing and then publishes must call `mark_dirty`.
5. **A test that imports `backend.main` runs in exactly one CI job.** The
   fast `checks` job omits the heavy stack, so such a file must
   `importorskip` AND be listed in the smoke job's "API tests" step, which
   fails on a skip. The guard alone would have left the session-alias
   authorization tests running nowhere.

## Found during remediation, not in the audit

| Finding | Commit | What it was |
| --- | --- | --- |
| A failed store read reported an empty store | `9381df8` | `db.list_listings` answered `[]` on any exception, like every read in `db.py`. For this one that is not a survivable degradation: the eBay import matches incoming items against the records it finds and imports whatever it does not, so **one Postgres blip during a sync imported a second copy of the seller's entire eBay store**, reported as a successful sync, leaving real duplicate listings to merge by hand. The same `[]` also let a release pass report "released 0", a status sweep report checking a store it never read, and the session-id migration report nothing to migrate. The read now raises `StorageUnavailable`; `list_listings_best_effort` exists for the three callers where empty genuinely only thins the answer (metrics, the weight pre-fill, the deletion preview's "unknown" path). No database configured is still `[]` — a configuration, not a failure. Client-side had the mirror image: a failed `/api/listings` left the cache at its initial `items: []`, so the store the app could not read rendered as "No listings yet" with a button to create a first listing. |

8. **Recording work per row inside a transaction that deletes set-wise.**
   The media-purge queue was first written with one `session.merge()` per
   listing: a SELECT and an INSERT each, inside the open `delete_user`
   transaction. 2,000 listings measured 4,009 statements — fine on SQLite,
   thousands of serial round trips against a cross-region Postgres, long
   enough to hit a statement timeout and roll the whole deletion back, so
   the seller is told their deletion failed. Batched deletes+inserts bring
   the same 2,000 listings to 19 statements. `delete_user` deletes the
   listings set-wise for exactly this reason; anything added to that
   transaction has to be set-wise too. Pinned by a statement-count test
   (not a timing one, which would pass on a laptop and prove nothing).

## Two operational consequences of the CI change — read before merging

1. **The required-check NAMES change.** A called workflow prefixes its job
   names with the calling job, so "Lint + unit tests" becomes
   "Gates / Lint + unit tests" (same for Cutout safety, Frontend build, App
   smoke test). If branch protection on `main` requires the old names, merges
   will block until the required checks are renamed in the repo settings.
   Nothing in the repository encodes those names, so this cannot be fixed
   from a commit.
2. **Deploys get slower and can now fail for new reasons.** Production was
   previously reachable after ~30s of unit tests; it now waits on the smoke
   test (boots the app, drives Chromium) too. That is the point — those jobs
   exist because they catch failures the unit tests do not — but a deploy
   that used to go out will now sometimes stop.

## Not done — agenda, in priority order

### 1. Deploy-time work for what has already landed

The `media_purges` table arrives through the same guarded `create_all`
as `ebay_deletion_notices`; nothing else is needed for it. Watch
`deletion_backlog` in `/api/admin/diagnostics` after the first deploy —
it should be 0, and a number that does not come back down is an
erasure this app promised somebody and has not managed to do.

- [ ] **Run `scripts/migrate_session_ids.py`** (dry run first, then `--apply`).
      Until it runs, imported listings' photos are filed under the old
      stripped names and will not be found. This is a **required deploy step**,
      not optional. It reports collisions rather than guessing; investigate
      any it prints.
- [ ] **Backfill `ebay_user_id`** on existing `ebay_accounts` rows. It is
      populated on connect and on profile sync, so today it fills in only when
      a seller reconnects. Until a row has it, that account's records fall back
      to username matching. Deletion notices for un-backfilled accounts record
      `no_match`.
- [ ] Add `EBAY_VERIFICATION_TOKEN` checks to the deploy gate — the deletion
      endpoint's GET challenge 503s without it.
- [ ] **Set `ADMIN_TOKEN`** in production, or `/api/admin/diagnostics` stays
      closed (deliberately — it fails closed). Documented in `.env.example`.
- [ ] Decide whether the Promoted Listings default flip (P1-06) is wanted.
      It follows the audit and the remediation prompt, but reverses a
      product choice made in response to seller complaints.

### 2. P1 and P2 items — what has landed, and what has not

| P1 | Commit | What it was |
| --- | --- | --- |
| P1-06 promotion consent | `97122c7` | Promoted Listings (COST_PER_SALE, 10% default rate) was enabled when the preference was ABSENT and when the prefs read RAISED — so silence and a database outage both counted as agreeing to a fee. Now off unless explicitly on. **This reverses a deliberate product choice** (the old default was on because sellers reported publishes landing unpromoted); the commit message says so. The UI mirrored the old default independently and was changed with it. |
| P1-09 public surface | `e2ca586` | Anonymous `/api/health` returned 26 operator keys including raw DB/R2 exception text (Neon host and role, R2 account id). Moved to `/api/admin/diagnostics` behind `ADMIN_TOKEN`, which **fails closed**. `build` deliberately stays public — deploy.yml, deploy.sh and health-watch.yml all poll it. Also: an unconnected production publish no longer answers `ok: true` with the Trading XML and a server path. |
| P1-01 automatic whole-store import (partial) | `3915997` | Every browser session with a connected account fired a whole-store import (one GetItem per listing, capped at 2,500) AND a concurrent FORCED status sweep — against a default allowance of 5,000 Trading calls a day. A second tab, a phone, a reload and a redeploy each spent it, unasked. The mirror is durable, so showing it costs nothing; an automatic rebuild now runs only on the first load after connecting or once the last one is 6h old, and the forced sweep is reserved for the deliberate "Sync with eBay" press. The REST of P1-01 (GetSellerList/GetSellerEvents instead of N+1 GetItem, notification-driven sync, pooled HTTP, quota budgets, and the "full sync" that samples a random 100) is untouched. |
| P1-01 honest sweep scope | `50c88ca` | The status sweep samples 100 live listings and reported only `checked`, so 100-of-400 and 100-of-100 were indistinguishable — behind a button called "Sync with eBay". It now reports `eligible`, `partial` and `sample_size` too. The sampling itself is right (a sweep is one eBay call per listing); admitting it is what was missing. |
| P0-01 follow-up: 400 not 500 | `2f3e45b` | Making `safe_session_name` reject rather than rewrite left the rejection with nowhere to go — a malformed id raised out of whatever handler touched storage and every route answered 500. Verified against the booted app. Now a 400 with a bare body, handled centrally like `StorageUnavailable`. |
| P1-07 never create-on-unknown (partial) | `9386c08` | "Create my policies" collapsed "eBay says you have none" and "eBay could not be reached" into the same `None`, and created a policy either way — so every timeout minted another "Thryft Shop" policy on the seller's real eBay account, visible in Seller Hub and never cleaned up. The lookups are now three-state and refuse on unknown (503, "try again"). The rest of P1-07 -- splitting Settings into independently saved sections and tri-state loading -- is untouched. (Previewing the terms is the row below.) |
| P1-07 policy terms shown before they are made (partial) | `85553b9` | "Create my policies" created three real eBay business policies on the seller's account carrying terms this app chose — dispatch within 2 business days (eBay scores it), returns accepted for 30 days, buyer pays return postage, immediate payment required, domestic-only calculated postage. All of it is published to buyers and binds the seller, and the button said none of it. There is now a static, side-effect-free `GET /api/ebay/policy-preview` derived from the same request bodies the create sends, a dialog that shows them, and both create routes refuse without `accept_terms: true` — strictly `True`, so a stale client or a half-filled form is not consent. The return policy is also named for the window it was actually given (a 14-day policy was called "30-day returns" in Seller Hub). |
| P1-07 Settings sections save and load independently | `326a0c3` | One Save wrote local preferences AND the seller's eBay account inside one `try`, and reported one verdict: when the eBay half failed, a seller whose listing defaults had just committed was told "Couldn't save" — and the obvious response is to type it all again. The two now save independently and the message names which committed. Loading had the mirror-image bug: a failed `/api/prefs` did `setPrefs({})` and a failed policies fetch left the dropdowns empty, so the app rendered its own fallbacks as the seller's saved settings and told them their eBay account had no business policies — on the strength of having failed to ask. Both are now loading / couldn't-ask / answered, and only the last says anything about the account. Two panels that shimmered forever after a failed load now say so and offer a retry. |
| P1-08 script-src no longer allows arbitrary inline script | `b3c0764` | The CSP shipped with `script-src 'self' 'unsafe-inline'`, which is the one allowance that matters: with it an injected `<script>` runs, and most of the header was decorative. It was there because index.html carries one inline script (the pre-paint theme snippet) and a policy that blanks the app is worse than a partial one — the note on `_CSP` said so and said the fix wanted its own change. `build_csp()` now derives per-script `sha256-` sources from the built index.html **at startup**, so the policy cannot drift from the file it describes; a hardcoded hash would go stale on the first edit of that snippet and ship a white screen after a green deploy. No readable build falls back to the old policy — there is no frontend to protect and no hash that could be right. Verified in Chromium: the app loads with zero CSP violations, React mounts, and an injected inline script is refused. style-src keeps `'unsafe-inline'` deliberately (React and Tailwind set element styles, so there is nothing to hash) and that is now stated rather than implied. |
| P1-11 actions pinned to reviewed commits | `cbae67d` | Every third-party action ran from a mutable tag — `actions/checkout@v4`, `@v5`, `@v1.5` — and `fly-logs.yml` ran `superfly/flyctl-actions/setup-flyctl@master`, an unversioned branch, in a job holding `FLY_API_TOKEN`. A tag is a pointer the action's owner can move, so each of those meant "whatever that account publishes next". All five workflows now pin a commit SHA with the release in the comment beside it, each SHA verified by cloning the tag and confirming it resolves to that commit. Paired with `.github/dependabot.yml`, because the next failure mode is a pin nobody refreshes: it reads those comments and opens one grouped pull request per month, so the pin buys review rather than staleness. |
| P1-05 the promised erasure now survives a restart | `df8d18c` | Deleting an account dropped the rows and handed the photos -- a local directory and an R2 prefix per listing -- to an untracked background thread. Nothing recorded that the pass was owed and nothing checked that it finished, so a deploy, restart, OOM or crash part-way left the rest of the seller's photos in the bucket indefinitely, with the rows that named them already gone: nothing would ever look for them again, and the app had already said they were deleted. A `media_purges` row is now written **inside `delete_user`'s transaction** -- either the rows go and the debt is recorded or neither happens; a failure to record fails the whole delete, which is the right way round. `services/deletion_queue.run_pending` retries what is outstanding at startup and from the housekeeping loop, one stuck object never blocks the rest, and there is deliberately no give-up count. The same pass finally calls `pending_deletion_notices`, which was written for P0-03 and never wired up -- eBay stops resending once acknowledged, so a notice interrupted between 'recorded' and 'erased' was never going to be finished by anyone. `/api/admin/diagnostics` reports the backlog as counts. This makes the WORK durable, not the runner; the runner is still a thread (P1-02). |
| P1-08 sessions can be cancelled | `a44ce67` | The session JWT lives 30 days and nothing could invalidate one. Logout deleted the cookie and that was all, so a token copied from a shared device, a browser left signed in, a backup or a log kept full access for the rest of the month and the seller had no way to end it. `users.sessions_valid_from` records a cancellation and `auth.current_user` refuses any token issued at or before it -- on the user row that request ALREADY fetches, so it costs no extra query (a revocation check that costs a round trip is one that gets skipped under load). Not a token blocklist: that has to be kept, expired and consulted, and fails open when its store is unreachable. `POST /api/auth/logout-everywhere` and a Settings control drive it; `db.revoke_sessions` raises rather than reporting a failed write as success. The boundary is inclusive because `iat` is whole seconds, so the revocation second itself is refused -- PyJWT rejects future-dated tokens, so there is no way to sidestep it. Verified end to end on a booted server: old token 401s, a later sign-in works. |
| P1-01 eBay's call limit is recognised, not hammered | `e75d7e2` | The Trading client turned every non-200 into `eBay returned <code> for <call>` and every rejection into a per-listing failure. eBay's rate limit arrives as both -- HTTP 429, or **ErrorCode 21919144** in the body (developer.ebay.com KB 2137; per SELLER and windowed, so one busy store reaches it) -- so a sync that hit one counted the remaining listings as FAILED ("eBay rejected these", about listings eBay never saw), kept firing hundreds more calls into a windowed limit, which holds the window open and lengthens the wait, and showed the seller a raw HTTP status. `RateLimited` is now its own condition carrying eBay's own retry-after (header, or parsed from its "Try again after N seconds"; `None` rather than a default, since a number is a promise about when eBay will answer). The import and the sweep stop on it, skipped listings are not counted as failures, and the app says the sync is incomplete rather than "everything's already in sync" -- and does not latch the 6-hour auto-sync mark on a pass eBay cut short. The application daily quota is matched on eBay's published wording and labelled as a wording match, because its numeric code is not something this repo can cite. |
| P0-08 follow-up: a held-back edit now says so | `2944b27` | The three-way merge records a conflict when the seller and eBay have both changed a field, and correctly sends NEITHER value -- picking one silently is how a Seller Hub fix gets overwritten. But refusing to choose is half an answer, and nothing said so: the seller edited a title, pressed Update, and was told "Your eBay listing has been updated" while their title never left the building. They found it missing from eBay later with no reason given -- worse than an error, because an error at least prompts. The revise message now names the held-back fields and the result carries them; `POST /api/listings/{id}/resolve-conflict` settles one (keep mine queues it for the next revise, take eBay's writes it in), refuses a field nobody asked about, and is strict about the write; a banner in the editor asks. The base moves to eBay's value either way -- it records what eBay LAST SAID, so leaving it behind would re-raise the same answered conflict on every sync. |
| A card-level change no longer ships a stale whole listing | `8c73c0e` | The drafts strip changed a shipping policy or a category by spreading the listing it happened to be holding -- the copy from the last `/api/listings` load -- into a full `POST /api/save`, which is a REPLACE. So a title fixed in the editor in another tab, or anything a background sync had pulled in, was overwritten the moment somebody picked from a dropdown on a card. `PATCH /api/listings/{id}` now merges named fields onto the STORED record and marks them dirty so a live listing's change still reaches eBay; the allowed set is deliberately small, because a patch route that accepts anything is a full replace with extra steps. Same reasoning that already produced `PATCH /api/listings/{id}/images/order` -- "a reorder could overwrite a title edit made in another tab with a stale copy". The editor and bulk queue still use the full save, correctly: they hold the whole working copy, and clearing a field means sending the listing without it. |
| P2-07 the payments check answers a product state, not eBay's HTTP response | `bf9fefb` | `/api/ebay/payments-status` returned the deployment's eBay ENVIRONMENT, a raw HTTP status and eBay's entire response body, and Settings put all three in a toast: `Couldn't verify payments setup (production): eBay API error: 500 {"errors":[{"errorId":20403,...}]}`. `production` is deployment configuration on a route any signed-in seller can call -- the same class of leak taken out of /api/health -- and none of the rest is actionable. Worse, it did not separate the three answers that lead to three different buttons. It now returns one of `ready` / `action_required` / `reconnect_required` / `unavailable` / `contact_support` with the sentence to show; the raw detail goes to the log under a short `reference` returned to the seller, so mapping to a state does not throw the evidence away. `/api/ebay/diagnose-block` is deliberately left raw: it is the documented escape hatch a seller asks for explicitly, to quote to eBay support. |
| P1-04 (partial) the subtitle and duration the seller chose are actually sent | `021cce2` | Three fields collected and discarded. **Subtitle**: the editor has the field, the importer reads eBay's `SubTitle` back, and the request builder never emitted one -- `SubTitle` appeared in that module exactly once, in the parser. **Auction duration**: the editor offers 1/3/5/7/10 days and `create_listing` hard-coded `Days_7`, so picking ten days produced a seven-day auction. **Package dimensions**: `int(10.5)` is 10, and under-declaring a box is money the seller pays out of every calculated-postage sale -- now rounded UP, which is the side to be wrong on. Both of the first two are FEE-BEARING on eBay (SubtitleFee; AuctionLengthFee for Days_10), so they are sent because the seller filled the field in AND the fee is disclosed where they choose -- starting to bill silently for something the app had been discarding would be the same mistake as P1-06. XML contract tests, which the audit asks for under P1-04 and which is the only place these were visible. Still open in P1-04: aspect truncation at 40/65 before the seller can revise, and variation listings undetected. |
| P1-04 (partial) variation listings are detected and quarantined | `9f6ca8a` | eBay lets a fixed-price listing carry variations -- a shirt in S/M/L, each with its own SKU, price and stock -- and nothing here ever looked at `GetItem`'s `Variations` container. Such a listing imported as ONE flat record: a single price (eBay reports the lowest variation's), a single item-level quantity, no sign the other sizes exist. The write side was worse: a revise sent item-level `Quantity` and `StartPrice`, and eBay's own documentation says **ReviseItem does not support revisions of multiple-variation listings**, with a variation reaching quantity 0 **removed** from the listing (error 21916620) and the listing ending once none are left. The record now carries `has_variations`; the revise builder refuses (every revise path goes through it), the preflight and the browser's blocker list say so BEFORE the seller fills in a form, and the listing stays visible and end-able. The flag is eBay-owned in both merge paths so the quarantine LIFTS when the seller removes the variations on eBay. A real variation model is still open. |
| P1-03 (partial) a category eBay moved on a revise is followed | `6651df8` | `create_listing` reads the remapped `CategoryID` out of eBay's response and stores what eBay actually filed; `revise_listing` read the item id and threw the rest away. eBay's docs are explicit that Revise responses return `CategoryID` when the primary category changed OR when eBay remapped the one sent, and that remapping happens when `CategoryMappingAllowed` is true **or omitted** -- which this app's revise omits. So the revise was the one path where a silent move could happen and the only one not looking. The stored id is what every later aspect lookup, condition list and revise is built from. It is now captured, written to the record BEFORE the record is saved (set after, it lived only in memory -- caught by re-reading), and the seller is told the listing moved. Still open in P1-03: re-fetching the item to identify data eBay dropped in the move, and a per-seller mapping preference. |
| A failed location lookup no longer reads as "you have none" | `f02c4bb` | `account_overview` swallowed every ship-from location failure -- a timeout, a 401, an eBay outage -- into `[]`, and Settings rendered that as "No inventory locations found." It is the same shape as the `programs_known` tri-state two fields away, with more riding on it: publishing needs a ship-from location, so a seller told they have none goes to eBay and creates a SECOND one for an account that already had it. `locations_known` now separates "eBay said none" from "eBay didn't answer". |
| The ship-these-orders list says when it is one page | `f3e7acb` | The awaiting-shipment list asked eBay for 50 orders and returned whatever came back; eBay's own `total` for the filter was dropped. A seller with 80 orders to pack saw 50, with nothing saying there were more -- and this is the list they read to decide what still has to go out. eBay measures late dispatch, so the thirty invisible orders cost their seller standing, not just their afternoon. Same finding as the sampled status sweep: the answer now carries what it could NOT show. `total` is never invented -- when eBay omits it, it falls back to the page and `partial` stays False. |
| A refund that didn't commit is no longer lost | `45f284c` | "Only pay for AI that worked" is the promise. `db.token_refund` returns False when the write did not happen and `tokens.refund` threw that answer away, so a database blip in the refund window left the seller charged for AI that failed with **nothing anywhere recording the debt** -- the existing crash recovery only settles jobs whose PROCESS died, and a job that finished normally with a failed refund was never revisited. The debt is now written to the VOLUME, deliberately not the database: the reason a refund fails is usually that the database is unreachable, so a record there would fail at the same moment for the same reason (services/jobstore mirrors job status this way for the same reason). Retrying is safe by construction -- the ledger keys a full refund by the spend's entry id and a partial one by entry id plus amount -- so the pass needs no bookkeeping of its own and a crash mid-settle costs nothing. Drained at startup and from the housekeeping loop; the backlog is in `/api/admin/diagnostics`. |
| P2-07 (extended) Stripe's own words stop reaching the buyer | `c45434b` | Same rule as the payments check, on the screen where someone is trying to give us money. `POST /api/tokens/checkout` answered a failure with `Couldn't start the purchase: {exc}`, and `exc` is Stripe's integrator-facing message -- `No such price: price_1ABC`, or `Invalid API Key provided: sk_live_51H4x***`, which puts a fragment of a LIVE SECRET in a toast. `/api/tokens/confirm` returned the exception text verbatim. Both now answer with a product sentence carrying a support reference, and the raw detail goes to the log under it. Each says the thing the log cannot: checkout says nothing has been charged (creating a Checkout Session moves no money -- a fact, not a reassurance), confirm says the tokens are still coming (the webhook credits the same session idempotently; that route is only the redirect fallback). The app's OWN refusals -- "no such pack", "this purchase belongs to a different account" -- are still shown, because those are exactly what the buyer needs to read. |
| A paid promotion is no longer recommended on an unanswered question | *this commit* | `promotions.active_ads` answered `{}` both when eBay said the seller has no ads and when the lookup FAILED, and the insights panel read the empty map as "Not promoted yet — promoted listings show up far more often". So during an ads-API blip a seller who promotes in Seller Hub was told their already-advertised listings were unpromoted, and invited to buy a second ad — Promoted Listings costs a percentage of the sale. That is the P1-06 rule from the other side: a fee must not be RECOMMENDED on the strength of a question nobody managed to ask. `active_ads_status` now returns (ads, known), a failed lookup is deliberately not cached (an outage must not be remembered as "no ads" for a whole TTL), and the recommender drops both promote nudges when the answer is unknown while keeping every other recommendation. |
| P2-03 unknown-outcome copy | `8df583b` | Every timed-out request said "Nothing was lost — try again", including a publish or a delete that may already have reached eBay. Writes now say the outcome is unknown and to check first, which is the difference between a retry and a duplicate live listing. |
| P1-05 privacy policy accuracy (partial) | `8a41647` | The policy made three claims the code contradicted: that deletion "immediately" removes photos (the media purge runs after the response returns), that it "hands your marketplace authorizations back" (nothing revokes the OAuth grants), and that eBay deletion notices are merely "recorded for audit" keyed on nothing (they are now verified and acted on, keyed on eBay's immutable id). Stripe was also absent from the service-provider list despite processing token purchases. Copy now matches the implementation. **Not legal review** — the audit's ask for counsel review, retention schedule, legal identity and a company-domain contact is untouched. |
| P1-11 deploy gate (partial) | `1521558` | `deploy` gated production on the lightweight lint+unit job ALONE — cutout safety, the frontend build and the smoke test never gated a deploy, and `ci.yml` runs only on `pull_request` so a push to `main` ran none of them. Both now call one shared `gates.yml`. `superfly/flyctl-actions/setup-flyctl` was pinned off `@master` (it runs in the job holding `FLY_API_TOKEN`). **See the two operational consequences below.** |
| P1-08 security headers (partial) | `46b89d3` | None of CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy or Permissions-Policy were sent. Now all present, verified against the booted app. The CSP keeps `'unsafe-inline'` in script-src because index.html has an inline theme script — tightening to a nonce is worth its own change. The REST of P1-08 (revocable sessions, password reset/verify/MFA, distributed rate limiting, fail-closed keys) is untouched. |
| P1-12 side-effect-free GET | `a8d8f0e` | A plain read downloaded up to 24 photos, wrote up to 48 files, started an R2 upload and wrote the DB row. Now `POST /api/listings/{id}/prepare-for-editing`, called by the frontend when the seller opens the editor. |

Still open:

- [ ] P1-01, MINUS the per-session auto-import and the rate-limit handling
      (both done, see above): the import is still N+1 — one `GetItem` per
      listing where `GetSellerList`/`GetSellerEvents` would page the store —
      there is no pooled HTTP client, and nothing budgets the daily quota
      ahead of time or opens a circuit breaker. What is fixed is the
      behaviour once eBay says stop; what is not is spending less to begin
      with.
- [ ] P1-02 jobs/locks/cooldowns are process-local and non-resumable.
- [ ] P1-05, MINUS the policy corrections and the durable purge (both done,
      see above): nothing revokes the marketplace OAuth grants — the policy
      says so rather than promising otherwise, which is the honest interim
      state, not the fix. Still needs: counsel review, a retention schedule,
      legal identity/address, and a company-domain support contact (it is
      currently a personal Gmail).
- [x] **P1-07 is closed.** create-on-unknown, the unshown policy terms, and
      the shared save/load are all done (see the rows above).
- [ ] P1-08 auth baseline, MINUS the headers (`46b89d3`), the script-src
      tightening and session revocation (both above): still no password
      reset/verify/MFA, and rate limiting is still process-local. Sessions are
      revocable but there is no per-device list — "sign out everywhere" is
      all-or-nothing, which is the useful 90% and worth saying is not the
      whole. `style-src` keeps `'unsafe-inline'`, which is deliberate and
      documented, not outstanding.
- [ ] P1-11, MINUS the deploy gate and the action pins (both done, see
      above): `create_all` instead of Alembic; **the container still runs as
      root**.

      The non-root container was deliberately NOT attempted. Fly mounts the
      `data` volume at `/data` (fly.toml) owned by root, so a bare `USER`
      directive makes every upload fail in production, and the fix — an
      entrypoint that chowns `/data` and drops privileges with gosu/su-exec,
      plus `U2NET_HOME` pointed at the baked rembg models, which currently
      live in root's `$HOME` — cannot be verified here: this container has a
      docker binary but no reachable daemon, so the image cannot be built or
      run. Shipping an unverified change of that shape risks breaking uploads
      for every seller. It needs one local `docker build && docker run` with a
      volume mounted at `/data`.

### 3. Structural work the prompt asks for, not started

- [ ] Alembic. Schema changes so far ride the existing guarded
      `ALTER TABLE` list in `db.py` (`ebay_user_id`, plus the new
      `ebay_deletion_notices` table via `create_all`). That is consistent with
      the codebase as it stands, but the prompt requires a real migration
      framework before beta.
- [ ] Normalized `ExternalListing` / `MarketplaceOperation` /
      `NotificationInbox` / `SyncCursor` / `DurableJob` tables. Partial
      progress: `ebay_deletion_notices` is the inbox for deletion notices, and
      `remote_shadow` / `conflicts` / `dirty_fields` live on the listing JSON
      rather than in their own tables.
- [ ] A durable `MarketplaceOperation` row written before every external
      write, with unknown-outcome reconciliation. P0-07 fixed the *contract*
      but locking is still process-local; `publish_guard`'s docstring now says
      so honestly instead of claiming a second server-side guard.
- [ ] eBay Sandbox contract tests and Playwright journeys. (The
      conflict-resolution UI is done — see the row above.)

## Release posture

**Still not approved for external beta**, and the reasons have narrowed rather
than gone away.

What is now true: all 8 P0 blockers are closed in code with regression tests
written before each fix; P1-06, P1-07, P1-09 and P1-12 are closed outright;
P1-01, P1-03, P1-04, P1-05, P1-08 and P1-11 are partly closed (see the table
above for exactly which halves); P2-03 and P2-07 are closed.

What still blocks it:

1. **Nothing here has been exercised against the eBay Sandbox.** Every eBay
   contract in this branch was checked against eBay's published documentation
   and pinned with XML fixture tests, which is the strongest thing available
   in this environment — and is not the same as a real call. The contract
   tests are the readiness step the audit asks for and they have not been run.
2. **The session-id migration has not been run** (see the deploy-time list).
3. **P1-02 and P1-10 are untouched**: jobs still run from process-local
   threads (the WORK is durable now; the runner is not), and the data model
   is still one JSON document per listing with unbounded list responses.
4. **No Alembic, and the container still runs as root** — the latter needs one
   local `docker build && docker run`, which this environment cannot do.
5. **P1-05's legal half is outstanding**: counsel review, a retention
   schedule, a legal identity and address, and a support address on a company
   domain rather than a personal Gmail. None of that is a code change.

A fair summary for whoever picks this up: the *silent-failure* class the audit
was really about — an outcome reported as success, a read failure reported as
emptiness, a fee or a commitment entered into without being shown — has been
worked through systematically, in code and in tests. The *infrastructure*
class (durable jobs, migrations, normalized schema, non-root, sandbox
verification) is largely still ahead.
