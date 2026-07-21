import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Image as ImageIcon, Type, FolderTree, ListChecks, Coins, PackageOpen,
  AlignLeft, Search, Plus, X, TrendingUp, ExternalLink, Truck, AlertTriangle,
  Sparkles, Megaphone,
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
  const onMove = (e) => {
    if (!dragNameRef.current) return;
    const el = document.elementFromPoint(e.clientX, e.clientY);
    const tile = el && el.closest ? el.closest("[data-photo-idx]") : null;
    if (tile) reorderTo(Number(tile.getAttribute("data-photo-idx")));
  };
  const endDrag = () => {
    if (!dragNameRef.current) return;
    dragNameRef.current = null;
    setDraggingName(null);
    const next = orderRef.current;
    if (next.join(SEP) !== (dragStartRef.current || []).join(SEP)) w.reorderImages(next);
  };

  return (
    <WorkflowCard
      id="photos" icon={ImageIcon} title="Photos"
      hint="Drag the handle to reorder — the first photo is your eBay main image. One-tap rotate & delete; hover Edit to clean up or crop"
      state={w.completion.photos} flagged={w.fixTarget === "photos"}
    >
      {order.length ? (
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          <AnimatePresence>
            {order.map((name, i) => (
              <PhotoTile
                key={name}
                sessionId={w.sessionId}
                name={name}
                index={i}
                version={w.imageVersion}
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
        </div>
      ) : (
        <p className="text-sm text-ink-secondary">
          No photos on this listing — start a new one to upload photos.
        </p>
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

export function SpecificsCard({ w }) {
  const aspects = w.categoryMeta.aspects || [];
  const required = aspects.filter((a) => a.required);
  const recommended = aspects.filter((a) => !a.required).slice(0, 8);
  const aspectNames = new Set(
    [...required, ...recommended].map((a) => a.name.toLowerCase()));
  // Free-form rows: everything not already shown as a category aspect field.
  const freeRows = w.form.item_specifics
    .map((s, i) => ({ ...s, i }))
    .filter((s) => !aspectNames.has(s.name.trim().toLowerCase()));

  const setRow = (i, key, value) => {
    const specs = [...w.form.item_specifics];
    specs[i] = { ...specs[i], [key]: value };
    w.set("item_specifics", specs);
  };
  const removeRow = (i) => {
    w.set("item_specifics", w.form.item_specifics.filter((_, j) => j !== i));
  };

  return (
    <WorkflowCard
      id="specifics" icon={ListChecks} title="Item specifics"
      hint="Details buyers filter by — required ones gate publishing"
      state={w.completion.specifics} flagged={w.fixTarget === "specifics"}
    >
      <div className="flex flex-col gap-5">
        {(required.length > 0 || recommended.length > 0) && (
          <div className="grid sm:grid-cols-2 gap-4">
            {[...required, ...recommended].map((a) => {
              const cur = w.getSpecific(a.name);
              const badge = (
                <TagPill tone={a.required ? "red" : "neutral"}>
                  {a.required ? "Required" : "Recommended"}
                </TagPill>
              );
              return (
                <Field key={a.name} label={a.name} hint={badge}>
                  {a.mode === "SELECTION_ONLY" && a.values?.length ? (
                    <Select value={cur} onChange={(e) => w.upsertSpecific(a.name, e.target.value)}>
                      <option value="">— select —</option>
                      {a.values.map((v) => <option key={v} value={v}>{v}</option>)}
                    </Select>
                  ) : (
                    <Input
                      value={cur}
                      placeholder={a.name}
                      onChange={(e) => w.upsertSpecific(a.name, e.target.value)}
                    />
                  )}
                </Field>
              );
            })}
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
            <span className="text-[13px] text-ink-secondary inline-flex items-center gap-1.5">
              <Sparkles size={14} className="text-blue" aria-hidden />
              Auto-filled from your photos — edit anything that needs a tweak.
            </span>
          )}
        </div>
      </div>
    </WorkflowCard>
  );
}

export function PricingCard({ w }) {
  const conditions = w.categoryMeta.conditions?.length
    ? w.categoryMeta.conditions.map((c) => ({ value: c.enum, label: c.label || conditionLabel(c.enum) }))
    : CONDITIONS.map((c) => ({ value: c, label: conditionLabel(c) }));
  const p = w.priceData;

  return (
    <WorkflowCard
      id="pricing" icon={Coins} title="Pricing & condition"
      hint="Check live comps so you never guess"
      state={w.completion.pricing}
      flagged={w.fixTarget === "price" || w.fixTarget === "condition"}
    >
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
          <Field label={`Price (${w.form.currency || "USD"})`}>
            <Input
              type="number" step="0.01" min="0" inputMode="decimal"
              value={w.form.price}
              needsFix={w.fixTarget === "price"}
              onChange={(e) => w.set("price", e.target.value)}
            />
          </Field>
          <Field label="Quantity">
            <Input
              type="number" min="1" inputMode="numeric"
              value={w.form.quantity}
              onChange={(e) => w.set("quantity", e.target.value)}
            />
          </Field>
          <Field label="Condition" className="col-span-2 sm:col-span-1">
            <Select value={w.form.condition} onChange={(e) => w.set("condition", e.target.value)}>
              {conditions.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
            </Select>
          </Field>
        </div>

        <Field label="Condition description" hint="(what a buyer should know)">
          <Textarea
            rows={2}
            value={w.form.condition_description}
            onChange={(e) => w.set("condition_description", e.target.value)}
          />
        </Field>

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
const PROMO_SUGGESTED = 6;

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
