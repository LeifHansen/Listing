# List tab redesign — research and plan

**Status:** proposal, not yet built. Research and a phased plan for the List tab
editor (`frontend/src/views/NewListing.jsx`). Nothing here is wired up; this is
the document the implementation PRs will cite, in the same spirit as
`SMART_LIST.md`.


## Context

Thryft Shop's pitch is "Snap it · AI writes it · list it everywhere". The List tab
(`frontend/src/views/NewListing.jsx`, route id `new`) is where that lands: the photo
uploader, the drafts grid, and the editor (`Workflow`) a seller reviews before
publishing. This plan makes that editor the reason a seller stops listing on eBay.com:
one page, fewer things on it, nothing lost, and a keyboard that works.

The editor today is one column of ten collapsible `WorkflowCard`s (Photos, Title,
Pricing & condition, Category, Video, Etsy, Depop, a "More details" fold with
Specifics / Description / Shipping / Promote, a Publish card, a sticky Publish bar). It is
already ahead of eBay in the ways that matter (AI pre-fill with ✓/⚠ marks, blockers
named in one place, refusals shown on the field). It still carries eBay's shape: one
card per eBay section, a raw numeric category-ID input, a paid Subtitle on every
listing, a whole card for a Promote toggle, Delete/Exit in two places, and edits that
are discarded on Exit.

### What sellers hate about eBay's form (research)

Sources: eBay community threads ("The new listing form is TERRIBLE", "Does anyone else
hate the new listing form", "New Listing Tool — all the bugs/complaints"),
EcommerceBytes and ValueAddedResource coverage, unstar.app's mining of eBay app
reviews, Frooition's 2026 listing-time analysis, and the Vendoo / List Perfectly /
Crosslist / Flyp / Nifty / Flowlister comparisons.

1. **Clicks and sections.** "2–3 more clicks on every field"; sections collapsed by
   default; "everything in popup boxes on the right"; Best Offer went from one click to
   three. Sellers report 2–4x longer per listing than the old single scrolling page.
2. **Item specifics** are the biggest time sink: "55 questions, 45 allowed"; can't press
   Enter to accept a typed brand; values disappearing. Yet Brand / Size / Color /
   Style / Material are what makes a listing findable, and sellers know it.
3. **Mobile form forced onto desktop.** Volume sellers list on a laptop; the form has
   no keyboard flow.
4. **Lost work.** No reliable autosave; a category change reloads the page.
5. **Forced AI you can't skip or trust.** Sellers like AI that fills the boring parts
   when they can see what was inferred vs read and fix it in place. (We already do.)
6. **Hidden and moved options** (custom SKU, "Sell similar", immediate payment). The
   lesson is "rare things live in one obvious place", not "show everything".

### What sellers actually use

From seller workflow write-ups (photos → title → specifics → description → price →
shipping → publish), eBay's numbers (85% of transactions fixed price; 40–45% of sellers
use Promoted Listings; Best Offer is an account habit, not a per-listing choice), and
what competitors put on their one form:

| Tier | Fields | Treatment |
|---|---|---|
| **Core, every listing** | Photos, Title, Condition + one-line note, Price + format, Cost ("You paid"), Category, required and most-searched specifics (Brand, Size, Color, Type, Material), package weight, Publish | Always visible, no card chrome |
| **Account defaults** | Shipping/returns/payment policies, handling time, ship-from, Best Offer, auto-promote, international shipping, pricing strategy, default weight/qty/condition | Stay in Settings. The editor shows one summary sentence with "change" |
| **Rare, findable** | Subtitle (paid), Store category, Quantity > 1, Retail on tag, per-listing Promote, Video, package dimensions, auction duration, per-listing shipping policy, eBay category # (read-only) | One "More options" disclosure that summarises what is set when collapsed |
| **Never** | Charity, scheduled start (Smart List owns it), private listing, lot size, variations, second category, buyer requirements, sales tax, reserve price, listing upgrades, custom SKU (server-owned here: `marketplaces/state.py:132` `SERVER_OWNED_FIELDS`) | Not built |

### Borrowed from competitors

Crosslist/Flowlister: one universal form, keyboard-first, ~60 s per item. Vendoo: cost
on the form. Depop/Mercari: photo → auto-fill → price → done, no sections as cards.
GarageSale: sell-similar as a first-class action. None show the listing as it will
appear in search; we can, for free.

### User decisions (confirmed)

- Two-column layout on desktop; one column on phone.
- "You paid" stays next to the price.
- Duplicate / sell similar is in scope.

## Design: "one page, five decisions"

```
┌ header: ← · "Nike Air Max 90 …" · [AI: high] · Saved just now · ⋯ ─────────────────┐
│  LEFT RAIL (lg: sticky)             │  FORM                                           │
│  photo grid (+ "Add video" tile)    │  Title ─────────────────────────── 62/80        │
│  main · ‹ › · rotate · edit         │  Condition  [Pre-owned ▾]   note: "small…"      │
│  ┌ How it looks in search ───────┐  │  Price $[64.99] · comps $62 median ▸  Cost $[8] │
│  │ [img] Nike Air Max 90 Men's…  │  │  Buy It Now | Auction | Both                    │
│  │ Pre-owned · $64.99            │  │  Category  Clothing › Men › Shoes › …  ✎        │
│  │ Free shipping · Offers        │  │  Details  3 of 4 required · ✓ read ⚠ inferred   │
│  └───────────────────────────────┘  │  [Brand: Nike ✓][Size: 10.5 ⚠][US Shoe Size —  │
│                                     │   required][+ Material] Show all 23 ▸           │
│                                     │  Shipping 1 lb 4 oz · USPS Ground · 30-day      │
│                                     │   returns · ships in 1 day · change             │
│                                     │  Description (3 lines…) Edit ▸                  │
│                                     │  More options ▸ Qty 1 · no subtitle             │
│                                     │  [Etsy / Depop sections when targeted]          │
├ sticky bar: [eBay][Etsy] ● Ready to publish   ✨ Ask AI   [Done] [Publish Live] ──────┤
```

Kept above the form, unchanged: the `missing_info` banner, `ConflictBanner`,
`LoadingOverlay` (`NewListing.jsx:391-422`).

### Section decisions

1. **Header.** Back arrow (no confirm on a saved draft), title, confidence badge,
   Live/Ended pill (keep "Editing updates the real listing on eBay"), autosave indicator,
   "Back to batch" when `activeBulk`, and a `⋯` menu: New listing (today's "Start
   over" = `startNew`), Duplicate, Check with eBay (`runPreflight`), Save to eBay
   drafts (today's `publish("draft")`, kept for the `ebay_draft` path), View on eBay,
   Delete (never while live). Replaces five header buttons and Cancel/Delete/Check in
   the bar.
2. **Photos (left rail).** `PhotosCard` grid and drop logic without chrome. Keep the
   `fromEbay` read-only `EbayPhotos` branch (`cards.jsx:40-57,163-174`). Video becomes a
   dashed "Add video" tile at the end of the grid plus a caption row under the grid when a
   video exists (status, "within 48 hours", eBay's refusal text, Remove; Remove disabled
   when there is no local `file`).
3. **Search preview.** New read-only card: first photo, title, condition label, price,
   shipping/offers line from `policiesData` and `prefs.allow_offers`. Needs a shared
   module-level `loadPrefs()` cache (pattern: `loadPolicies` in
   `ShippingPolicySelect.jsx:37-44`); today only Settings and `ShippingDialog` fetch
   `/api/prefs`.
4. **Title.** Input, counter, `RiskyWordNotes`, inline refusal, as today. Subtitle moves
   to More options; Brand leaves (it is mirrored into the Brand aspect already).
5. **Condition.** Own section (`id="condition"`) under Title: `ConditionPicker` (keeps
   the trading-card two-step) and a one-line note that grows on focus. Split
   `completion.pricing` into `price` and `condition` (`useListingForm.js:1151-1154`).
6. **Price.** Price, format segmented control, Cost; starting bid and duration only on
   auction formats; live-listing locks unchanged (`cards.jsx:1299-1310`). "Check market
   price" becomes an inline "comps ▸" disclosure under the row, reusing the existing
   `SuggestionRow` list and its strings. Retail-on-tag and the %-of-tag line move to
   More options (the note still renders when set).
7. **Category.** Path (leaf bold) + ✎. Picker = the inline suggestion list extracted from
   `CategoryQuickPick.jsx:78-120` into `CategorySuggestList`, fed by
   `w.suggestCategories`; choosing calls `loadCategoryMeta` as today. The numeric ID input
   goes; "#id" is read-only in More options. `StoreCategorySelect` moves to More options.
8. **Details (specifics).** Chip grid: required first (empty = amber "Size — required"),
   filled recommended with ✓/⚠, the next six empty recommended as ghost "+ Material",
   "Show all N ▸" (everything after a refusal). Tapping a chip opens an inline editor
   row under the chips (same inline-swap pattern as `CategoryQuickPick`, no floating
   layer) holding the existing control for that aspect type (`renderAspect`,
   `AspectChecklist`). **Enter commits and focuses the next empty required chip; Esc
   closes.** Keep "Your own specifics", "eBay asked for these", `datalist` ids
   `sugg-${slug}`, the `aria-label="Add a {name} value of your own"`, the refused chip
   ringed red with "eBay refused this" / "Fix this to publish".
9. **Shipping.** Weight lb/oz row and one sentence: fulfillment policy in effect + the
   returns/payment names from `policiesData` + "change" (inline `ShippingPolicySelect`
   plus a Settings link). Weight-cap and orphaned-policy warnings stay. Dimensions move to
   More options.
10. **Description.** Collapsed to three lines with "Edit ▸"; opens on
    `fixTarget === "description"` or `issuesFor(publishResult, "description")`.
11. **More options.** Collapsible `Section` with a summary line ("Qty 3 · Subtitle set ·
    Promoted 8%"). Contents moved verbatim: Quantity, Subtitle + fee note, Store category,
    Retail on tag, Package dimensions, Promote toggle + slider (`PromoteCard` body),
    auction duration, eBay category # (read-only).
12. **Etsy / Depop.** `EtsyCard`/`DepopCard` bodies as sections after More options, shown
    on the same rule as today (target selected or already on Etsy).
13. **Sticky bar.** Target chips, readiness + blocker chips (unchanged), "✨ Ask AI"
    expanding the refine input (keep the `aria-label="Refine listing with AI"` input
    mounted; `sellIsTwoTabs.test.jsx` asserts on it), **Done** (flush autosave, close,
    return to batch when `activeBulk`, else drafts) and **Publish Live** (exact text) /
    Update Live Listing + End. Publish results (dry-run payload, per-marketplace panel,
    issue list with Fix buttons, "Ask eBay why") render in a panel above the bar inside
    the same `[data-publish-bar]` wrapper; `PublishCard` is deleted. Keep the bottom-nav
    clearance classes (`PublishCard.jsx:446`) and re-run `npm run reach`.
14. **Autosave (drafts only).** See design below. Done/back never asks "discard?" on a
    draft whose `saveState` is `saved`; the existing confirm copy stays for live listings
    and for `saveState === "error"`.
15. **Keyboard.** Tab order = page order. Cmd/Ctrl+Enter → `publish("live")` with the
    same confirm. Esc closes the open chip editor / disclosure. Listed in the ⋯ menu.
16. **Duplicate.** ⋯ menu + a card action on draft and live cards. Backend route below.

### Autosave design (the risky part, done safely)

Facts: `PATCH /api/listings/{id}` only honours `_PATCHABLE`
(`backend/main.py:6278-6280`: policy, category, price, quantity, condition,
descriptors, format, starting bid) and 400s on anything else; it writes without the
row lock `images/order` uses; `publishListing` saves then publishes
(`publishShared.jsx:82-94`); `autofillSpecifics` updates `form` but not
`session.listing` (`useListingForm.js:1025-1029`); `addImages` ends with a full
`/api/save`; live listings revise through `quick-edit` and `dirty_fields`.

Backend:
- `_EDITOR_PATCHABLE = _PATCHABLE + (title, subtitle, brand, description,
  condition_description, item_specifics, package_weight_lb/oz, package_length/width/
  height_in, purchase_price, retail_price, promote, ad_rate_percent, auction_duration,
  store_category_id, store_category_name, etsy, depop, currency)`. Never `images`,
  `image_urls`, `videos`, `sku`, `source`, `ebay_listing_id`, `marketplaces`,
  `missing_info`, `ai_confidence`, `sold_*`, `dirty_fields`, `remote_shadow`,
  `conflicts`. Keep the explicit-list shape the route's docstring and
  `backend/tests/test_partial_listing_update.py` argue for.
- Write under the row lock via `db.mutate_listing_data` (pattern at `main.py:6745-6766`),
  compute `_sticky_status` inside the mutator, run `restore_server_fields` on the merged
  listing. Refuse (409) any non-`_PATCHABLE` key when status is published/live/sold.
- Test: a PATCH racing a publish must not demote status or blank `ebay_listing_id`.

Frontend (`useListingForm.js`):
- `savedRef` = what the server holds. Set on seed/reseed and from every writing path's
  response: `refine`, `autofillSpecifics` (also `setSession` there, fixing the drift),
  `addImages`, `deleteImage`, `reorderImages`, `addVideo`/`removeVideo`, `publish`,
  `saveSaleFigures`, and PATCH's own `res.listing`.
- Diff `collect()` against `savedRef` with the `same()`/`wire()` normalisers lifted from
  `QuickEdit.jsx:107-124` into `lib/fieldDiff.js`; send only changed
  `_EDITOR_PATCHABLE` keys; 1.5 s debounce after `set`.
- Gate: skip when `isLive || isSold || aiBusy || addingPhotos || addingVideo ||
  publishing || !sessionId`. Add a `publishing` flag set in `publish`/`endListening`/
  relist and cleared in `finally`; before `publishListing`, cancel the timer and await
  the in-flight PATCH (`pendingSave` ref).
- `saveState: idle|saving|saved|error`, `lastSavedAt`. On failure: one toast with the
  server's sentence, retry on next change with 2/5/15 s backoff. On success:
  `patchListing(sessionId, { listing })` (`store.jsx:929`) so the card updates without a
  refetch; `invalidateListings()` once on Done.

### Duplicate route

`POST /api/listings/{session_id}/duplicate`, modelled on `relist` (`main.py:10981-11049`)
but: clear `ebay_listing_id, sku, source, view_url, marketplaces, videos, dirty_fields,
remote_shadow, conflicts, ai_confidence, sold_*, ended_at, price_lowered_at,
offer_sent_at, ebay_start_time, watch_count, sold_quantity`; do not `carry_live_others`;
copy local image files and fetch from `objstore` when a file is only on R2 (relist
silently drops those, `:11020-11021`); drop `image_urls` once files are copied; status
`draft`; open the new session with `confidence: null` so the paid autofill does not
re-run. Frontend: `⋯ → Duplicate` and a card action (update
`oneGridOfDraftCards.test.jsx:172-175`, which asserts the exact action list).

### Kept as-is

All state in `useListingForm.js`; `blockers.js`; `publishShared.jsx`; the fix-target
vocabulary (`photos|title|category|specifics|price|condition|weight|shipping|
description|etsy_*|depop_*`, `location|policies → settings`), the `flagged` vs `expand`
split and the `setFixTarget(null)` + rAF re-trigger; `ConditionPicker`;
`AspectChecklist`; `ImageEditor`; `AiNotesStep`; bulk queue; drafts grid and quick-pick
components; `PublishedScreen`; `SoldArchive`; live-listing mode (locks, Update/End,
`mode: "revise"` blockers); the ended → relist path (Publish, not Update).
ListHome (uploader + drafts) is unchanged apart from the Duplicate card action.

## Implementation phases (each shippable)

**Phase 0 — Backend for autosave.** *Built (this branch).* `_EDITOR_PATCHABLE` beside
`_PATCHABLE` on `PATCH /api/listings/{id}`, the write moved under the row lock via
`db.mutate_listing_data` with `status=None`, a 409 for editor fields on a published,
live or sold listing; tests in `test_partial_listing_update.py` and
`test_autosave_patches_the_editor_fields.py`, including the publish race.

**Phase 1 — Autosave + Done.** *Built.* `lib/fieldDiff.js` (the allow-list mirrored
from the server, and "same value" by meaning rather than representation); in
`useListingForm` a `saved` baseline every writing path updates, a 1.5 s debounce
sending only the changed keys through `PATCH /api/listings/{id}`, a gate on live,
sold, AI-busy, uploading and publishing, a wait on the in-flight save before any
publish, retries at 2/5/15 s with one warning, and a stop on the server's 409;
`saveStatus`/`dirty`/`flushSave` exported. The header shows Unsaved / Saving… /
Saved / Not saved yet; Exit, My drafts and the bar's Done (was Cancel) flush and
leave without a confirm on a draft, and still confirm on a live listing or after a
failed save. Tests: `lib/fieldDiff.test.js`, `views/listing/autosave.test.jsx`.

**Phase 2 — `Section` and layout.** *Built.* `views/listing/Section.jsx` replaces
`WorkflowCard` 1:1 (same `id`, `title`, `hint`, `state`, `flagged`, `expand`; the
icon tile, the Complete/Optional chip and the per-card collapse are gone; `collapsible`
only on Description); the form is one surface divided by rules. Two columns from
`lg` up with a sticky left rail holding the photos (3–4 tile columns there) and the
new `SearchPreview` (first photo, title cut at 80, condition, price or starting bid,
carrier from the policy in effect, "or Best Offer" from the account default via
`lib/prefs.js`). Tests: `section.test.jsx`, `searchPreview.test.jsx`; `npm run reach`
clean on phone and desktop.

**Phase 3 — Consolidation.** *Built.* The header is a back arrow, the title and
one `⋯` menu (new `components/ui/Menu.jsx`: `role="menu"`, outside-click, Esc,
arrow keys) holding New listing, Check with eBay, Save to eBay drafts, View on
eBay and Delete (never while live); the five header buttons and the bar's
Cancel/Delete/Check/Save Draft are gone. The video is an "Add video" tile at the
end of the photo grid with a caption row under it. Condition is a section of its
own (`completion.price` and `completion.condition` replace `pricing`). The price
row keeps Cost beside it; "Check market price" is a disclosure that opens itself
when the comps land. The category is a line (path, leaf bold, pencil) that swaps
in `CategorySuggestList` (extracted from `CategoryQuickPick`, with a search box;
`suggestCategories(query)`); the numeric id is read-only under More options.
Shipping is the weight row and one sentence naming the policies in effect, with
`change` opening the picker inline. The description is three lines and Edit.
`MoreOptions` (collapsible `Section` with a `summary` line) holds quantity,
subtitle, brand, store shelf, retail on tag, box size, promotion and the
category number; `PromoteCard` and `PublishCard` are deleted. The sticky bar
(`PublishBar.jsx`) holds Ask AI (the refine input, mounted and hidden), Done and
Publish Live / Update + End, with the publish result panel above it inside
`[data-publish-bar]`; `npm run reach` clean. Tests rewritten: `addVideo`,
`theTagPriceIsNotWhatYouPaid`, `storeCategoryPicker`, `missingFieldColour`,
`sellingFormatOnDetail`, `theOpeningBidIsNotTheAskingPrice`, `priceRowsRoundTo99`,
`oneGridOfDraftCards`; new: `menu.test.jsx`, `editorHeader`, `categoryLine`,
`publishBar`, `compsDisclosure`, `descriptionPreview`. Not in this phase: the
keyboard shortcuts (Phase 4) and Duplicate (Phase 5), which join the menu then.

**Phase 4 — Details chips + keyboard.** `views/listing/Details.jsx` (extract
`SpecificsCard`), `ChipEditor`, Enter/Esc, `useEditorShortcuts`. Tests to rewrite:
`specificsRefusal` ("one ringed control" → "one ringed chip", "Show 4 more"),
`aspectCheckboxes`, `specificsAreFilledNotOffered` ("Add specific", inferred
`aria-label`), `generationFillsEveryAspect`.

**Phase 5 — Duplicate.** Route + test (strips eBay identity, copies R2 photos) + menu
item + card action.

## Verification

- `cd frontend && npm run lint && npm test` after every phase; `npm run reach` after
  Phases 2 and 3 (bar height changes).
- `pytest backend/tests -q` and `ruff check backend` after Phases 0 and 5.
- Manual (`./run.sh`): upload one photo; draft opens in the new layout at 375px and
  1280px; type a title and watch the preview and "Saved"; press Enter through required
  chips; publish in dry-run and read the payload in the bar panel; open a live listing
  and confirm it still asks before discarding and shows Update / End; open an imported
  eBay listing and confirm read-only photos; open a draft from a running batch and
  confirm Done returns to the batch; Duplicate a live listing and get a draft with
  photos and no eBay id.
