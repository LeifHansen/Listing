# Smart List — design

**Status:** proposal, not yet built. Nothing in this document is wired up;
`SMART_LIST_ENABLED` does not exist yet. This is the plan for adding it.

Smart List takes the drafts a seller has finished and posts them to eBay for
them — one at a time, spaced out over days, at the hours buyers are actually
browsing — instead of all forty going live at once at 2am when the seller
finally finished photographing. The seller keeps every draft in the app until
the moment it fires, can pull one back or move it at any time, and is told
what went live and what could not.

The two goals in the request, and what each one turns into:

| Goal | What it means in practice |
| --- | --- |
| **Maximum visibility** | Post when the marketplace's buyers are online, so the hours a new listing spends at the top of "newly listed" and inside Best Match's fresh-listing window overlap with the most eyeballs rather than the fewest. |
| **Help Best Match** | eBay does not publish its ranking, but it does say what it rewards: complete listings, competitive prices, and *engagement* (views, watchers, sales relative to impressions). A listing that gets its first views and watchers in its first hours starts its life with a better engagement ratio than one that sat unseen for twelve of them. Spacing listings out also stops a seller's own forty listings from competing with each other for the same window. And the readiness gate below refuses to post a listing that would go up with blank item specifics — the other thing eBay says it demotes. |

What Smart List deliberately does **not** claim: that it can "game" Best Match.
Every user-facing sentence should say *posted when buyers are browsing* and
*spaced out so each listing gets its own new-listing window* — never
*boosts your ranking*. The learning loop in §8 is what lets the app say
something stronger later, with the seller's own numbers behind it.

---

## 1. What we know about timing, and how sure we are

Three tiers of evidence, and the design leans on them in this order.

**eBay says (official).** Best Match weighs relevance (title keywords, item
specifics, category) and quality (price and shipping competitiveness, seller
performance, listing completeness, photos) and rewards listings that convert.
Newly listed items get a "fresh" period in Best Match and are what the
*Newly Listed* sort shows. Item specifics completeness is called out as a
ranking input. eBay also lets a listing be **scheduled** up to three weeks
ahead (Trading API `Item.ScheduleTime`, ceiling in
`SchedulingInfoType.MaxScheduledMinutes`); the $0.10 scheduling fee was dropped
in eBay's 2023 winter update for every format except Classified Ad. Fees on a
scheduled listing are charged when it starts, not when it is submitted.
Sources: eBay's *Optimising listings for Best Match* help page, *Listing
durations and timings*, the Trading API `AddItem`/`AddFixedPriceItem` reference
and KB 844 (*Retrieving Scheduled Item*), and community threads confirming the
fee removal. (developer.ebay.com and ebay.com are not reachable from the
sandbox this was written in; the fee status should be re-checked against the
current *Selling fees* page before the feature copy mentions it.)

**Sellers and tooling vendors report (consistent, unofficial).** For a US
audience the peak windows are weekday evenings 6–10pm Eastern and Sunday
evening 7–11pm Eastern, with Thursday evening the second-best day and Saturday
the weakest. Listing *daily* rather than in bursts is widely held to keep a
store surfacing. These are traffic curves, not eBay statements, and they vary
by category: business and industrial supplies sell in weekday working hours;
toys, games and clothing peak evenings and Sundays; collectibles and auctions
concentrate on Sunday evening.

**This app can measure (per seller, once built).** `services/metrics.py`
already pulls impressions, views and watchers per listing from eBay's traffic
report and `GetMyeBaySelling`. Today that is a 90-day *total* per listing —
no time series, nothing persisted — so the app cannot currently say which hour
worked. §8 adds the snapshots that turn the tier above into the seller's own
data.

The planner therefore ships with a **prior** built from the second tier, per
marketplace and per category family, and **replaces it with the seller's own
outcomes** as they accumulate. It never has to be right about eBay's
internals; it has to be right about which of a seller's own posting times got
more views, which is something it can check.

---

## 2. Principles inherited from the rest of the app

These are not new; they are the rules the existing publish path and sweeps
already follow, restated because a scheduler is a new way to break them.

1. **Drafts stay in the app and never reach eBay until they are published.**
   (README, *Marketplaces*.) Smart List holds the draft locally and calls the
   same publish path at fire time. It does **not** hand eBay a `ScheduleTime`
   in v1 — see §11 for why, and for the case where that changes.
2. **One listing, one live listing.** Every fire goes through
   `publish_guard.session_lock`, sends `publish_guard.idempotency_key` as the
   eBay `UUID`/`SKU`, and decides create-vs-revise from
   `publish_guard.stored_item_id` on the *server's* record. A scheduler that
   cached anything about the listing is the exact bug those three exist to
   stop.
3. **An unknown outcome is its own answer.** `PublishOutcome.outcome_unknown`
   means eBay may have minted a listing we cannot point at. The queue never
   refires it; the next sync's SKU match reclaims it
   (`test_lost_publish_is_reclaimed.py`).
4. **A spend nobody wrote down cannot be rationed.** `sync_guard.sweep_due`
   writes its mark *before* the work, in the database, fail-closed. The queue
   claims a row the same way before it publishes (§6.2).
5. **The daily Trading allowance is app-wide.** Exhaust it and
   `AddFixedPriceItem` fails for every seller, including the one pressing
   Publish by hand. Smart List runs on a budget (§6.5) and stops the moment
   eBay says *slow down*.
6. **A token blip is not consent to dry-run.** `creds_for(uid)` returns `None`
   for both "not connected" and "refresh failed"; `has_stored_connection`
   tells them apart, and a connected seller's scheduled publish is *deferred*,
   never quietly downgraded.
7. **Scope is never implicit; a listing that cannot take the change is skipped
   with a reason, not failed.** (`services/bulk_actions.py`.) Queueing many
   drafts reports per listing.
8. **Server-owned fields are restored before any publish**
   (`main._restore_server_state`). The scheduler replicates the four things
   `POST /api/publish` does before calling the provider, by sharing the code
   rather than copying it (§6.3).

---

## 3. The seller's experience

### Switching it on — Settings → Smart List

A new section in `SettingsView.jsx`, same shape as *Allow offers*
(`SettingsView.jsx:404-437`): a `SectionHeader` with an `InfoTip`, then
`Toggle`s and `Select`s inside `Field`s, saved through the existing
`prefs`/`setPref`/`saveSections` plumbing.

| Control | Pref key | Default | Notes |
| --- | --- | --- | --- |
| **Smart List** on/off | `smart_list_enabled` | off | Master switch. Off pauses the queue; nothing is removed from it. |
| **Mode** | `smart_list_mode` | `auto` | `auto` — every draft that passes the readiness gate (§5) joins the queue by itself once it has sat unedited for the settle window. `manual` — only drafts the seller adds. |
| **Post during** | `smart_list_plan` | `evenings` | `evenings` (weekday 6–10pm, Sunday 6–11pm, market time) · `daytime` (Mon–Fri 9am–5pm) · `spread` (7am–11pm every day, even) · `custom` |
| Custom window | `smart_list_window_start` / `_end` | `18:00` / `22:00` | `HH:MM`, market time. Only read when plan is `custom`. |
| Days | `smart_list_days` | `1111111` | Seven flags, Monday first. |
| **Listings per day** | `smart_list_max_per_day` | 5 | 1–50 |
| **Minimum gap** | `smart_list_min_gap_minutes` | 20 | 5–240. Also the planner's slot granularity. |
| Time zone | `smart_list_timezone` | browser's | IANA name (`zoneinfo` validates). Used to *display* slots; windows are in **market** time (§4.1). The client sends `Intl.DateTimeFormat().resolvedOptions().timeZone` when the section is first saved. |
| Auctions end at the peak | `smart_list_auction_align` | on | An auction's start is chosen so its *end* lands in the Sunday-evening peak (§4.3). |
| Quality bar | `smart_list_quality_bar` | on | Beyond the publish preflight: ≥3 photos, a description, and item specifics filled or `enriched_at` set (§5). |

Every key goes into `main._PREF_FIELDS` (`main.py:3374`) with the same clamp
and whitelist treatment `pricing_strategy` gets. `prefs` is a JSON column, so
this part needs no migration.

Under the controls, a **preview** of the next seven days: "Mon 7:12pm · Tue
7:40pm · …" computed by the planner from the current settings and the current
queue, so the seller sees what the settings mean before saving.

### Adding drafts

- **Per draft** — a *Smart List* action (clock icon) in the draft card's
  action row beside *Publish* (`DraftsStrip.jsx:483-500`), and a third button
  in the editor's publish card beside *Publish* and *Save draft*
  (`PublishCard.jsx:499-524`). Both call `POST /api/smart-list/queue` with
  one id. The toast says the slot and the position: *Queued for Thu 7:12pm ·
  3 ahead of it.*
- **Selected drafts** — a *Smart List selected (n)* button in the drafts
  strip's select-mode bar (`DraftsStrip.jsx:392-427`), mirroring
  `publishSelected`. The response reports per draft, in the `bulk_actions`
  shape: *queued 9 · 3 need attention* with the reasons listed.
- **Automatically** — in `auto` mode, the tick enqueues any draft that passes
  the gate and whose `updated_at` is older than `SMART_LIST_SETTLE_MINUTES`
  (default 20). A draft the seller is still typing into is never picked up
  mid-edit, and a draft they open again after it was queued is re-checked at
  fire time (§6.3, step 4).

### Seeing the queue

- **Card chip** — `SmartListChip` next to the status badge on a queued draft
  (pattern: `OfferChip`, `ListingCard.jsx:90`), reading *Thu 7:12pm* with a
  tooltip that gives the reason (*Sunday evening is your best-viewed slot* /
  *weekday evening, spaced 40 min after the previous one*). A held draft's
  chip is amber and reads *Needs attention*; the existing `needsInfo` card
  treatment applies. `CTA` (`ListingCard.jsx:192`) gets a `queued` entry
  (*Change time*) and `STATUS_META` (`badges.jsx:83`) is **not** touched —
  a queued draft's status is still `draft` (§6.1).
- **Dashboard card** — *Smart List* beside *Suggested actions*
  (`Dashboard.jsx:1057`), using the `RecGroup` collapse: *Next up · Thu
  7:12pm · "Nike Air Max 90…"*, today's remaining slots, *N queued, done by
  Sat 14 Sep*, then a *Needs attention* group with one-tap *Open* per row,
  and per-row *List now* / *Remove* / *Move…*. A *Pause* toggle mirrors the
  Settings switch. Hidden when the feature is off and nothing is queued.
- **Drafts strip filter** — a *Scheduled* pill alongside the existing draft
  filters so a seller can see just the queue.
- **Bell** — one notification per outcome (§9): *Went live: …* and *Couldn't
  list: … — reason*.

### Moving and pulling back

*Move…* opens a small dialog: the planner's next five suggestions as buttons,
and a date-time field for anything else (≥5 minutes out, ≤21 days). A chosen
time is **pinned** and the planner will not move it; *Let Smart List choose*
unpins. *Remove* takes the draft out and leaves it a draft. *List now* fires
it on the next tick (within a minute) through the same path.

### Mobile

Nothing native. The Capacitor shell bundles the same React build, so the
section, chips and card ship to iOS with the next `ios-prepare.sh` build. The
Dashboard card sits above the fixed bottom nav like the others
(`pb-28 md:pb-10`).

---

## 4. Choosing the time — the planner

`services/smart_list/planner.py`. Pure: takes the seller's settings, their
queue, the prior table, any learned weights, and *now*; returns a slot per
unpinned queued listing plus a one-line reason for each. No I/O, so it is
testable the way `recommender.py` and `preflight.py` are.

### 4.1 Market time, not seller time

Buyers on eBay US are spread over four time zones, and it is *their* evening
that matters. Windows are therefore expressed in a **reference zone per
marketplace**, keyed off `config.EBAY_MARKETPLACE_ID` exactly as
`ebay_trading._site_id()` is:

| Marketplace | Reference zone |
| --- | --- |
| `EBAY_US` | `America/New_York` |
| `EBAY_CA` | `America/Toronto` |
| `EBAY_GB` | `Europe/London` |
| `EBAY_DE` | `Europe/Berlin` |
| `EBAY_AU` | `Australia/Sydney` |

The seller's own zone is only for rendering ("7:12pm" in their clock) — and
the app stores no timezone anywhere today, so `smart_list_timezone` is new.
All stored instants stay UTC-aware, per `test_a_stored_timestamp_carries_its_timezone.py`.

### 4.2 Scoring a slot

Candidate slots are every `min_gap` minutes inside the seller's windows over
the next `SMART_LIST_MAX_HORIZON_DAYS` (21). Each is scored:

```
score(slot) = prior[dow][hour]            # market curve, §1 tier 2
            × category[family][daypart]   # small table, defaults to 1.0
            × learned[bucket]             # seller's own multiplier, §8; 1.0 until learned
            × crowd_penalty(slot)         # 0.6 if another of this seller's listings
                                          #   is within 2 h; nothing should share
                                          #   its new-listing window with a sibling
```

`prior` is a 7×24 table per marketplace in `planner.py`, with Sunday evening
the highest, weekday evenings next, Thursday above the other weekdays, small
hours near zero. `category[family]` is a handful of coarse families derived
from the listing's eBay category root (Business & Industrial → daytime;
Toys/Video Games/Clothing → evenings and Sunday; everything else 1.0). Both
tables are data, not code paths, so a later "learn the prior from all
sellers" job can overwrite them.

### 4.3 Assigning listings to slots

Deterministic, so re-planning does not shuffle a queue the seller has just
looked at:

1. Queued listings in **queue order** (oldest `queued_at` first). Pinned ones
   are placed first and hold their slot.
2. For each, take the highest-scoring slot that is ≥ `now + 5 min`, respects
   the per-day cap and the gap to every already-assigned slot, and is not
   already taken.
3. **Auctions** (`listing_format` `AUCTION`/`AUCTION_BIN`, `auction_align`
   on): the slot is chosen so that `slot + auction_duration` lands in the
   Sunday 6–10pm market window — because for an auction it is the *end* that
   draws the crowd. A 7-day auction therefore starts Sunday evening; a 3-day
   one starts Thursday evening. If no such start fits inside the horizon, fall
   back to the ordinary rule and say so in the reason.
4. **Jitter**: ±0–`SMART_LIST_JITTER_MINUTES` (7) added per slot, seeded from
   the listing id so it is stable across re-plans. It keeps every seller of
   the app from firing at 19:00:00 sharp (one Trading window, §6.5) and keeps
   a store from looking like a cron job.
5. **Drip**: if the queue is longer than `days × max_per_day` within the
   horizon, the remainder is left **unslotted** (`slot_at = NULL`, state
   `queued`) and the card says *waiting for a slot — raise "Listings per day"
   to finish sooner*. They are slotted as days roll over.

The reason string comes from whichever factor dominated: *Sunday evening —
the busiest hour on eBay*, *weekday evening, 40 min after your previous
listing*, *auction timed to end Sunday 8pm*, *your best-viewed slot so far*.

Re-planning happens whenever settings change, a listing is added or removed,
or a day rolls over — unpinned future rows only. A row within 15 minutes of
firing is never moved.

---

## 5. What "completed" means — the readiness gate

The request says *completed* drafts. The app already has an authority on
that: `preflight.validate` (`services/preflight.py:145`) via
`ebay_provider.preflight_issues(uid, listing, "live")`, which is uid-only and
needs no request. Smart List **reuses it and adds nothing to it**, then
layers an optional quality bar on top.

A draft is **eligible** when all of these hold:

| Check | Source | On failure |
| --- | --- | --- |
| Record status is `draft` or `unlisted` | `ListingRecord.status` | never eligible (`sold` is terminal — `POST /api/publish` answers 409; `published`/`ended` are not drafts) |
| Owner is the seller | `user_id` | never eligible |
| Not a variation listing | `listing.has_variations` | held: *variation listings can't be published from here yet* |
| Preflight `live` has **no blocking issue** | `preflight_issues(uid, listing, "live")` → `errors_only` | held, with the preflight `fix` strings as the reason, exactly as the editor shows them |
| eBay connected | `has_stored_connection(uid)` | held: *Connect eBay to use Smart List* |
| Unedited for the settle window | `updated_at < now − SETTLE_MINUTES` | not yet — skipped this tick, no row written |
| **Quality bar** (when on) | ≥3 photos (`FEW_PHOTOS` in `recommender.py`), non-blank description, and (`filled_specifics ≥ MIN_SPECIFICS` or `enriched_at` set) | held: *Add photos / fill in details, or turn off the quality bar* — with an *Enrich* shortcut that runs the existing `POST /api/enrich/{id}` |

Two things are **deliberately not** in the gate:

- `missing_info == []`. Those notes are what the AI declined to invent; the
  model docstring (`models.py:170-184`) explains at length why they are not a
  publish gate. Using them would make Smart List a permanent no-op for
  exactly the listings that need it most.
- `promote`. A Smart List fire inherits `auto_promote_enabled(uid)` the same
  way a manual publish does, because the provider applies it inside
  `_publish_locked`. That is the seller's standing decision and Smart List
  neither adds nor launders consent — but the queue card **says** *Promoted
  Listings: on · 10%* when it is, so the spend is visible before it happens.

The gate runs at **enqueue** (so the seller hears *needs attention* at once)
and again at **fire time** (§6.3), because a week can pass in between.

---

## 6. Firing — the scheduler

### 6.1 Where the schedule lives

A new table, `smart_list_queue`, one row per queued listing. Not a field in
`listings.data` (unqueryable — every tick would scan every draft), and not a
new `status` value (`derive_top_status` treats statuses as a lifecycle and a
save would demote `scheduled` to `draft`; adding it to `STICKY_STATUSES` would
make it un-demotable, which is worse).

```python
class SmartListQueue(Base):
    __tablename__ = "smart_list_queue"
    __table_args__ = (
        Index("ix_smart_list_due", "state", "slot_at"),
        Index("ix_smart_list_user", "user_id", "state"),
    )
    listing_id:  Mapped[str]  = mapped_column(String(64), primary_key=True)
    user_id:     Mapped[str]  = mapped_column(String(64))
    state:       Mapped[str]  = mapped_column(String(16), default="queued")
                 # queued | firing | published | held | unknown | cancelled
    slot_at:     Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    pinned:      Mapped[bool] = mapped_column(Boolean, default=False)
    slot_reason: Mapped[str]  = mapped_column(String(255), default="")
    claimed_at:  Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fired_at:    Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    attempts:    Mapped[int]  = mapped_column(Integer, default=0)
    hold_reason: Mapped[str]  = mapped_column(String(512), default="")
    hold_issues: Mapped[Optional[dict]] = mapped_column(JSON)   # preflight issues, for the card
    queued_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at:  Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

No foreign keys, like every other table here. Deleting the listing deletes
the row (`db.delete_listing` gains one statement); account deletion
(`POST /api/account/delete`) and eBay's marketplace-deletion notice both drop
the user's rows alongside the listings they already remove.

Schema change checklist (the agree-test is strict): the model in `db.py`, a
`CREATE TABLE IF NOT EXISTS` in `db._MIGRATIONS`, and
`alembic revision --autogenerate`, committed unedited.

The listing row itself is untouched until the fire: status stays `draft`, so
every existing predicate (`isDraft`, tabs, the *finish* nudge) keeps working,
and the queue is joined in by id on the client the way metrics already are
(`_metrics_by_record_id`).

### 6.2 The tick

`services/smart_list/runner.py`, a daemon thread started from
`main._warm_models` next to `_reclaim_loop` (`main.py:698`), same
`while True: try … except … sleep` shape, `SMART_LIST_TICK_SECONDS` (60)
apart, and a no-op when the flag is off or `db.enabled()` is false — so
local dev and the test suite never grow a thread that races them.

```
tick():
  if global_backoff_until > now: return            # eBay said slow down (§6.5)
  if auto mode sellers exist: enqueue_ready_drafts()   # §5, bounded per tick
  replan_rolled_over_days()
  for row in db.smart_list_due(now, limit=SMART_LIST_TICK_CAP):   # queued, slot_at <= now
      if not claim(row): continue                  # CAS, §6.2 below
      fire(row)                                    # §6.3, serial
  snapshot_outcomes_due()                          # §8, at most one pass per account per day
```

**Claiming** is the durable, fail-closed mark:

```sql
UPDATE smart_list_queue
   SET state = 'firing', claimed_at = :now, attempts = attempts + 1
 WHERE listing_id = :id AND state = 'queued'
```

Row count 1 means this process owns the fire. Anything else — a second
process, a restart that re-adopted, an unreadable database — means *no*.
This is `sync_guard.sweep_due` for a single listing, and it is correct with
one machine (today, `min_machines_running = 1`, `auto_stop_machines = 'off'`)
and still correct if there are ever two.

Fires are **serial** and capped at `SMART_LIST_TICK_CAP` (3) per tick: a
publish uploads every photo and takes tens of seconds, and three per minute
app-wide is already far more than the per-seller gaps allow.

### 6.3 One fire

The body of `POST /api/publish` (`main.py:7966`) does four things before it
calls the provider that a scheduler must not skip: the ownership check, the
`sold` refusal, `_restore_server_state`, and `storage.save_listing`. Rather
than copy them, the PR extracts `main._publish_listing(uid, session_id,
listing, mode, marketplaces, base_url) -> dict` and has both the route and
the runner call it. That is the one refactor of existing code this feature
needs, and it is the same seam `_run_enrich_job` (`main.py:7378`) already
publishes through from a thread.

```
fire(row):
  1. rec = db.get_listing(row.listing_id)      # fresh; never the enqueue-time copy
     no rec, or rec.user_id != row.user_id      → cancelled, "listing no longer exists"
     rec.status in (sold, published, live, ended) → cancelled, "already live" / "sold" / "ended"
  2. listing = Listing(**rec["listing"])        # same shape main.py:9200 uses
     rec.updated_at within SETTLE_MINUTES       → back to queued, slot = next free slot,
                                                   reason "you were editing it"
  3. gate(uid, listing) (§5)                    → held with the issues; notify
  4. creds = creds_for(uid)
     None and not has_stored_connection(uid)    → held, "Connect eBay"
     None and has_stored_connection(uid)        → queued, slot = now + 15 min, attempts kept;
                                                   after 3: held, "eBay token could not be
                                                   refreshed — reconnect eBay"   (never dry-run)
  5. budget_ok() (§6.5)                         → else queued, slot = next slot tomorrow
  6. outcome = _publish_listing(uid, id, listing, "live", ["ebay"],
                                base_url=config.SMART_LIST_PUBLIC_ORIGIN)
     wrapped: HTTPException / RateLimited / Unreachable / anything
  7. outcome.ok                                  → published, fired_at; notify "Went live"
     outcome.outcome_unknown                     → unknown; notify "We can't tell whether this
                                                   went live; the next sync will check";
                                                   NEVER refired
     RateLimited                                 → queued, slot = now + (retry_after or 300);
                                                   set global_backoff_until; stop the tick
     Unreachable (nothing was sent)              → queued, slot = now + 10 min; after 3: held
     refused (ok=False, issues/message)          → held with eBay's own message, the way the
                                                   editor shows it (ebay_errors names the field)
```

`base_url` has no request to come from. `SMART_LIST_PUBLIC_ORIGIN` defaults to
`APP_ORIGINS[0]` and must be a public origin, because eBay fetches the photo
URLs at publish time; a dead URL is the opaque 25001 the `fly.toml` mount
comment warns about. `config_warnings()` reports it when it is unset while the
flag is on.

### 6.4 Restarts

Deploys restart this process several times on a busy day. Rows in `queued`
need nothing — they are in the database. A row in `firing` whose `claimed_at`
is older than `SMART_LIST_CLAIM_TTL_SECONDS` (900) is one the previous
process died holding, and it **may have sent `AddFixedPriceItem`**. At
startup (and on every tick, for the case where the process wedged rather
than died) such rows go to `unknown`, not back to `queued`, with the same
notification as an unknown outcome. The next sync's SKU match
(`idempotency_key` → `Item.SKU`) is what settles them, exactly as it settles
a lost manual publish; if it finds nothing live, the row is offered back to
the seller as *Retry* on the card rather than refired by the machine.

### 6.5 Budget

Two limits, one per seller and one for the app.

- **Per seller:** `max_per_day` and `min_gap` are enforced by the planner and
  re-checked at fire time against `fired_at` counts for the day, so a
  re-plan cannot slip an extra one through.
- **App-wide:** `SMART_LIST_DAILY_PUBLISH_CAP` (300) fires per UTC day across
  all sellers, counted from `fired_at`. Past it, rows defer to tomorrow and
  the admin diagnostics say so. The number is set against the ~5,000/day
  default Trading allowance that `ratelimit.py` documents, leaving most of
  it for the sweeps and for sellers pressing Publish themselves — a scheduled
  listing that waits a day is a nuisance; a manual one that fails is a bug
  report.
- **Backoff:** any `RateLimited` from any fire sets a process-wide
  `global_backoff_until = now + (retry_after or 300)` and ends the tick.
  `test_ebay_rate_limit.py` is explicit that extra calls inside the window
  hold it open.

---

## 7. API

All routes require a login, check ownership through `_assert_session_owner`
for anything that names a listing, and land in
`test_every_scoped_route_checks_the_owner.py`. Per-listing results follow
`bulk_actions.BulkResult` (`changed` / `skipped` / `failed`, each with a
reason).

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/smart-list` | The seller's queue: rows with `slot_at`, `state`, `slot_reason`, `hold_reason`, `hold_issues`, `pinned`; `summary` (`queued`, `held`, `published_7d`, `next_slot_at`, `done_by`); `preview` (the next 7 days' slots for the Settings preview). Never raises — an unreadable queue answers `{"ok": false, "error": …}` the way `/api/insights` does. |
| `POST` | `/api/smart-list/queue` | `{listing_ids: [...]}` → per-id `queued` / `skipped` (with gate issues). Capped at `SMART_LIST_SELECT_CAP` (100), remainder `deferred`. |
| `POST` | `/api/smart-list/remove` | `{listing_ids}` → rows deleted; listings stay drafts. |
| `POST` | `/api/smart-list/move` | `{listing_id, slot_at}` pins (≥ now+5 min, ≤ 21 days, inside a day the seller allows); `{listing_id, slot_at: null}` unpins and re-plans. |
| `POST` | `/api/smart-list/list-now` | `{listing_id}` → `slot_at = now`, `pinned = true`; the next tick fires it. Answers the row, not the outcome — the card polls `/api/smart-list` the way `pollJob` polls a batch. |
| `POST` | `/api/smart-list/retry` | `{listing_id}` on a `held`/`unknown` row → re-gates and re-queues. |
| `GET/POST` | `/api/prefs` | Existing; new keys from §3. Saving any `smart_list_*` key re-plans. |
| `GET` | `/api/health` | Gains `smart_list: bool` beside the other capability flags, so the UI hides the feature where the operator has it off. |
| `GET` | `/api/admin/diagnostics` | Gains a `smart_list` block: queue depth by state, fires today vs cap, `global_backoff_until`, last tick time and duration, rows adopted as `unknown` at the last restart. |

The `finish` recommendation (`recommender.py:139`) is suppressed for a queued
listing: `recommendations()` takes a `queued_ids` set and skips the rule, on
the same reasoning `VERIFY_QUIET_DAYS` records — the seller should not be
nagged about work they have just handed to the app.

---

## 8. Learning — the part that earns the name

v1 ships the prior; v1 also **captures what happened**, so that v1.1 can
replace the prior with the seller's own curve without a second schema change.

### 8.1 Outcomes table

```python
class SmartListOutcome(Base):
    __tablename__ = "smart_list_outcomes"
    __table_args__ = (Index("ix_smart_list_outcomes_user", "user_id", "fired_at"),)
    listing_id:   Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id:      Mapped[str] = mapped_column(String(64))
    marketplace:  Mapped[str] = mapped_column(String(16))      # EBAY_US …
    category_id:  Mapped[str] = mapped_column(String(32), default="")
    listing_format: Mapped[str] = mapped_column(String(16))
    price:        Mapped[Optional[float]]
    fired_at:     Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bucket:       Mapped[str] = mapped_column(String(16))      # e.g. "sun-evening", §8.2
    chosen_by:    Mapped[str] = mapped_column(String(16))      # prior | learned | explore | pinned | manual
    imp_24h / views_24h / watchers_24h:   Optional[int]
    imp_72h / views_72h / watchers_72h:   Optional[int]
    sold_within_7d: Mapped[Optional[bool]]
    snapshot_24h_at / snapshot_72h_at / snapshot_7d_at: Optional[datetime]
```

`chosen_by = "manual"` rows are the control group: every **manual** publish
the seller makes while Smart List is on also gets an outcome row (written by
`_publish_listing` when the flag is on), so the app can compare *Smart List
listings got 2.3× the first-day views of the ones you posted yourself* —
which is the only honest sentence about "helping the algorithm" the app will
ever be able to say.

### 8.2 Snapshots

`snapshot_outcomes_due()` runs inside the tick, **at most once per account
per day**, and covers every row of that account with a snapshot due
(24 h, 72 h, 7 d after `fired_at`, ±1 h). It calls the existing
`metrics.listing_metrics(creds, ids)` — one traffic-report pass per 200 ids,
one `GetMyeBaySelling` walk for watchers — so the cost is one small sweep per
active account per day, on the Sell Analytics allowance rather than Trading.
A failed lookup leaves the columns `NULL`, never `0`: the *measured nought is
a nought* rule from the README applies here too, and a `NULL` row is simply
excluded from learning.

`sold_within_7d` comes from the listing record itself (`sold_at`), which the
sync already stamps.

Buckets are coarse on purpose — 3 day types × 5 dayparts = 15, not 168 hours —
so a seller with thirty outcomes has a curve rather than noise:

```
day type: weekday | saturday | sunday
daypart:  morning 6–11 | midday 11–14 | afternoon 14–18 | evening 18–22 | late 22–6
```

### 8.3 Using them (v1.1)

Per seller, per bucket, the metric is `views_24h` divided by the seller's own
median `views_24h` (so a seller of $5 pins and a seller of $500 jackets learn
on the same scale). With ≥ `SMART_LIST_MIN_OUTCOMES` (20) rows the planner's
`learned[bucket]` becomes a Thompson-sampled multiplier from a Beta posterior
over "above-median or not", clipped to `[0.5, 2.0]` so one lucky Sunday cannot
own the calendar. `SMART_LIST_EXPLORE_RATE` (0.15) of slots are drawn from the
prior instead and tagged `explore`, so the curve keeps being tested. Below the
threshold the prior stands and the card says *learning from your first N
listings*.

A cold-start improvement that needs no per-seller data: an aggregate per
marketplace across all sellers' outcome rows, computed by a weekly pass and
written into the prior table. It is counts by bucket — nothing that
identifies a seller — and is worth doing once there are a few thousand rows.

### 8.4 What the seller sees

`GET /api/smart-list/insights` → the seller's bucket table with sample sizes
and the manual-vs-Smart-List comparison; a small heat strip on the Dashboard
card (`dataviz` conventions, 15 cells) once there are ≥ 20 rows. Until then,
nothing — a chart of three points is a lie with axes.

---

## 9. Notifications

`db.add_notification` (`db.py:2320`) with a new `kind = "smart_list"`; the
bell needs no change beyond rendering the kind. Dedupe keys make every path
restart-safe for free:

| Event | Title | Dedupe key |
| --- | --- | --- |
| Fired OK | *Went live: {title}* | `smartlist:live:{listing_id}` |
| Held | *Couldn't list: {title}* — body is the reason | `smartlist:held:{listing_id}:{attempts}` |
| Unknown | *We can't tell whether {title} went live* — body says the next sync will check | `smartlist:unknown:{listing_id}` |
| Reconnect needed | *Smart List is paused — reconnect eBay* | `smartlist:reconnect:{uid}:{date}` |
| Budget deferral (app-wide) | none to the seller; the card's slot simply moves and the admin block says why | — |

A seller with a 20-a-day plan would get 20 bell rows a day, which is the
inbox equivalent of the pile of ended cards the README describes. When more
than `SMART_LIST_DIGEST_AFTER` (3) *Went live* rows land for one seller inside
an hour, later ones collapse into one *N listings went live this evening* row
keyed `smartlist:digest:{uid}:{yyyy-mm-dd-hh}`, with the ids in `data`.

No email or push exists in this repo; when it does, `kind = "smart_list"` is
already the hook.

---

## 10. Configuration

`backend/config.py`, in the established shapes (`_ENABLED` flags read
`in ("1","true","yes","on")`; numbers through `env_float` so a bad value logs
and takes the default instead of crashing boot). All documented in
`.env.example` under a new `# ---- Smart List ----` banner.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SMART_LIST_ENABLED` | off | The feature. Off: no thread, routes answer 404, UI hidden via `/api/health`. |
| `SMART_LIST_TICK_SECONDS` | 60 | Loop interval. |
| `SMART_LIST_TICK_CAP` | 3 | Fires per tick, serial. |
| `SMART_LIST_CLAIM_TTL_SECONDS` | 900 | A `firing` row older than this was orphaned by a dead process → `unknown`. |
| `SMART_LIST_SETTLE_MINUTES` | 20 | A draft edited more recently is not picked up or fired. |
| `SMART_LIST_DAILY_PUBLISH_CAP` | 300 | App-wide fires per UTC day. |
| `SMART_LIST_MAX_HORIZON_DAYS` | 21 | How far ahead the planner slots (and the pin limit). |
| `SMART_LIST_JITTER_MINUTES` | 7 | Per-slot jitter. |
| `SMART_LIST_SELECT_CAP` | 100 | Ids per queue request. |
| `SMART_LIST_PUBLIC_ORIGIN` | `APP_ORIGINS[0]` | Origin eBay fetches `/media` from at fire time. |
| `SMART_LIST_MIN_OUTCOMES` | 20 | Rows before learned weights apply (v1.1). |
| `SMART_LIST_EXPLORE_RATE` | 0.15 | Share of slots drawn from the prior once learning is on. |
| `SMART_LIST_DIGEST_AFTER` | 3 | Bell rows per hour before collapsing into a digest. |
| `SMART_LIST_OWNER_EMAILS` | unset | Beta roster: when set, only these logins see the feature, the way `ETSY_OWNER_EMAILS` gates Etsy. Unset means every seller. |

`config_warnings()` adds: flag on with no `DATABASE_URL` (the queue has
nowhere to live), flag on with `SMART_LIST_PUBLIC_ORIGIN` unresolvable, and
the `near_miss_env` check for `SMARTLIST_*` typos.

---

## 11. Alternatives considered

**Hand eBay the time (`Item.ScheduleTime`).** eBay would fire the listing
even if this machine were down, and the seller would see it under *Scheduled*
in Seller Hub. Rejected for v1 because it breaks the first principle: the
draft would leave the app the moment it was queued, would need
`ReviseFixedPriceItem` on every later edit and `EndItem` to cancel, would need
the sync to learn `GetMyeBaySelling`'s `ScheduledList` so the store import
does not treat it as missing, and would cap the horizon at three weeks. The
fee is not the reason — it was removed in 2023 outside Classified Ads — but
that should be re-verified before any copy says "free". It stays a good
**v2 hardening option**: the tick could hand eBay a `ScheduleTime` for slots
in the next few hours, so a deploy or outage in the final stretch cannot miss
one. Everything in §6 is designed so that swap is local to step 6.

**A GitHub Actions cron.** The repo already runs its daily triage there.
Rejected: it would need a token-protected "fire what is due" endpoint, could
not share the process's backoff state, and the app already runs daemon
threads for exactly this kind of recurring work.

**`status = "scheduled"` on the listing row.** Rejected above (§6.1): the
lifecycle machinery would demote it on the next save, and every
draft-predicate in the client would need teaching.

**Storing the slot in `listings.data`.** No migration, but the tick would
read every draft in the database every minute to find the due ones.

**Per-hour learning (168 buckets).** Rejected for sparsity; fifteen buckets
give a usable curve from thirty listings.

---

## 12. Rollout

| Phase | Ships | Flag state | Roughly |
| --- | --- | --- | --- |
| **0 — plumbing** | Table + migrations, config, `_publish_listing` extraction, runner with the tick and claim, admin block. No UI. | off | 2–3 days |
| **1 — manual queue** | Planner with the prior, per-draft and selected-drafts queueing, Settings section (manual mode only), chips, Dashboard card, move/remove/list-now, notifications. | on for `SMART_LIST_OWNER_EMAILS` first, the way Etsy's roster gated its beta | 4–6 days |
| **2 — automatic** | `auto` mode with the settle window, quality bar, `finish`-nudge suppression, auction alignment. | on | 2–3 days |
| **3 — learning** | Outcome rows (including manual controls), daily snapshots, insights endpoint, heat strip; learned weights behind `SMART_LIST_MIN_OUTCOMES`. | on | 3–4 days |
| **4 — later** | Etsy/Depop through the same `marketplaces.get(key).publish` seam (the fan-out already isolates failures per provider); optional `ScheduleTime` hand-off for near slots; aggregate prior from all sellers. | — | — |

Each phase is its own PR against the gates in `gates.yml`; nothing in a later
phase is needed for an earlier one to be shippable.

---

## 13. Tests

Named the way this suite names things — after the promise, not the module.
Backend, `backend/tests/`:

- `test_a_scheduled_publish_fires_exactly_once.py` — two ticks racing on one
  due row; one claim wins; one provider call.
- `test_a_draft_being_edited_is_not_fired.py` — `updated_at` inside the settle
  window at fire time → re-slotted, never sent.
- `test_a_queued_draft_that_stopped_being_ready_is_held.py` — passes at
  enqueue, fails preflight at fire → `held` with the preflight issues; no
  provider call.
- `test_an_unknown_outcome_is_never_refired.py` — `outcome_unknown` → state
  `unknown`; a hundred later ticks make no call.
- `test_an_orphaned_claim_is_not_refired.py` — a `firing` row older than the
  TTL at startup → `unknown`, notification written, no call.
- `test_a_token_blip_does_not_dry_run_a_scheduled_publish.py` — `creds_for`
  `None` with a stored connection → deferred; `dry_run` never appears.
- `test_a_rate_limit_stops_the_tick.py` — first fire raises `RateLimited`;
  the second due row is not attempted; `global_backoff_until` honoured.
- `test_no_seller_can_exceed_their_daily_cap.py` — planner and fire-time
  count agree, including across a re-plan.
- `test_smart_list_stays_inside_the_app_budget.py` — the 301st fire of a day
  defers.
- `test_slots_land_inside_the_sellers_windows.py` — market-zone windows,
  including the DST week and a seller in Australia posting to EBAY_US.
- `test_sunday_evening_outranks_tuesday_morning.py` — the prior's ordering
  is what the copy promises.
- `test_an_auction_is_started_so_it_ends_at_the_peak.py` — 7-day and 3-day.
- `test_a_replan_does_not_move_a_pinned_or_imminent_slot.py`.
- `test_a_sold_record_is_never_queued.py` and `…never_fired.py`.
- `test_the_finish_nudge_is_quiet_for_a_queued_draft.py`.
- `test_a_snapshot_that_failed_is_null_not_zero.py`.
- `test_a_manual_publish_is_a_control_row.py`.
- `test_every_scoped_route_checks_the_owner.py` — extended to the new routes.
- `test_the_migrations_and_the_models_agree.py` — passes with the two new
  tables in all three places.
- `test_public_surface.py` / `test_readiness.py` — the flag's `/api/health`
  and diagnostics additions.

Frontend, `frontend/src/`: `SmartListSettings.test.jsx` (every pref key round
trips; the preview updates on change; an unreadable `prefs` renders no
defaults as if saved), `DraftsStrip.smartList.test.jsx` (select bar button,
per-card action, per-id toast tally), `SmartListChip.test.jsx` (queued / held
/ unknown states and the reason tooltip), `Dashboard.smartList.test.jsx`
(card hidden when off and empty; *List now* / *Remove* / *Move* call the right
routes). The app smoke test's screen walk gets the Settings section and the
Dashboard card.

---

## 14. Risks

| Risk | Mitigation |
| --- | --- |
| Two live listings from one draft | The three existing guards, plus claim-before-fire, plus never refiring `unknown`. The one new code path that can create a listing is step 6 of §6.3, and it is the same function the button uses. |
| Photos gone by fire time | Preflight at fire time; `SMART_LIST_PUBLIC_ORIGIN` must be public; the volume mount already outlives restarts. |
| Draft changes between enqueue and fire | Fresh read at fire; settle window; the seller's edit always wins. |
| Quota exhaustion hurting manual publishes | App-wide cap far under the allowance; stop on the first `RateLimited`; snapshots on the Sell Analytics allowance, not Trading. |
| Spend the seller did not expect | Promoted Listings state shown on the card; Smart List never enables `promote`. No AI tokens are spent by a fire; the optional *Enrich* shortcut goes through the existing confirm-the-cost dialog. |
| Sellers expecting a ranking guarantee | Copy rules in the preamble; the insights compare against their own manual publishes and say nothing before twenty rows. |
| A wedged loop | The tick logs its duration; diagnostics show the last tick time; `health-watch.yml` can alarm on a stale one the way it does on build drift. |
| A second machine some day | The claim is a row-level CAS; the only process-local state is the backoff, which is merely conservative when duplicated. |

---

## 15. Decisions taken here, open to change

These are the calls this design makes without a product decision on record.
Each has a default so the build can start; any can be flipped in Settings
copy or a config default without touching the schema.

1. **`auto` is the default mode once the switch is on.** The request says
   *periodically posts completed drafts*, which is automatic; the settle
   window and the gate are what make that safe. `manual` is one select away.
2. **Windows are in market time, not seller time.** Buyers, not sellers,
   decide the peak. Seller time is for display.
3. **Smart List fires honour `auto_promote`.** It is the seller's standing
   choice and the card shows it; overriding it either way would be the app
   making a spend decision.
4. **Five a day, twenty minutes apart.** Drip beats dump; a seller who wants
   more raises the number and sees the *done by* date move.
5. **eBay only in v1.** The seam is marketplace-generic; the planner's prior
   is not, and Etsy's peak curve is a different table.
6. **No `ScheduleTime` in v1**, for the reasons in §11, with the hand-off
   designed in as a later local change.
