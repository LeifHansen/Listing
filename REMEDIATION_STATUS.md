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

### Five traps already hit and fixed — do not reintroduce

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
| A failed store read reported an empty store | *this commit* | `db.list_listings` answered `[]` on any exception, like every read in `db.py`. For this one that is not a survivable degradation: the eBay import matches incoming items against the records it finds and imports whatever it does not, so **one Postgres blip during a sync imported a second copy of the seller's entire eBay store**, reported as a successful sync, leaving real duplicate listings to merge by hand. The same `[]` also let a release pass report "released 0", a status sweep report checking a store it never read, and the session-id migration report nothing to migrate. The read now raises `StorageUnavailable`; `list_listings_best_effort` exists for the three callers where empty genuinely only thins the answer (metrics, the weight pre-fill, the deletion preview's "unknown" path). No database configured is still `[]` — a configuration, not a failure. Client-side had the mirror image: a failed `/api/listings` left the cache at its initial `items: []`, so the store the app could not read rendered as "No listings yet" with a button to create a first listing. |

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

### 2. P1 items — three closed, the rest open

| P1 | Commit | What it was |
| --- | --- | --- |
| P1-06 promotion consent | `97122c7` | Promoted Listings (COST_PER_SALE, 10% default rate) was enabled when the preference was ABSENT and when the prefs read RAISED — so silence and a database outage both counted as agreeing to a fee. Now off unless explicitly on. **This reverses a deliberate product choice** (the old default was on because sellers reported publishes landing unpromoted); the commit message says so. The UI mirrored the old default independently and was changed with it. |
| P1-09 public surface | `e2ca586` | Anonymous `/api/health` returned 26 operator keys including raw DB/R2 exception text (Neon host and role, R2 account id). Moved to `/api/admin/diagnostics` behind `ADMIN_TOKEN`, which **fails closed**. `build` deliberately stays public — deploy.yml, deploy.sh and health-watch.yml all poll it. Also: an unconnected production publish no longer answers `ok: true` with the Trading XML and a server path. |
| P1-01 automatic whole-store import (partial) | `3915997` | Every browser session with a connected account fired a whole-store import (one GetItem per listing, capped at 2,500) AND a concurrent FORCED status sweep — against a default allowance of 5,000 Trading calls a day. A second tab, a phone, a reload and a redeploy each spent it, unasked. The mirror is durable, so showing it costs nothing; an automatic rebuild now runs only on the first load after connecting or once the last one is 6h old, and the forced sweep is reserved for the deliberate "Sync with eBay" press. The REST of P1-01 (GetSellerList/GetSellerEvents instead of N+1 GetItem, notification-driven sync, pooled HTTP, quota budgets, and the "full sync" that samples a random 100) is untouched. |
| P1-01 honest sweep scope | `50c88ca` | The status sweep samples 100 live listings and reported only `checked`, so 100-of-400 and 100-of-100 were indistinguishable — behind a button called "Sync with eBay". It now reports `eligible`, `partial` and `sample_size` too. The sampling itself is right (a sweep is one eBay call per listing); admitting it is what was missing. |
| P0-01 follow-up: 400 not 500 | `2f3e45b` | Making `safe_session_name` reject rather than rewrite left the rejection with nowhere to go — a malformed id raised out of whatever handler touched storage and every route answered 500. Verified against the booted app. Now a 400 with a bare body, handled centrally like `StorageUnavailable`. |
| P1-07 never create-on-unknown (partial) | `9386c08` | "Create my policies" collapsed "eBay says you have none" and "eBay could not be reached" into the same `None`, and created a policy either way — so every timeout minted another "Thryft Shop" policy on the seller's real eBay account, visible in Seller Hub and never cleaned up. The lookups are now three-state and refuse on unknown (503, "try again"). The rest of P1-07 -- splitting Settings into independently saved sections and tri-state loading -- is untouched. (Previewing the terms is the row below.) |
| P1-07 policy terms shown before they are made (partial) | *this commit* | "Create my policies" created three real eBay business policies on the seller's account carrying terms this app chose — dispatch within 2 business days (eBay scores it), returns accepted for 30 days, buyer pays return postage, immediate payment required, domestic-only calculated postage. All of it is published to buyers and binds the seller, and the button said none of it. There is now a static, side-effect-free `GET /api/ebay/policy-preview` derived from the same request bodies the create sends, a dialog that shows them, and both create routes refuse without `accept_terms: true` — strictly `True`, so a stale client or a half-filled form is not consent. The return policy is also named for the window it was actually given (a 14-day policy was called "30-day returns" in Seller Hub). |
| P1-07 Settings sections save and load independently | `326a0c3` | One Save wrote local preferences AND the seller's eBay account inside one `try`, and reported one verdict: when the eBay half failed, a seller whose listing defaults had just committed was told "Couldn't save" — and the obvious response is to type it all again. The two now save independently and the message names which committed. Loading had the mirror-image bug: a failed `/api/prefs` did `setPrefs({})` and a failed policies fetch left the dropdowns empty, so the app rendered its own fallbacks as the seller's saved settings and told them their eBay account had no business policies — on the strength of having failed to ask. Both are now loading / couldn't-ask / answered, and only the last says anything about the account. Two panels that shimmered forever after a failed load now say so and offer a retry. |
| P2-03 unknown-outcome copy | `8df583b` | Every timed-out request said "Nothing was lost — try again", including a publish or a delete that may already have reached eBay. Writes now say the outcome is unknown and to check first, which is the difference between a retry and a duplicate live listing. |
| P1-05 privacy policy accuracy (partial) | `8a41647` | The policy made three claims the code contradicted: that deletion "immediately" removes photos (the media purge runs after the response returns), that it "hands your marketplace authorizations back" (nothing revokes the OAuth grants), and that eBay deletion notices are merely "recorded for audit" keyed on nothing (they are now verified and acted on, keyed on eBay's immutable id). Stripe was also absent from the service-provider list despite processing token purchases. Copy now matches the implementation. **Not legal review** — the audit's ask for counsel review, retention schedule, legal identity and a company-domain contact is untouched. |
| P1-11 deploy gate (partial) | `1521558` | `deploy` gated production on the lightweight lint+unit job ALONE — cutout safety, the frontend build and the smoke test never gated a deploy, and `ci.yml` runs only on `pull_request` so a push to `main` ran none of them. Both now call one shared `gates.yml`. `superfly/flyctl-actions/setup-flyctl` was pinned off `@master` (it runs in the job holding `FLY_API_TOKEN`). **See the two operational consequences below.** |
| P1-08 security headers (partial) | `46b89d3` | None of CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy or Permissions-Policy were sent. Now all present, verified against the booted app. The CSP keeps `'unsafe-inline'` in script-src because index.html has an inline theme script — tightening to a nonce is worth its own change. The REST of P1-08 (revocable sessions, password reset/verify/MFA, distributed rate limiting, fail-closed keys) is untouched. |
| P1-12 side-effect-free GET | `a8d8f0e` | A plain read downloaded up to 24 photos, wrote up to 48 files, started an R2 upload and wrote the DB row. Now `POST /api/listings/{id}/prepare-for-editing`, called by the frontend when the seller opens the editor. |

Still open:

- [ ] P1-01, MINUS the per-session auto-import (done, see above): still N+1, automatic per browser session, and
      quota-unsafe (2,500 GetItem calls against a 5,000/day allowance).
- [ ] P1-02 jobs/locks/cooldowns are process-local and non-resumable.
- [ ] P1-05, MINUS the policy corrections (done, see above): user-initiated
      deletion is still not durable — the media purge is an untracked
      background thread with no completion state — and nothing revokes the
      marketplace OAuth grants. The policy now says so rather than promising
      otherwise, which is the honest interim state, not the fix. Still needs:
      counsel review, a retention schedule, legal identity/address, and a
      company-domain support contact (it is currently a personal Gmail).
- [x] **P1-07 is closed.** create-on-unknown, the unshown policy terms, and
      the shared save/load are all done (see the rows above).
- [ ] P1-08 auth baseline, MINUS the headers (done, `46b89d3`): 30-day
      irrevocable JWTs, no reset/verify/MFA, process-local rate limiting, and
      a CSP still carrying `'unsafe-inline'` for scripts.
- [ ] P1-11, MINUS the deploy gate (done, see above): actions are still pinned
      to version TAGS rather than reviewed commit SHAs (`actions/checkout@v4`,
      `actions/setup-python@v5`, `actions/setup-node@v4`,
      `superfly/flyctl-actions/setup-flyctl@v1.5`); `create_all` instead of
      Alembic; **the container still runs as root**.

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
- [ ] eBay Sandbox contract tests, Playwright journeys, conflict-resolution UI
      (conflicts are recorded on the record but nothing surfaces them yet).

## Release posture

Unchanged from the prompt: **not approved for external beta.** The P0 set is
closed in code with tests, but none of it has been exercised against the eBay
Sandbox, the session-id migration has not been run, and the P1 security,
privacy and quota items remain open.
