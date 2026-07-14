import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Image as ImageIcon, Type, FolderTree, ListChecks, Coins, PackageOpen,
  AlignLeft, Search, Plus, X, TrendingUp, ExternalLink, Truck, AlertTriangle,
} from "lucide-react";
import { cn, CONDITIONS, conditionLabel } from "@/lib/utils";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea, Select, Toggle } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { AIStatusInline } from "@/components/ui/AIStatus";
import { useToast } from "@/components/ui/Toaster";
import { WorkflowCard } from "./WorkflowCard";
import { PhotoTile } from "./PhotoTile";

/* The eight workflow cards. Each is presentational; all state lives in
   useListingForm (passed down as `w`). */

export function PhotosCard({ w, onEdit, onSmartCrop, onDelete }) {
  // Drag-to-reorder: swap live as the drag passes over other tiles.
  const dragIndex = useRef(null);
  const [draggingIdx, setDraggingIdx] = useState(null);

  const move = (from, to) => {
    if (from === to || to < 0 || to >= w.form.images.length) return;
    const images = [...w.form.images];
    const [x] = images.splice(from, 1);
    images.splice(to, 0, x);
    w.set("images", images);
  };

  return (
    <WorkflowCard
      id="photos" icon={ImageIcon} title="Photos"
      hint="Drag to reorder — the first photo is your cover. Hover for clean-up, smart crop, or delete"
      state={w.completion.photos} focus={w.cardFocus} flagged={w.fixTarget === "photos"}
    >
      {(w.form.images || []).length ? (
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          <AnimatePresence>
            {w.form.images.map((name, i) => (
              <PhotoTile
                key={name}
                sessionId={w.sessionId}
                name={name}
                version={w.imageVersion}
                index={i}
                count={w.form.images.length}
                isCover={i === 0}
                dragging={draggingIdx === i}
                onMove={move}
                onDragStart={() => { dragIndex.current = i; setDraggingIdx(i); }}
                onDragEnter={() => {
                  if (dragIndex.current !== null && dragIndex.current !== i) {
                    move(dragIndex.current, i);
                    dragIndex.current = i;
                    setDraggingIdx(i);
                  }
                }}
                onDragEnd={() => { dragIndex.current = null; setDraggingIdx(null); }}
                onEdit={() => onEdit(name)}
                onSmartCrop={() => onSmartCrop(name)}
                onDelete={() => onDelete(name)}
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
      state={w.completion.title} focus={w.cardFocus} flagged={w.fixTarget === "title"}
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
      state={w.completion.category} focus={w.cardFocus} flagged={w.fixTarget === "category"}
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
        <div className="flex items-center justify-between gap-3">
          <p className="text-[13px] font-semibold text-ink">
            Suggested eBay categories <span className="font-normal text-ink-faint">— best guess selected</span>
          </p>
          <Button variant="ghost" size="sm" onClick={w.suggestCategories}>
            <Search aria-hidden /> Refresh
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
      state={w.completion.specifics} focus={w.cardFocus} flagged={w.fixTarget === "specifics"}
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

        <div>
          <Button
            variant="ghost"
            onClick={() => w.set("item_specifics", [...w.form.item_specifics, { name: "", value: "" }])}
          >
            <Plus aria-hidden /> Add specific
          </Button>
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
      state={w.completion.pricing} focus={w.cardFocus}
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

        <div className="flex flex-col gap-3">
          <Toggle
            checked={w.form.best_offer_enabled}
            onChange={(v) => w.set("best_offer_enabled", v)}
            label="Allow offers"
            help="Buyers can negotiate — listings with offers enabled usually sell faster."
          />
          <AnimatePresence initial={false}>
            {w.form.best_offer_enabled && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                className="overflow-hidden"
              >
                <div className="grid grid-cols-2 gap-4 max-w-md pt-1">
                  <Field
                    label="Minimum offer"
                    help="Offers below this auto-decline."
                  >
                    <Input
                      type="number" step="0.01" min="0" inputMode="decimal"
                      value={w.form.best_offer_min}
                      placeholder="optional"
                      onChange={(e) => w.set("best_offer_min", e.target.value)}
                    />
                  </Field>
                  <Field
                    label="Auto-accept at"
                    help="Offers at or above this accept instantly."
                  >
                    <Input
                      type="number" step="0.01" min="0" inputMode="decimal"
                      value={w.form.best_offer_accept}
                      placeholder="optional"
                      onChange={(e) => w.set("best_offer_accept", e.target.value)}
                    />
                  </Field>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
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

const isGroundPolicy = (p) => (p.services || []).some(
  (s) => s.toLowerCase().replaceAll("_", "").includes("groundadvantage"));

// Per-listing shipping service = an eBay fulfillment policy on the offer.
// The dropdown lists every policy on the account; USPS Ground Advantage
// (cheapest broadly-applicable service) is selected by default — created
// silently on the account if it doesn't exist yet.
function ShippingServicePicker({ w }) {
  const { ebay, policiesData, setPoliciesData } = useApp();
  const ensured = useRef(false);

  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    api("/api/ebay/policies").then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);

  const policies = policiesData?.policies?.fulfillment || [];
  const accountDefault = policiesData?.selected?.fulfillment_policy_id || "";

  // Default this listing's service: the account preference saved in Settings
  // wins; otherwise USPS Ground Advantage (quietly created when the account
  // has no policies at all). Never overrides a per-listing choice.
  useEffect(() => {
    if (!ebay.connected || !policiesData || w.form.fulfillment_policy_id) return;
    if (accountDefault) {
      w.set("fulfillment_policy_id", accountDefault);
      return;
    }
    const ground = policies.find(isGroundPolicy);
    if (ground) {
      w.set("fulfillment_policy_id", ground.id);
    } else if (!ensured.current) {
      ensured.current = true;
      postJson("/api/ebay/ensure-defaults", {})
        .then((res) => {
          const pol = res.fulfillment;
          if (pol?.id) w.set("fulfillment_policy_id", pol.id);
          setPoliciesData(null); // reload so any new policies show in the list
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ebay.connected, policiesData, w.form.fulfillment_policy_id]);

  if (!ebay.connected) return null;

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
              {p.id === accountDefault ? " · account default" : ""}
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
      state={w.completion.shipping} focus={w.cardFocus}
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
      state={w.completion.description} focus={w.cardFocus} flagged={w.fixTarget === "description"}
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
