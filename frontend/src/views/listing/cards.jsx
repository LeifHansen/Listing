import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Image as ImageIcon, Type, FolderTree, ListChecks, Coins, PackageOpen,
  AlignLeft, Search, Plus, X, TrendingUp, ExternalLink, Truck, AlertTriangle,
  Sparkles, Megaphone, Loader2, Ruler, Check,
} from "lucide-react";
import { cn, CONDITIONS, conditionLabel, formatMoney } from "@/lib/utils";
import { api } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { WorkflowCard } from "./WorkflowCard";
import { PhotoTile } from "./PhotoTile";

/* The eight workflow cards. Each is presentational; all state lives in
   useListingForm (passed down as `w`). */

const SEP = "|";

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
  // Local order is the source of truth while dragging; the ref mirrors it
  // synchronously so pointer handlers never read a stale array.
  const [order, setOrder] = useState(formImages);
  const orderRef = useRef(formImages);
  const dragNameRef = useRef(null);
  const dragStartRef = useRef(null);
  const [draggingName, setDraggingName] = useState(null);

  // Sync from the form when NOT mid-drag (a photo was added, deleted, rotated).
  useEffect(() => {
    if (dragNameRef.current) return;
    const imgs = w.form.images || [];
    orderRef.current = imgs;
    setOrder(imgs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [w.form.images]);

  // Pointer moves are coalesced to one hit-test per animation frame — the raw
  // pointermove stream (60-120/s) each forced a synchronous elementFromPoint
  // reflow, which is what made the drag stutter.
  const rafRef = useRef(0);
  const lastPtRef = useRef(null);

  const reorderTo = (targetIdx) => {
    const cur = orderRef.current;
    const from = cur.indexOf(dragNameRef.current);
    if (from < 0 || targetIdx < 0 || targetIdx >= cur.length || targetIdx === from) return;
    const next = [...cur];
    const [moved] = next.splice(from, 1);
    next.splice(targetIdx, 0, moved);
    orderRef.current = next;
    setOrder(next);
  };

  const startDrag = (name) => (e) => {
    e.preventDefault();
    e.stopPropagation();
    dragNameRef.current = name;
    dragStartRef.current = orderRef.current;
    setDraggingName(name);
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* not supported */ }
  };
  const processMove = () => {
    rafRef.current = 0;
    const p = lastPtRef.current;
    if (!p || !dragNameRef.current) return;
    const el = document.elementFromPoint(p.x, p.y);
    const tile = el && el.closest ? el.closest("[data-photo-idx]") : null;
    if (tile) reorderTo(Number(tile.getAttribute("data-photo-idx")));
  };
  const onMove = (e) => {
    if (!dragNameRef.current) return;
    lastPtRef.current = { x: e.clientX, y: e.clientY };
    if (!rafRef.current) rafRef.current = requestAnimationFrame(processMove);
  };
  const endDrag = () => {
    if (!dragNameRef.current) return;
    dragNameRef.current = null;
    setDraggingName(null);
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0; }
    const next = orderRef.current;
    if (next.join(SEP) !== (dragStartRef.current || []).join(SEP)) w.reorderImages(next);
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
      <div className={cn(
        "grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3",
        // While dragging, kill hover scale + CSS transitions on every tile and
        // hide the hover overlay — otherwise the pointer sweeping across the
        // grid fires all their group-hover effects at once (the repaint storm
        // behind the lag, plus the Edit button flashing on each tile).
        draggingName && "select-none [&_img]:!scale-100 [&_*]:!transition-none [&_.ph-ov]:!opacity-0",
      )}>
        <AnimatePresence>
          {order.map((name, i) => (
            <PhotoTile
              key={name}
              sessionId={w.sessionId}
              name={name}
              index={i}
              version={w.imageVersions[name] || 0}
              reorderable={order.length > 1}
              dragging={draggingName === name}
              onDragStart={startDrag(name)}
              onDragMove={onMove}
              onDragEnd={endDrag}
              onEdit={() => onEdit(name)}
              onDelete={() => onDelete(name)}
              onRotate={() => w.rotateImage(name)}
            />
          ))}
        </AnimatePresence>
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

// The ✓/⚠ trust badge for one specific: ✓ = the AI read it off the item
// (tag, label, print) or it's unambiguous; ⚠ = a reasonable inference worth a
// glance; nothing = the seller typed or confirmed it (or it's empty).
function ConfidencePill({ row }) {
  if (!row || !(row.value || "").trim() || !row.confidence) return null;
  return row.confidence === "high" ? (
    <TagPill tone="green"><Check size={11} aria-hidden /> AI</TagPill>
  ) : (
    <TagPill tone="yellow"><AlertTriangle size={11} aria-hidden /> Review</TagPill>
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
    // the grid, not discovered via a failed publish. Amber ring + "Missing"
    // pill until it's filled.
    const missing = a.required && !shown.trim();
    const ringCls = missing ? "ring-2 ring-warning/60" : undefined;
    const badge = (
      <span className="inline-flex items-center gap-1.5">
        <ConfidencePill row={row} />
        {missing ? (
          <TagPill tone="yellow">
            <AlertTriangle size={11} aria-hidden /> Missing — required
          </TagPill>
        ) : (
          <TagPill tone={a.required ? "red" : "neutral"}>
            {a.required ? "Required" : "Recommended"}
          </TagPill>
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
        ? `${filledCount} of ${catAspects.length} filled`
          + (reviewCount ? ` · ${reviewCount} for you to review` : "")
          + " — required ones gate publishing"
        : "Details buyers filter by — required ones gate publishing"}
      state={w.completion.specifics} flagged={w.fixTarget === "specifics"}
    >
      <div className="flex flex-col gap-5">
        {(required.length > 0 || recommended.length > 0) && (
          <div className="grid sm:grid-cols-2 gap-4">
            {[...required, ...recommended].map(renderAspect)}
          </div>
        )}
        {hiddenCount > 0 && (
          <div>
            <Button variant="ghost" size="sm" onClick={() => setShowAll(true)}>
              <Plus aria-hidden /> Show {hiddenCount} more optional specific{hiddenCount === 1 ? "" : "s"}
            </Button>
          </div>
        )}

        {/* Item size — the ITEM's own measurements (eBay sometimes rejects a
            publish without them), distinct from the shipping box under
            Shipping. AI pre-fills estimates; tweak as needed. */}
        {dimensions.length > 0 && (
          <div>
            <p className="text-[13px] font-semibold text-ink flex items-center gap-1.5">
              <Ruler size={14} className="text-blue" aria-hidden /> Item size
              <span className="font-normal text-ink-faint">
                — the item itself, not the shipping box (e.g. “7 in”)
              </span>
            </p>
            <div className="grid sm:grid-cols-3 gap-4 mt-2.5">
              {dimensions.map(renderAspect)}
            </div>
          </div>
        )}

        {freeRows.length > 0 && (
          <div className="flex flex-col gap-2.5">
            {freeRows.map((s) => (
              <div key={s.i} className="flex gap-2.5 items-center">
                <Input
                  value={s.name} placeholder="Name" className="flex-1"
                  onChange={(e) => setRow(s.i, "name", e.target.value)}
                />
                <Input
                  value={s.value} placeholder="Value" className="flex-[1.4]"
                  onChange={(e) => setRow(s.i, "value", e.target.value)}
                />
                <ConfidencePill row={s} />
                <Button variant="ghost" size="iconSm" aria-label="Remove specific"
                  onClick={() => removeRow(s.i)}>
                  <X size={15} />
                </Button>
              </div>
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Button
            variant="ghost"
            onClick={() => w.set("item_specifics", [...w.form.item_specifics, { name: "", value: "" }])}
          >
            <Plus aria-hidden /> Add specific
          </Button>
          {w.form.item_specifics.length > 0 && (
            <span className="text-[13px] text-ink-secondary inline-flex items-center gap-1.5 flex-wrap">
              <Sparkles size={14} className="text-blue" aria-hidden />
              <TagPill tone="green"><Check size={11} aria-hidden /> AI</TagPill> read from
              your photos & tags · <TagPill tone="yellow"><AlertTriangle size={11} aria-hidden /> Review</TagPill> inferred
              — worth a glance.
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
        </div>

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
const SERVICE_CAPS_OZ = [
  ["standardenvelope", 3, "eBay Standard Envelope"],
  ["firstclass", 15.9, "USPS First Class"],
];

function capIssueFor(services, weightOz) {
  if (!weightOz) return null;
  for (const code of services || []) {
    const c = code.toLowerCase().replaceAll("_", "");
    for (const [frag, cap, name] of SERVICE_CAPS_OZ) {
      if (c.includes(frag) && weightOz > cap) {
        return `${name} maxes out at ${cap} oz — this package is ${+weightOz.toFixed(1)} oz. Pick a different service or eBay will reject the publish.`;
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
