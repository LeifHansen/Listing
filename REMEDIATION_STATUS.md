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

Currently **899 passed** on that subset, Ruff clean.

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

### Two traps already hit and fixed — do not reintroduce

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

### 2. P1 items verified as still present (evidence gathered, not yet fixed)

- [ ] **P1-06 — Promoted Listings defaults to on.**
      `backend/marketplaces/ebay_provider.py` `auto_promote_enabled`: returns
      `True` when the preference is absent *and* when the prefs read raises.
      This enrolls sellers in a fee-bearing programme (10% default ad rate)
      without consent, and treats an outage as consent. Fix: default off,
      require explicit durable opt-in, record consent version/actor/timestamp,
      and make a read failure block promotion rather than enable it.
      **Note this reverses a deliberate product choice** (the docstring says
      sellers asked for on-by-default), so flag it to the owner rather than
      changing it silently. The outage path is indefensible either way.
- [ ] **P1-09 — operator diagnostics leak; dry run reports success.**
      Anonymous `GET /api/health` returns 26 diagnostic keys including the
      build SHA, unset env-var *names*, R2 bucket name, raw R2/DB exception
      text (which embeds the Neon host and role), free disk, and Stripe mode.
      Separately, an unconnected user can "publish" and receive `ok: true`
      with `dry_run`, the raw Trading XML, and a server filesystem path. Fix:
      minimal public liveness endpoint, diagnostics behind admin auth, and a
      production live publish that fails with "Connect eBay" instead of
      succeeding.
- [ ] **P1-12 — `GET /api/listings/{id}` performs writes.** For an
      eBay-sourced listing it downloads up to 24 photos inline, writes up to
      48 files plus a manifest, starts an R2 upload thread, and writes the DB
      row — on a plain read. `backend/storage.py` states the rule this
      violates. Fix: side-effect-free GET, remote photos read-only, and an
      explicit "Prepare for editing" job.
- [ ] P1-01 whole-store sync is N+1, automatic per browser session, and
      quota-unsafe (2,500 GetItem calls against a 5,000/day allowance).
- [ ] P1-02 jobs/locks/cooldowns are process-local and non-resumable.
- [ ] P1-05 user-initiated deletion is not durable and the privacy policy
      overpromises (also: Stripe is undisclosed).
- [ ] P1-07 settings combine partial operations and hide remote uncertainty.
- [ ] P1-08 auth baseline: 30-day irrevocable JWTs, no reset/verify/MFA, no
      security headers, process-local rate limiting.
- [ ] P1-11 deploy gates only on the lightweight backend job; actions are not
      SHA-pinned; `create_all` instead of Alembic; container runs as root.

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
