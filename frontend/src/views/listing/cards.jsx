import { useEffect, useState } from "react";
import {
  DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors,
} from "@dnd-kit/core";
import {
  SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import { AnimatePresence, motion } from "framer-motion";
import {
  Image as ImageIcon, Type, FolderTree, ListChecks, Coins, PackageOpen,
  AlignLeft, Search, Plus, X, TrendingUp, ExternalLink, Truck, AlertTriangle,
  Sparkles, Megaphone, Loader2, Check, Store, ShoppingBag,
} from "lucide-react";
import { cn, CONDITIONS, conditionLabel, formatMoney } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { useToast } from "@/components/ui/Toaster";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea, Select } from "@/components/ui/fields";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { WorkflowCard } from "./WorkflowCard";
import { PhotoTile } from "./PhotoTile";

/* The eight workflow cards. Each is presentational; all state lives in
   useListingForm (passed down as `w`). */

// Fallback strip for an imported listing whose photos couldn't be copied into
// app storage yet (opening the listing normally imports them automatically,
// making them fully editable). Read-only: ebayimg URLs never enter the editor.
function EbayPhotos({ urls }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        {urls.map((url, i) => (
          <div key={url + i} className="rounded-tile overflow-hidden bg-bg-sunken aspect-square">
            <img src={url} alt="" loading="lazy" className="size-full object-cover" />
          </div>
        ))}
      </div>
      <p className="text-[13px] text-ink-secondary">
        These photos are still hosted by eBay — we couldn’t copy them for editing
        just now. Close and reopen this listing to try again; everything else
        here stays editable.
      </p>
    </div>
  );
}

export function PhotosCard({ w, onEdit, onDelete }) {
  const formImages = w.form.images || [];
  // Local order leads while dragging so the tiles move with the finger; the
  // form catches up when the drop is persisted.
  const [order, setOrder] = useState(formImages);
  const [draggingName, setDraggingName] = useState(null);

  // Sync from the form when NOT mid-drag (a photo was added, deleted, rotated).
  useEffect(() => {
    if (draggingName) return;
    setOrder(w.form.images || []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [w.form.images]);

  // Pointer sensor with a small activation distance: on a phone the drag
  // handle is also a tap target, and without a threshold every tap that
  // wobbles a pixel starts a drag. Keyboard sensor because reordering photos
  // with a keyboard is otherwise impossible — the old implementation was
  // pointer events and elementFromPoint, which no keyboard can reach.
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const onDragStart = ({ active }) => setDraggingName(active.id);
  const onDragCancel = () => setDraggingName(null);
  const onDragEnd = ({ active, over }) => {
    setDraggingName(null);
    if (!over || active.id === over.id) return;
    const from = order.indexOf(active.id);
    const to = order.indexOf(over.id);
    if (from < 0 || to < 0) return;
    const next = arrayMove(order, from, to);
    setOrder(next);
    // reorderImages rolls this back if the server rejects it.
    w.reorderImages(next);
  };

  const ebayUrls = w.form.image_urls || [];
  const fromEbay = (w.form.source || "") === "ebay" && !formImages.length;

  return (
    <WorkflowCard
      id="photos" icon={ImageIcon} title="Photos"
      hint={fromEbay
        ? "The photos on your live eBay listing"
        : "Drag the handle to reorder — the first photo is your eBay main image. One-tap rotate & delete; hover Edit to clean up or crop"}
      state={w.completion.photos} flagged={w.fixTarget === "photos"}
    >
      {fromEbay ? <EbayPhotos urls={ebayUrls} /> : (
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={onDragCancel}
      >
      <div className={cn(
        "grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3",
        // While dragging, kill hover scale + CSS transitions on every tile and
        // hide the hover overlay — otherwise the pointer sweeping across the
        // grid fires all their group-hover effects at once (the repaint storm
        // behind the lag, plus the Edit button flashing on each tile).
        draggingName && "select-none [&_img]:!scale-100 [&_*]:!transition-none [&_.ph-ov]:!opacity-0",
      )}>
        <SortableContext items={order} strategy={rectSortingStrategy}>
          <AnimatePresence>
            {order.map((name, i) => (
              <PhotoTile
                key={name}
                sessionId={w.sessionId}
                name={name}
                index={i}
                version={w.imageVersions[name] || 0}
                reorderable={order.length > 1}
                onEdit={() => onEdit(name)}
                onDelete={() => onDelete(name)}
                onRotate={() => w.rotateImage(name)}
              />
            ))}
          </AnimatePresence>
        </SortableContext>
        {/* Add more photos — optimizes + appends to this listing. */}
        <label className={cn(
          "relative rounded-tile border-2 border-dashed border-line bg-bg-sunken/40 aspect-square",
          "grid place-items-center cursor-pointer text-ink-secondary transition-colors duration-150",
          "hover:border-blue/50 hover:text-blue",
          w.addingPhotos && "pointer-events-none opacity-70",
        )}>
          <input
            type="file" accept="image/*" multiple className="sr-only"
            disabled={w.addingPhotos}
            onChange={(e) => { w.addImages(e.target.files); e.target.value = ""; }}
          />
          <span className="flex flex-col items-center gap-1 text-[12px] font-semibold">
            {w.addingPhotos
              ? <Loader2 size={20} className="animate-spin" aria-hidden />
              : <Plus size={20} aria-hidden />}
            {w.addingPhotos ? "Adding…" : "Add photos"}
          </span>
        </label>
      </div>
      </DndContext>
      )}
    </WorkflowCard>
  );
}

export function TitleCard({ w }) {
  const len = w.form.title.length;
  return (
    <WorkflowCard
      id="title" icon={Type} title="Title"
      hint="What buyers see first in search"
      state={w.completion.title} flagged={w.fixTarget === "title"}
    >
      <div className="flex flex-col gap-4">
        <Field
          label="Title"
          hint={
            <span className={cn("tabular-nums", len > 72 && "text-warning font-semibold")}>
              {len}/80
            </span>
          }
        >
          <Input
            maxLength={80}
            value={w.form.title}
            needsFix={w.fixTarget === "title"}
            onChange={(e) => w.set("title", e.target.value)}
            placeholder="e.g. Nike Air Max 90 Men's 10.5 White Leather Sneakers"
          />
        </Field>
        <div className="grid sm:grid-cols-2 gap-4">
          <Field label="Subtitle" hint="(optional)">
            <Input
              value={w.form.subtitle}
              onChange={(e) => w.set("subtitle", e.target.value)}
            />
          </Field>
          <Field label="Brand">
            <Input
              value={w.form.brand}
              onChange={(e) => w.set("brand", e.target.value)}
            />
          </Field>
        </div>
      </div>
    </WorkflowCard>
  );
}

// A div with button semantics (not a <button>) because the price rows embed
// real <a> links — interactive elements can't nest.
function SuggestionRow({ chosen, onClick, left, right }) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); }
      }}
      className={cn(
        "w-full flex items-center justify-between gap-3 text-left px-4 py-3 rounded-input border",
        "transition-colors duration-150 cursor-pointer text-sm",
        chosen
          ? "border-blue bg-blue-soft"
          : "border-line hover:border-line-strong hover:bg-bg-sunken",
      )}
    >
      <span className="min-w-0 text-ink">{left}</span>
      <span className="shrink-0 font-bold text-blue tabular-nums">{right}</span>
    </div>
  );
}

export function CategoryCard({ w }) {
  const s = w.catSuggestions;
  return (
    <WorkflowCard
      id="category" icon={FolderTree} title="Category"
      hint="The right category unlocks eBay's required fields"
      state={w.completion.category} flagged={w.fixTarget === "category"}
    >
      <div className="flex flex-col gap-4">
        <div className="grid sm:grid-cols-[1fr_auto] gap-4">
          <Field label="Category" help="A human-readable label — the numeric ID is what eBay uses.">
            <Input
              value={w.form.category_suggestion}
              needsFix={w.fixTarget === "category"}
              onChange={(e) => w.set("category_suggestion", e.target.value)}
            />
          </Field>
          <Field label="eBay Category ID" hint="(numeric)">
            <Input
              className="sm:w-40"
              value={w.form.category_id}
              needsFix={w.fixTarget === "category"}
              onChange={(e) => w.set("category_id", e.target.value)}
              onBlur={() => w.loadCategoryMeta()}
            />
          </Field>
        </div>
        <div>
          <Button variant="soft" onClick={w.suggestCategories}>
            <Search aria-hidden /> Suggest eBay categories
          </Button>
        </div>
        {s?.loading && <AIStatusInline message="Matching eBay categories…" />}
        {s?.error && <p className="text-sm text-ink-secondary">{s.error}</p>}
        {s?.items && (
          s.items.length ? (
            <div className="flex flex-col gap-2">
              {s.items.map((c) => (
                <SuggestionRow
                  key={c.category_id}
                  chosen={c.category_id === w.form.category_id}
                  onClick={() => w.chooseCategory(c)}
                  left={c.path || c.category_name}
                  right={`#${c.category_id}`}
                />
              ))}
            </div>
          ) : (
            <p className="text-sm text-ink-secondary">No category matches found. Try editing the title.</p>
          )
        )}
      </div>
    </WorkflowCard>
  );
}

// Physical item-size aspects — pinned into their own "Item size" group so
// they're easy to find (they used to drown alphabetically in Recommended, and
// some categories refuse to publish without them).
const DIMENSION_ASPECTS = new Set([
  "item height", "item length", "item width", "item depth", "item diameter",
  "item weight", "height", "length", "width", "depth", "diameter",
]);

// The trust marker for one specific: ✓ = the AI read it off the item (tag,
// label, print) or it's unambiguous; ⚠ = a reasonable inference worth a
// glance; nothing = the seller typed or confirmed it (or it's empty).
//
// A bare icon rather than a pill: a category can carry twenty of these, and
// twenty pills read as decoration rather than signal. The icon keeps the
// per-field cue while the section headers carry the words.
function ConfidenceMark({ row, className }) {
  if (!row || !(row.value || "").trim() || !row.confidence) return null;
  const high = row.confidence === "high";
  const label = high
    ? "Read from your photos by the AI"
    : "Inferred by the AI — worth a glance";
  return (
    <span
      title={label} aria-label={label} tabIndex={0}
      className={cn("inline-flex shrink-0 cursor-help outline-none",
        high ? "text-green" : "text-warning", className)}
    >
      {high ? <Check size={13} aria-hidden /> : <AlertTriangle size={13} aria-hidden />}
    </span>
  );
}

// One labelled group of specifics. The rule under the heading is what makes a
// long list scannable — without it, required and optional fields are one
// undifferentiated grid and the eye has nothing to anchor on.
function SpecGroup({ title, note, count, children }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline flex-wrap gap-x-2 gap-y-0.5 pb-1.5 border-b border-line">
        <h4 className="text-[13px] font-bold text-ink">{title}</h4>
        {count != null && (
          <span className="text-[12px] font-semibold text-ink-faint tabular-nums">{count}</span>
        )}
        {note && <span className="text-[12px] font-normal text-ink-faint">{note}</span>}
      </div>
      {children}
    </section>
  );
}

export function SpecificsCard({ w }) {
  const aspects = w.categoryMeta.aspects || [];
  const required = aspects.filter((a) => a.required);
  const dimensions = aspects.filter(
    (a) => !a.required && DIMENSION_ASPECTS.has(a.name.trim().toLowerCase()));
  const recommendedAll = aspects.filter(
    (a) => !a.required && !DIMENSION_ASPECTS.has(a.name.trim().toLowerCase()));
  // Every filled recommended aspect is always visible (a hidden ⚠ would be an
  // un-reviewable flag); unfilled ones show the first 8 until "Show all".
  const [showAll, setShowAll] = useState(false);
  const recommended = showAll ? recommendedAll
    : recommendedAll.filter((a, i) => i < 8 || w.getSpecific(a.name));
  const hiddenCount = recommendedAll.length - recommended.length;
  const aspectNames = new Set(
    [...required, ...dimensions, ...recommendedAll].map((a) => a.name.toLowerCase()));
  // Free-form rows: everything not already shown as a category aspect field.
  const freeRows = w.form.item_specifics
    .map((s, i) => ({ ...s, i }))
    .filter((s) => !aspectNames.has(s.name.trim().toLowerCase()));

  const catAspects = [...required, ...dimensions, ...recommendedAll];
  const filledCount = catAspects.filter((a) => w.getSpecific(a.name)).length;
  // Brand mirrors the listing's own brand field (see renderAspect), so a
  // required Brand aspect isn't "empty" just because no specifics row exists
  // for it — counting it as missing would send the seller hunting for a field
  // that already shows a value.
  const isFilled = (a) => !!(w.getSpecific(a.name)
    || (a.name.trim().toLowerCase() === "brand" && (w.form.brand || "").trim()));
  const missingRequired = required.filter((a) => !isFilled(a)).length;
  const recommendedFilled = recommendedAll.filter(isFilled).length;
  const reviewCount = w.form.item_specifics
    .filter((s) => (s.value || "").trim() && s.confidence === "medium").length;

  const setRow = (i, key, value) => {
    const specs = [...w.form.item_specifics];
    // Editing a value makes it the seller's own — drop the AI badge.
    specs[i] = key === "value"
      ? { ...specs[i], value, confidence: "" }
      : { ...specs[i], [key]: value };
    w.set("item_specifics", specs);
  };
  const removeRow = (i) => {
    w.set("item_specifics", w.form.item_specifics.filter((_, j) => j !== i));
  };

  const renderAspect = (a) => {
    const row = w.getSpecificRow(a.name);
    // MULTI-value aspects (Season, Features, Theme...) can hold several
    // values; the field edits the first and the rest show as removable chips
    // — without this they'd be invisible (hidden from freeRows by name).
    const extras = w.form.item_specifics
      .map((s, i) => ({ ...s, i }))
      .filter((s) => s.name.trim().toLowerCase() === a.name.toLowerCase())
      .slice(1);
    // Brand lives on the listing itself (Title card, AI identify, the maker
    // double-check) — mirror it here so the Brand aspect never LOOKS empty
    // when the listing has one, and edits flow back to the brand field.
    const isBrand = a.name.trim().toLowerCase() === "brand";
    const shown = row?.value || (isBrand ? (w.form.brand || "") : "");
    const setValue = (v) => {
      if (isBrand) w.set("brand", v);
      w.upsertSpecific(a.name, v);
    };
    // An empty REQUIRED aspect blocks publishing — it must be unmissable in
    // the grid, not discovered via a failed publish. Amber ring + the word
    // "Required" in amber until it's filled.
    //
    // Nothing else earns a badge here. Which fields are required and which
    // are optional is what the group heading says, so repeating it on every
    // row was pure noise — and a "Recommended" pill on twenty rows made the
    // two rows that actually needed attention impossible to spot.
    const missing = a.required && !shown.trim();
    const ringCls = missing ? "ring-2 ring-warning/60" : undefined;
    // An inference the seller hasn't looked at yet. This is the one thing in
    // the card that can put a WRONG value on a live listing, so it gets the
    // only interactive affordance on a field label: read it, tap ✓, done.
    const unreviewed = row?.confidence === "medium" && (shown || "").trim();
    const badge = (
      <span className="inline-flex items-center gap-1.5">
        <ConfidenceMark row={row} />
        {unreviewed && (
          <button
            type="button"
            onClick={() => w.confirmSpecific(a.name)}
            title={`Confirm "${shown}" is correct`}
            className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning-soft px-1.5 py-px text-[11px] font-bold text-warning cursor-pointer hover:border-warning transition-colors"
          >
            <Check size={10} aria-hidden /> Looks right
          </button>
        )}
        {missing && (
          <span className="text-[12px] font-semibold text-warning">Required</span>
        )}
      </span>
    );
    return (
      <Field key={a.name} label={a.name} hint={badge}>
        {a.mode === "SELECTION_ONLY" && a.values?.length ? (
          <Select value={shown} className={ringCls}
            onChange={(e) => setValue(e.target.value)}>
            <option value="">— select —</option>
            {shown && !a.values.includes(shown) && (
              <option value={shown}>{shown}</option>
            )}
            {a.values.map((v) => <option key={v} value={v}>{v}</option>)}
          </Select>
        ) : (
          <Input
            value={shown}
            placeholder={a.name}
            className={ringCls}
            onChange={(e) => setValue(e.target.value)}
          />
        )}
        {extras.length > 0 && (
          <span className="flex flex-wrap items-center gap-1.5 mt-1.5">
            {extras.map((s) => (
              <span key={s.i}
                className="inline-flex items-center gap-1 rounded-full bg-bg-sunken border border-line px-2 py-0.5 text-[12px] font-semibold text-ink-secondary">
                {s.value}
                <button type="button" aria-label={`Remove ${a.name}: ${s.value}`}
                  onClick={() => removeRow(s.i)}
                  className="cursor-pointer text-ink-faint hover:text-error">
                  <X size={11} aria-hidden />
                </button>
              </span>
            ))}
          </span>
        )}
      </Field>
    );
  };

  return (
    <WorkflowCard
      id="specifics" icon={ListChecks} title="Item specifics"
      hint={catAspects.length
        ? `${filledCount} of ${catAspects.length} filled by the AI`
          + (missingRequired ? ` · ${missingRequired} required still empty` : "")
          + (reviewCount ? ` · ${reviewCount} to check` : "")
          + ". Required ones gate publishing; a wrong specific is worse than a "
          + "missing one, so check anything flagged."
        : "Details buyers filter by — required ones gate publishing"}
      state={w.completion.specifics} flagged={w.fixTarget === "specifics"}
    >
      <div className="flex flex-col gap-6">
        {/* What still needs a human. The AI fills as much as it can, so the
            seller's actual job here is the exception list — surfacing it at
            the top is the difference between "scan twenty fields" and "handle
            these three". Hidden entirely once there's nothing outstanding. */}
        {(missingRequired > 0 || reviewCount > 0) && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-input border border-warning/40 bg-warning-soft px-3.5 py-2.5">
            <AlertTriangle size={16} className="text-warning shrink-0" aria-hidden />
            <span className="text-[13px] text-ink flex-1 min-w-0">
              {missingRequired > 0 && (
                <strong className="font-bold">
                  {missingRequired} required {missingRequired === 1 ? "specific is" : "specifics are"} empty
                </strong>
              )}
              {missingRequired > 0 && reviewCount > 0 && " · "}
              {reviewCount > 0 && (
                <>
                  <strong className="font-bold">{reviewCount} AI {reviewCount === 1 ? "guess" : "guesses"}</strong>
                  {" "}to check — a wrong specific is worse than a missing one
                </>
              )}
            </span>
            {reviewCount > 1 && (
              // Offered last and worded plainly, never as the primary action:
              // one tap that accepts everything is exactly how a wrong value
              // reaches a live listing.
              <button
                type="button"
                onClick={w.confirmAllSpecifics}
                className="shrink-0 text-[12px] font-semibold text-ink-secondary underline underline-offset-2 cursor-pointer hover:text-ink"
              >
                I've read them all
              </button>
            )}
          </div>
        )}

        {required.length > 0 && (
          <SpecGroup
            title="Required to publish"
            count={`${required.length - missingRequired}/${required.length}`}
            note={missingRequired > 0 ? "eBay rejects the listing without these" : "all set"}
          >
            <div className="grid sm:grid-cols-2 gap-x-4 gap-y-3.5">
              {required.map(renderAspect)}
            </div>
          </SpecGroup>
        )}

        {recommended.length > 0 && (
          <SpecGroup
            title="Recommended"
            count={`${recommendedFilled}/${recommendedAll.length}`}
            note="buyers filter by these — more filled, more views"
          >
            <div className="grid sm:grid-cols-2 gap-x-4 gap-y-3.5">
              {recommended.map(renderAspect)}
            </div>
            {hiddenCount > 0 && (
              <div>
                <Button variant="ghost" size="sm" onClick={() => setShowAll(true)}>
                  <Plus aria-hidden /> Show {hiddenCount} more
                </Button>
              </div>
            )}
          </SpecGroup>
        )}

        {/* Item size — the ITEM's own measurements (eBay sometimes rejects a
            publish without them), distinct from the shipping box under
            Shipping. AI pre-fills estimates; tweak as needed. */}
        {dimensions.length > 0 && (
          <SpecGroup title="Item size" note="the item itself, not the shipping box (e.g. “7 in”)">
            <div className="grid sm:grid-cols-3 gap-x-4 gap-y-3.5">
              {dimensions.map(renderAspect)}
            </div>
          </SpecGroup>
        )}

        {freeRows.length > 0 && (
          <SpecGroup title="Your own specifics" count={freeRows.length}>
            <div className="flex flex-col gap-2.5">
              {freeRows.map((s) => (
                <div key={s.i} className="flex gap-2.5 items-center">
                  <Input
                    value={s.name} placeholder="Name" className="flex-1"
                    aria-label="Specific name"
                    onChange={(e) => setRow(s.i, "name", e.target.value)}
                  />
                  <Input
                    value={s.value} placeholder="Value" className="flex-[1.4]"
                    aria-label="Specific value"
                    onChange={(e) => setRow(s.i, "value", e.target.value)}
                  />
                  <ConfidenceMark row={s} />
                  <Button variant="ghost" size="iconSm" aria-label="Remove specific"
                    onClick={() => removeRow(s.i)}>
                    <X size={15} />
                  </Button>
                </div>
              ))}
            </div>
          </SpecGroup>
        )}

        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 pt-0.5">
          <span className="inline-flex flex-wrap items-center gap-2">
            {/* The AI fills these automatically once per listing, but only
                when a REQUIRED aspect is empty — so a category change (a whole
                new aspect set), a run that errored, or a listing that's merely
                missing recommended fields all left the seller typing by hand
                with no way to ask again. This is that way. */}
            {catAspects.length > 0 && filledCount < catAspects.length && (
              <Button variant="secondary" onClick={w.autofillSpecifics}>
                <Sparkles aria-hidden /> Fill {catAspects.length - filledCount} with AI
              </Button>
            )}
            <Button
              variant="ghost"
              onClick={() => w.set("item_specifics", [...w.form.item_specifics, { name: "", value: "" }])}
            >
              <Plus aria-hidden /> Add specific
            </Button>
          </span>
          {/* The key, one quiet line — it explains the two marks a seller
              actually sees on the fields, and nothing else. */}
          {w.form.item_specifics.length > 0 && (
            <span className="text-[12px] text-ink-faint inline-flex items-center gap-3 flex-wrap">
              <span className="inline-flex items-center gap-1">
                <Check size={13} className="text-green" aria-hidden /> AI read it off the item
              </span>
              <span className="inline-flex items-center gap-1">
                <AlertTriangle size={13} className="text-warning" aria-hidden /> AI guessed — check it
              </span>
            </span>
          )}
        </div>
      </div>
    </WorkflowCard>
  );
}

const LISTING_FORMATS = [
  ["FIXED_PRICE", "Buy It Now"],
  ["AUCTION", "Auction"],
  ["AUCTION_BIN", "Auction + BIN"],
];
const AUCTION_DURATIONS = [
  ["DAYS_1", "1 day"], ["DAYS_3", "3 days"], ["DAYS_5", "5 days"],
  ["DAYS_7", "7 days"], ["DAYS_10", "10 days"],
];

export function PricingCard({ w }) {
  const conditions = w.categoryMeta.conditions?.length
    ? w.categoryMeta.conditions.map((c) => ({ value: c.enum, label: c.label || conditionLabel(c.enum) }))
    : CONDITIONS.map((c) => ({ value: c, label: conditionLabel(c) }));
  // A controlled <select> whose value isn't among its options renders BLANK —
  // which happens when a saved listing's condition isn't in the category's
  // allowed list (or the list is still loading). Always include the current
  // value so the Condition field can never look empty/missing.
  const curCondition = w.form.condition;
  if (curCondition && !conditions.some((c) => c.value === curCondition)) {
    conditions.unshift({ value: curCondition, label: conditionLabel(curCondition) });
  }
  const p = w.priceData;
  const currency = w.form.currency || "USD";
  const fmt = w.form.listing_format || "FIXED_PRICE";
  const isAuction = fmt === "AUCTION" || fmt === "AUCTION_BIN";

  return (
    <WorkflowCard
      id="pricing" icon={Coins} title="Pricing & condition"
      hint="Buy It Now, auction, or both — check live comps so you never guess"
      state={w.completion.pricing}
      flagged={w.fixTarget === "price" || w.fixTarget === "condition"}
    >
      <div className="flex flex-col gap-4">
        <Field label="Listing format">
          <div className="inline-flex w-full sm:w-auto rounded-input border border-line p-0.5 bg-bg-sunken">
            {LISTING_FORMATS.map(([val, lbl]) => (
              <button
                key={val} type="button" onClick={() => w.set("listing_format", val)}
                aria-pressed={fmt === val}
                className={cn(
                  "flex-1 sm:flex-none px-3.5 h-9 rounded-[9px] text-[13px] font-semibold cursor-pointer",
                  "whitespace-nowrap transition-colors duration-150",
                  fmt === val ? "bg-card text-ink shadow-card" : "text-ink-secondary hover:text-ink",
                )}
              >
                {lbl}
              </button>
            ))}
          </div>
        </Field>

        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
          {isAuction && (
            <Field label={`Starting bid (${currency})`}>
              <Input
                type="number" step="0.01" min="0" inputMode="decimal"
                value={w.form.auction_start_price}
                needsFix={w.fixTarget === "price"}
                onChange={(e) => w.set("auction_start_price", e.target.value)}
              />
            </Field>
          )}
          {(!isAuction || fmt === "AUCTION_BIN") && (
            <Field label={isAuction ? `Buy It Now (${currency})` : `Price (${currency})`}>
              <Input
                type="number" step="0.01" min="0" inputMode="decimal"
                value={w.form.price}
                needsFix={w.fixTarget === "price"}
                onChange={(e) => w.set("price", e.target.value)}
              />
            </Field>
          )}
          {isAuction ? (
            <Field label="Duration">
              <Select value={w.form.auction_duration}
                onChange={(e) => w.set("auction_duration", e.target.value)}>
                {AUCTION_DURATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </Select>
            </Field>
          ) : (
            <Field label="Quantity">
              <Input
                type="number" min="1" inputMode="numeric"
                value={w.form.quantity}
                onChange={(e) => w.set("quantity", e.target.value)}
              />
            </Field>
          )}
          {/* Optional cost basis — auto-read from a price sticker when Shop
              Mode could see one. Never required; powers profit once sold. */}
          <Field
            label={`You paid (${currency})`}
            help="What it cost you — used to show your profit when it sells."
          >
            <Input
              type="number" step="0.01" min="0" inputMode="decimal"
              placeholder="optional"
              value={w.form.purchase_price}
              onChange={(e) => w.set("purchase_price", e.target.value)}
            />
          </Field>
        </div>
        {w.form.purchase_price !== "" && Number(w.form.price) > 0 && (
          <p className="text-[13px] text-ink-secondary -mt-1">
            Potential profit at this price:{" "}
            <strong className={Number(w.form.price) - Number(w.form.purchase_price) >= 0
              ? "text-success" : "text-warning"}>
              ${(Number(w.form.price) - Number(w.form.purchase_price)).toFixed(2)}
            </strong>{" "}
            before fees & shipping.
          </p>
        )}

        {/* Condition gets its own labeled row (not the last cell of the price
            grid, where it was easy to miss) paired with its description. */}
        <div className="grid sm:grid-cols-[minmax(200px,260px)_1fr] gap-4 pt-1 border-t border-line">
          <Field label="Condition">
            <Select value={w.form.condition} onChange={(e) => w.set("condition", e.target.value)}>
              {conditions.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </Select>
          </Field>
          <Field label="Condition description" hint="(what a buyer should know)">
            <Textarea
              rows={2}
              value={w.form.condition_description}
              onChange={(e) => w.set("condition_description", e.target.value)}
            />
          </Field>
        </div>

        <div>
          <Button variant="soft" onClick={w.checkMarketPrice}>
            <TrendingUp aria-hidden /> Check market price
          </Button>
        </div>

        {p?.loading && <AIStatusInline message="Finding comparable listings…" />}
        {p?.error && <p className="text-sm text-ink-secondary">{p.error}</p>}
        {p && !p.loading && !p.error && (
          !p.suggestion ? (
            <p className="text-sm text-ink-secondary">
              No comparable listings found — try a simpler title or set a category first.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {(p.sources || []).map((src) => (
                <div key={src.label} className="flex flex-col gap-2">
                  <SuggestionRow
                    chosen={Number(w.form.price) === Number(src.estimate)}
                    onClick={() => w.set("price", Number(src.estimate).toFixed(2))}
                    left={
                      <>
                        <strong>{src.label}</strong> — median of {src.count} listings
                        (typical ${src.low}–${src.high}). Click to use.
                      </>
                    }
                    right={`$${src.estimate}`}
                  />
                  {(src.sample || []).map((c, i) => (
                    <SuggestionRow
                      key={i}
                      chosen={Number(w.form.price) === Number(c.price)}
                      onClick={() => w.set("price", Number(c.price).toFixed(2))}
                      left={
                        <>
                          {c.title}
                          {c.condition && <em className="text-ink-secondary"> ({c.condition})</em>}
                          {c.url && (
                            <a
                              href={c.url} target="_blank" rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="inline-flex items-center gap-0.5 ml-1.5 text-blue font-semibold"
                            >
                              view <ExternalLink size={11} aria-hidden />
                            </a>
                          )}
                        </>
                      }
                      right={`$${c.price}`}
                    />
                  ))}
                  {src.search_url && (
                    <a
                      href={src.search_url} target="_blank" rel="noopener noreferrer"
                      className="text-sm font-semibold text-blue inline-flex items-center gap-1"
                    >
                      See all comparable listings on eBay <ExternalLink size={12} aria-hidden />
                    </a>
                  )}
                </div>
              ))}
              {!p.suggestion.sold_data && (
                <p className="text-xs text-ink-secondary">
                  These are asking prices (what sellers want), not sold prices — pricing a
                  little under the median usually sells faster.
                </p>
              )}
            </div>
          )
        )}
      </div>
    </WorkflowCard>
  );
}

// Weight caps (oz) for services that silently kill a publish, mirrored from
// the backend preflight so the warning shows the moment the weight is typed.
// Mirrors SERVICE_WEIGHT_CAPS_OZ in backend/services/preflight.py — keep the
// two in step. This copy exists so the Shipping card can warn as you type,
// but preflight (and eBay) are the authority, and a client list that quietly
// falls behind the server's is worse than no warning: it says "looks fine"
// about a package eBay will reject.
const SERVICE_CAPS_OZ = [
  ["standardenvelope", 3, "eBay Standard Envelope"],
  ["firstclass", 15.9, "USPS First Class"],
  ["uspsground", 70 * 16, "USPS Ground Advantage"],
  ["mediamail", 70 * 16, "USPS Media Mail"],
];

function capIssueFor(services, weightOz) {
  if (!weightOz) return null;
  for (const code of services || []) {
    // Same normalization as the server's service_cap(): underscores AND
    // spaces out, so "USPS First Class" and "USPS_FirstClass" both match.
    const c = code.toLowerCase().replaceAll("_", "").replaceAll(" ", "");
    for (const [frag, cap, name] of SERVICE_CAPS_OZ) {
      if (c.includes(frag) && weightOz > cap) {
        const prettyCap = cap < 16 ? `${cap} oz` : `${+(cap / 16).toFixed(0)} lb`;
        return `${name} maxes out at ${prettyCap} — this package is ${+weightOz.toFixed(1)} oz. Pick a different service or eBay will reject the publish.`;
      }
    }
  }
  return null;
}

// Per-listing shipping service = an eBay fulfillment policy on the offer.
function ShippingServicePicker({ w }) {
  const { ebay, policiesData, setPoliciesData } = useApp();

  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    api("/api/ebay/policies").then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);

  if (!ebay.connected) return null;
  const policies = policiesData?.policies?.fulfillment || [];
  const accountDefault = policiesData?.selected?.fulfillment_policy_id || "";

  const chosen = w.form.fulfillment_policy_id || accountDefault;
  const services = policies.find((p) => p.id === chosen)?.services || [];
  const weightOz = (parseFloat(w.form.package_weight_lb) || 0) * 16
    + (parseFloat(w.form.package_weight_oz) || 0);
  const capIssue = capIssueFor(services, weightOz);

  return (
    <div className="flex flex-col gap-3 max-w-md">
      <Field
        label={
          <span className="inline-flex items-center gap-1.5">
            <Truck size={14} aria-hidden /> Shipping service
          </span>
        }
        help="How this item ships (an eBay shipping policy). USPS Ground Advantage is the cheapest option for most packages — up to 70 lb."
      >
        <Select
          value={chosen}
          onChange={(e) => w.set("fulfillment_policy_id", e.target.value)}
        >
          {policies.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}{p.summary ? ` · ${p.summary}` : ""}
            </option>
          ))}
        </Select>
      </Field>
      {capIssue && (
        <p className="text-[13px] font-medium text-warning flex gap-1.5" role="alert">
          <AlertTriangle size={15} className="shrink-0 mt-0.5" aria-hidden /> {capIssue}
        </p>
      )}
    </div>
  );
}

export function ShippingCard({ w }) {
  return (
    <WorkflowCard
      id="shipping" icon={PackageOpen} title="Shipping package"
      hint="Weight, size, and how it ships — eBay needs a weight to publish"
      state={w.completion.shipping}
      flagged={w.fixTarget === "weight" || w.fixTarget === "shipping"}
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-4 max-w-md">
          <Field label="Weight — lb">
            <Input
              type="number" min="0" step="1" inputMode="numeric"
              value={w.form.package_weight_lb}
              needsFix={w.fixTarget === "weight"}
              onChange={(e) => w.set("package_weight_lb", e.target.value)}
            />
          </Field>
          <Field label="Weight — oz">
            <Input
              type="number" min="0" max="15" step="0.1" inputMode="decimal"
              value={w.form.package_weight_oz}
              needsFix={w.fixTarget === "weight"}
              onChange={(e) => w.set("package_weight_oz", e.target.value)}
            />
          </Field>
        </div>
        <div className="grid grid-cols-3 gap-4 max-w-md">
          {[
            ["package_length_in", "Length (in)"],
            ["package_width_in", "Width (in)"],
            ["package_height_in", "Height (in)"],
          ].map(([key, label]) => (
            <Field key={key} label={label} hint="optional">
              <Input
                type="number" min="0" step="0.1" inputMode="decimal"
                value={w.form[key]}
                onChange={(e) => w.set(key, e.target.value)}
              />
            </Field>
          ))}
        </div>
        <ShippingServicePicker w={w} />
      </div>
    </WorkflowCard>
  );
}

export function DescriptionCard({ w }) {
  return (
    <WorkflowCard
      id="description" icon={AlignLeft} title="Description"
      hint="The story buyers read before they commit"
      state={w.completion.description} flagged={w.fixTarget === "description"}
    >
      <Textarea
        rows={7}
        value={w.form.description}
        needsFix={w.fixTarget === "description"}
        onChange={(e) => w.set("description", e.target.value)}
      />
    </WorkflowCard>
  );
}

// Promoted Listings — mirrors eBay's Promoted Listings Standard: a slider to
// pick an ad rate, charged only when the item sells through the promotion.
const PROMO_MIN = 2;
const PROMO_MAX = 20;
// Starting rate when the seller turns Promote on by hand. On the automatic
// path (auto-promote at publish) the rate is left at 0, which tells the server
// to use eBay's own recommendation for the listing and fall back to this.
const PROMO_SUGGESTED = 10;

export function PromoteCard({ w }) {
  const { ebay } = useApp();
  const on = !!w.form.promote;
  const rate = Number(w.form.ad_rate_percent) || 0;
  const price = Number(w.form.price) || 0;
  const fee = price > 0 && rate > 0 ? (price * rate) / 100 : 0;

  const toggle = () => {
    if (on) { w.set("promote", false); return; }
    w.set("promote", true);
    if (!rate) w.set("ad_rate_percent", PROMO_SUGGESTED);
  };

  return (
    <WorkflowCard
      id="promote" icon={Megaphone} title="Promote"
      hint="Boost this listing in eBay search — you only pay if it sells through the promotion"
      state={on ? "complete" : "todo"}
    >
      <div className="flex flex-col gap-5">
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <p className="font-semibold text-ink text-[15px]">Promoted Listing</p>
            <p className="text-[13px] text-ink-secondary mt-0.5">
              eBay Promoted Listings Standard — more visibility, pay only per sale.
            </p>
          </div>
          <button
            type="button" role="switch" aria-checked={on} onClick={toggle}
            aria-label="Promote this listing"
            className={cn(
              "relative shrink-0 h-7 w-12 rounded-full transition-colors duration-200 cursor-pointer",
              on ? "bg-blue" : "bg-line-strong",
            )}
          >
            <span className={cn(
              "absolute top-0.5 left-0.5 size-6 rounded-full bg-white shadow-card transition-transform duration-200",
              on && "translate-x-5",
            )} />
          </button>
        </div>

        {on && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className="flex flex-col gap-4"
          >
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-[13px] font-medium text-ink-secondary">Ad rate</p>
                <p className="text-[34px] font-bold text-ink tabular-nums leading-none mt-1">
                  {rate.toFixed(1)}<span className="text-xl align-top">%</span>
                </p>
              </div>
              <div className="text-right">
                {fee > 0 ? (
                  <>
                    <p className="text-[13px] font-medium text-ink-secondary">Fee if it sells</p>
                    <p className="text-lg font-bold text-blue tabular-nums mt-1">≈ {formatMoney(fee)}</p>
                  </>
                ) : (
                  <p className="text-[13px] text-ink-faint">Set a price to preview the fee</p>
                )}
              </div>
            </div>

            <div>
              <input
                type="range" min={PROMO_MIN} max={PROMO_MAX} step={0.5} value={rate}
                onChange={(e) => w.set("ad_rate_percent", parseFloat(e.target.value))}
                className="w-full accent-blue cursor-pointer h-2"
                aria-label="Ad rate percentage"
              />
              <div className="flex justify-between items-center text-[11px] text-ink-faint mt-1 tabular-nums">
                <span>{PROMO_MIN}%</span>
                <button
                  type="button" onClick={() => w.set("ad_rate_percent", PROMO_SUGGESTED)}
                  className="font-semibold text-blue hover:underline cursor-pointer"
                >
                  Suggested {PROMO_SUGGESTED}%
                </button>
                <span>{PROMO_MAX}%</span>
              </div>
            </div>

            <p className="text-[13px] text-ink-secondary">
              Promoted listings show higher in search and on more pages. Nothing upfront —
              eBay charges the {rate.toFixed(1)}% ad rate <strong className="text-ink">only</strong> when
              your item sells through the promotion.
            </p>

            {!ebay.connected && (
              <p className="text-[13px] font-medium text-warning flex gap-1.5" role="note">
                <AlertTriangle size={15} className="shrink-0 mt-0.5" aria-hidden />
                Connect eBay to run the promotion — we'll save this rate and apply it when you publish live.
              </p>
            )}
          </motion.div>
        )}
      </div>
    </WorkflowCard>
  );
}

// --- marketplace-specific cards ---------------------------------------------
// Rendered only while that marketplace is among the publish targets (the
// chips in the publish bar), so eBay-only sellers never see them.

const WHO_MADE_OPTIONS = [
  ["i_did", "I made it"],
  ["someone_else", "Someone else made it (vintage / resale)"],
  ["collective", "A member of my shop made it"],
];

const WHEN_MADE_OPTIONS = [
  ["made_to_order", "Made to order"],
  ["2020_2026", "2020–2026"],
  ["2010_2019", "2010–2019"],
  ["2007_2009", "2007–2009"],
  ["2000_2006", "2000–2006"],
  ["1990s", "1990s"],
  ["1980s", "1980s"],
  ["1970s", "1970s"],
  ["1960s", "1960s"],
  ["1950s", "1950s"],
  ["1940s", "1940s"],
  ["1930s", "1930s"],
  ["1920s", "1920s"],
  ["1910s", "1910s"],
  ["1900s", "1900s"],
  ["1800s", "1800s"],
  ["1700s", "1700s"],
  ["before_1700", "Before 1700"],
];

// Etsy — the fields Etsy requires beyond the shared ones: its own category
// (taxonomy), the handmade/vintage/supplies attribution, and an optional
// shipping-profile override. Tags and materials are auto-derived from the
// brand + item specifics at publish time; the inputs here override that.
export function EtsyCard({ w }) {
  const { toast } = useToast();
  const show = (w.chipTargets || []).includes("etsy");
  const [suggesting, setSuggesting] = useState(false);
  const [catPath, setCatPath] = useState("");
  const [options, setOptions] = useState(null); // shipping profiles for override

  useEffect(() => {
    if (!show || options) return;
    api("/api/etsy/settings-options")
      .then(setOptions)
      .catch(() => setOptions({ shipping_profiles: [] }));
  }, [show, options]);

  if (!show) return null;
  const e = w.form.etsy || {};
  const setEtsy = (key, value) => w.set("etsy", { ...e, [key]: value });
  const complete = !!(e.taxonomy_id && e.who_made && e.when_made);
  const flagged = typeof w.fixTarget === "string" && w.fixTarget.startsWith("etsy_");

  const suggest = async () => {
    setSuggesting(true);
    try {
      const res = await postJson(`/api/etsy/suggest-taxonomy/${w.sessionId}`,
        { listing: w.collect() });
      if (res.taxonomy_id) {
        setEtsy("taxonomy_id", res.taxonomy_id);
        setCatPath(res.path || "");
        toast(`Etsy category: ${res.path}`, { kind: "success" });
      } else {
        toast("Couldn't find a matching Etsy category — try refining the title.",
          { kind: "warning" });
      }
    } catch (err) {
      toast(`Suggestion failed: ${err.message}`, { kind: "error" });
    } finally {
      setSuggesting(false);
    }
  };

  return (
    <WorkflowCard
      id="etsy" icon={Store} title="Etsy"
      hint="What Etsy needs beyond the shared fields — category, who/when made, shipping profile"
      state={complete ? "complete" : "todo"} flagged={flagged}
    >
      <div className="flex flex-col gap-5">
        <Field
          label="Etsy category"
          help={catPath
            || (e.taxonomy_id ? `Set (taxonomy #${e.taxonomy_id})` : "Etsy has its own category tree — tap Suggest to pick one from your item details.")}
        >
          <div className="flex gap-2">
            <Input
              type="number" min="0" inputMode="numeric" className="max-w-44"
              placeholder="taxonomy id"
              value={e.taxonomy_id || ""}
              needsFix={w.fixTarget === "etsy_taxonomy"}
              onChange={(ev) => { setCatPath(""); setEtsy("taxonomy_id", parseInt(ev.target.value, 10) || 0); }}
            />
            <Button variant="secondary" onClick={suggest} loading={suggesting}>
              <Sparkles aria-hidden /> Suggest
            </Button>
          </div>
        </Field>

        <div className="grid sm:grid-cols-2 gap-4">
          <Field
            label="Who made it?"
            help="Etsy only allows handmade, vintage (20+ years), and craft supplies."
          >
            <Select
              value={e.who_made || ""}
              needsFix={w.fixTarget === "etsy_attribution"}
              onChange={(ev) => setEtsy("who_made", ev.target.value)}
            >
              <option value="">— choose —</option>
              {WHO_MADE_OPTIONS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </Select>
          </Field>
          <Field label="When was it made?">
            <Select
              value={e.when_made || ""}
              needsFix={w.fixTarget === "etsy_attribution"}
              onChange={(ev) => setEtsy("when_made", ev.target.value)}
            >
              <option value="">— choose —</option>
              {WHEN_MADE_OPTIONS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </Select>
          </Field>
        </div>

        <label className="flex items-center gap-2.5 text-sm text-ink cursor-pointer">
          <input
            type="checkbox"
            checked={!!e.is_supply}
            onChange={(ev) => setEtsy("is_supply", ev.target.checked)}
            className="size-4 accent-blue"
          />
          This is a craft supply (not a finished item)
        </label>

        <Field
          label="Shipping profile (this listing)"
          help="Leave on the account default from Settings unless this item ships differently."
        >
          <Select
            value={e.shipping_profile_id || ""}
            needsFix={w.fixTarget === "etsy_shipping_profile"}
            onChange={(ev) => setEtsy("shipping_profile_id", ev.target.value)}
          >
            <option value="">Account default</option>
            {((options && options.shipping_profiles) || []).map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </Select>
        </Field>

        <div className="grid sm:grid-cols-2 gap-4">
          <Field
            label="Tags (optional)"
            help="Comma-separated, up to 13. Left empty, we build them from your brand + item details."
          >
            <Input
              value={(e.tags || []).join(", ")}
              onChange={(ev) => setEtsy("tags",
                ev.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
            />
          </Field>
          <Field
            label="Materials (optional)"
            help='Comma-separated. Left empty, we use any "Material" item specifics.'
          >
            <Input
              value={(e.materials || []).join(", ")}
              onChange={(ev) => setEtsy("materials",
                ev.target.value.split(",").map((t) => t.trim()).filter(Boolean))}
            />
          </Field>
        </div>
      </div>
    </WorkflowCard>
  );
}

// Depop — minimal extras: size and category. Everything else maps from the
// shared fields (title truncated to Depop's limit, condition translated,
// first four photos).
export function DepopCard({ w }) {
  const show = (w.chipTargets || []).includes("depop");
  if (!show) return null;
  const d = w.form.depop || {};
  const setDepop = (key, value) => w.set("depop", { ...d, [key]: value });
  const flagged = typeof w.fixTarget === "string" && w.fixTarget.startsWith("depop_");

  return (
    <WorkflowCard
      id="depop" icon={ShoppingBag} title="Depop"
      hint="Depop extras — size and category; title, price, condition and the first 4 photos map automatically"
      state="todo" flagged={flagged}
    >
      <div className="grid sm:grid-cols-2 gap-4">
        <Field
          label="Size"
          help='Leave empty to use the "Size" item specific when there is one.'
        >
          <Input
            value={d.size || ""}
            placeholder="e.g. M, W 32, US 9"
            onChange={(ev) => setDepop("size", ev.target.value)}
          />
        </Field>
        <Field
          label="Depop category"
          help="Optional — Depop can infer it; set one to be exact."
        >
          <Input
            value={d.category || ""}
            needsFix={w.fixTarget === "depop_category"}
            onChange={(ev) => setDepop("category", ev.target.value)}
          />
        </Field>
      </div>
    </WorkflowCard>
  );
}
