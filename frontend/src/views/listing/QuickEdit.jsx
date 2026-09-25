import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { postJson, PUBLISH_TIMEOUT_MS } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/fields";
import {
  AUCTION, AUCTION_BIN, isAuctionFormat, listingFormat,
} from "@/lib/listingFormat";
import { isDraft, isLive } from "@/lib/listingsView";
import { ConditionPicker } from "./ConditionPicker";
import { ShippingPolicySelect, useFulfillmentPolicies } from "./ShippingPolicySelect";
import { blockedReason, outcomeUnknown } from "./publishShared";

/* A listing's "Quick edit" panel — the fields a seller changes most, right
 * under the card, without opening the editor.
 *
 * The grid could only OPEN a live listing. The draft controls (category,
 * format, price) were kept off live cards on purpose: a number changed there
 * was a local write, and a live listing changed only here leaves this app and
 * eBay disagreeing with nothing on either screen saying so. The panel does
 * not have that problem, because it does not write locally on its own: it
 * goes through POST /api/listings/{id}/quick-edit, which saves a draft and,
 * for a live listing, revises it on every marketplace it is live on in the
 * same request (see main.quick_edit_listing). "Saved" and "on eBay" are one
 * answer.
 *
 * Collapsed by default and opened per card (ListingCard's toggle), so a grid
 * of forty listings is still a grid of forty listings.
 *
 * WHAT IT SENDS is only what the seller touched. The card holds whatever
 * /api/listings last loaded, and every untouched field keeps showing the
 * card's CURRENT value — so a title a sync brought in while the panel was
 * open is on screen, and is not sent back over itself with the value from
 * when the panel opened. On a live listing that matters twice over: a revise
 * carries exactly the fields that were sent, and a stale one overwrites
 * whatever eBay has now.
 */

// eBay's own ceiling; the server refuses anything longer rather than cutting
// a title the seller typed (main._quick_edit_value).
export const TITLE_MAX = 80;

// The id the toggle's aria-controls points at, shared so the card and the
// panel it opens cannot disagree about it.
export const quickEditPanelId = (id) => `quick-edit-${id}`;

// Collapse, and hand the keyboard back to the toggle that opened the panel —
// otherwise Escape or Cancel drops focus to the top of the page, forty cards
// away from where the seller was working.
function closeAndRefocus(id, onClose) {
  onClose?.();
  requestAnimationFrame(() => {
    [...document.querySelectorAll("[data-quick-edit-toggle]")]
      .find((el) => el.dataset.quickEditToggle === String(id))?.focus();
  });
}

// Which listings can be quick-edited at all. A sale is the archive of one
// finished sale, and an ended listing is relisted rather than revised — eBay
// will not revise a finished item — so both open the editor as they always
// have.
export function canQuickEdit(item) {
  return isDraft(item) || isLive(item) || item?.status === "unlisted";
}

/* The fields the panel offers for this listing, in the order it shows them.
 *
 * Price is left to the draft card's own control (PriceQuickEdit), which knows
 * the format and carries eBay's comps — two price boxes under one card would
 * be two answers to one question. A plain auction has no Buy It Now to type,
 * and its opening bid cannot move once it is live. An auction of either kind
 * sells exactly one item, so it has no stock to change. And a listing with
 * size or colour variations is refused whole by eBay's revise, so the only
 * thing left to change from here is what the seller paid, which never goes
 * to eBay. */
export function quickEditFields(item) {
  const l = item?.listing || {};
  if (isLive(item) && l.has_variations) return ["purchase_price"];
  const fmt = listingFormat(l);
  const price = !isDraft(item) && fmt !== AUCTION;
  const stock = !isAuctionFormat(fmt);
  return ["title", ...(price ? ["price"] : []), ...(stock ? ["quantity"] : []),
    "condition", "brand", "fulfillment_policy_id", "purchase_price"];
}

const text = (v) => (v == null ? "" : String(v));
const marketLabel = (key) => (key === "ebay"
  ? "eBay" : key.charAt(0).toUpperCase() + key.slice(1));

// A listing, in the shape the inputs hold: numbers as the strings typed.
function valuesOf(l) {
  return {
    title: text(l.title),
    price: text(l.price),
    quantity: text(l.quantity ?? 1),
    condition: text(l.condition),
    condition_descriptors: l.condition_descriptors || [],
    brand: text(l.brand),
    fulfillment_policy_id: text(l.fulfillment_policy_id),
    purchase_price: text(l.purchase_price),
  };
}

const MONEY = new Set(["price", "purchase_price"]);
const asNumber = (v) => (text(v).trim() === "" ? null : Number(v));

function same(field, a, b) {
  if (field === "condition_descriptors") {
    return JSON.stringify(a || []) === JSON.stringify(b || []);
  }
  if (MONEY.has(field) || field === "quantity") return asNumber(a) === asNumber(b);
  return text(a).trim() === text(b).trim();
}

// What goes over the wire for one field.
function wire(field, value) {
  if (MONEY.has(field)) return asNumber(value);
  if (field === "quantity") return Number(value);
  if (field === "condition_descriptors") return value || [];
  return text(value).trim();
}

/* What the seller has typed that the server would refuse — said beside the
 * field, and the reason the save button is off. The server checks all of it
 * again (it is the authority); this is so the seller is not sent a round trip
 * to find out their price box is empty. */
function problemsWith(values, fields, { live }) {
  const out = {};
  if (fields.includes("title") && !values.title.trim()) out.title = "A listing needs a title.";
  const money = (field, label) => {
    const raw = values[field].trim();
    if (raw === "") return;
    const n = Number(raw);
    if (!Number.isFinite(n)) out[field] = `${label} has to be a number.`;
    else if (n < 0) out[field] = `${label} can't be negative.`;
  };
  if (fields.includes("price")) {
    money("price", "The price");
    if (!out.price && live && !(Number(values.price) > 0)) {
      out.price = "A live listing needs a price above zero.";
    }
  }
  if (fields.includes("purchase_price")) money("purchase_price", "What you paid");
  if (fields.includes("quantity")) {
    const n = Number(values.quantity);
    if (values.quantity.trim() === "" || !Number.isInteger(n) || n < 0) {
      out.quantity = "A whole number, zero or more.";
    }
  }
  return out;
}

// The category's condition ladder, asked for once the panel is open — never
// on render, since every card on the grid would ask at once.
function useConditions(categoryId) {
  const { health } = useApp();
  const cid = text(categoryId).trim();
  const enabled = !!health.taxonomy_configured && !!cid;
  const [state, setState] = useState({ for: "", list: [], checked: true });
  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    postJson("/api/item-conditions", { category_id: cid })
      // `checked: false` is the route saying it could not ask eBay, and a
      // request that failed is the same news: the picker falls back to the
      // general list and says so.
      .catch(() => ({ conditions: [], checked: false }))
      .then((res) => {
        if (alive) {
          setState({ for: cid, list: res.conditions || [],
                     checked: res.checked !== false });
        }
      });
    return () => { alive = false; };
  }, [enabled, cid]);
  // Until eBay answers — and wherever it cannot be asked — the picker offers
  // the general ladder with no warning, exactly as the editor does.
  return enabled && state.for === cid ? state : { list: [], checked: true };
}

// Compact controls for a card's width. The condition picker draws its own
// selects, so it is sized from outside (see the wrapper below).
const COMPACT = "h-9 text-[13px] px-3";

/**
 * @param item    the saved listing, as the grid holds it.
 * @param layout  "grid" (a card's width: two columns) or "list" (a row's
 *                width: the fields run across).
 * @param onClose collapse the panel. Called after a save that landed, and by
 *                Cancel / Escape, which discard what was typed.
 */
export function QuickEditPanel({ item, layout = "grid", onClose, className }) {
  const { patchListing, invalidateListings } = useApp();
  const { toast } = useToast();
  const { connected: shipsFromEbay, policies } = useFulfillmentPolicies();
  const l = item.listing || {};
  const live = isLive(item);
  const list = layout === "list";
  const current = valuesOf(l);
  const conditions = useConditions(l.category_id);

  // Only the fields the seller touched; every other field reads the card's
  // current value (see the note at the top).
  const [edits, setEdits] = useState({});
  const [saving, setSaving] = useState(false);
  // What happened to the last save, when it did not simply land: the reason,
  // and a sentence on where that leaves the change.
  const [problem, setProblem] = useState(null);
  // The patch a refusal left SAVED here. A change no marketplace took is
  // normally put back (the card keeps showing what buyers see, and what was
  // typed still differs from it, so Save sends it again). When it could not
  // be put back — or one marketplace took it and another did not — the card
  // already holds those values and nothing reads as changed any more; "Try
  // again" is how the same change is offered a second time (the server
  // re-sends a named field that is still pending).
  const [retry, setRetry] = useState(null);

  const fields = quickEditFields(item);
  const shipping = fields.includes("fulfillment_policy_id") && shipsFromEbay
    && policies.length > 0;
  const values = { ...current, ...edits };
  const set = (patch) => setEdits((e) => ({ ...e, ...patch }));
  const problems = problemsWith(values, fields, { live });

  // The patch this save would send: touched fields that differ from what the
  // card holds now. A condition and its details travel together, because
  // changing the first changes which second answers still apply.
  const patch = {};
  for (const field of Object.keys(edits)) {
    const offered = fields.includes(field)
      || (field === "condition_descriptors" && fields.includes("condition"));
    if (offered && !same(field, edits[field], current[field])) {
      patch[field] = wire(field, edits[field]);
    }
  }
  if ("condition" in patch || "condition_descriptors" in patch) {
    patch.condition = wire("condition", values.condition);
    patch.condition_descriptors = wire("condition_descriptors", values.condition_descriptors);
  }
  const changed = Object.keys(patch).length > 0;
  const blocked = Object.keys(problems).length > 0;

  // Where a save of this listing lands, in words, for the button.
  const targets = [
    ...(live && l.ebay_listing_id ? ["ebay"] : []),
    ...Object.entries(l.marketplaces || {})
      .filter(([key, st]) => key !== "ebay" && st?.status === "published")
      .map(([key]) => key),
  ];
  const where = targets.map(marketLabel).join(" & ");
  // The button says where a save goes — "Update eBay" — whenever it goes
  // anywhere. What the seller paid is the one change that never does.
  const sends = live && !!where && !(changed && Object.keys(patch)
    .every((f) => f === "purchase_price"));

  const send = async (body) => {
    setSaving(true);
    setProblem(null);
    try {
      // A revise waits on eBay, not on us — the publish deadline, not the
      // default one, or a slow revise that landed is reported as lost.
      const res = await postJson(`/api/listings/${item.id}/quick-edit`, body,
        live ? { timeoutMs: PUBLISH_TIMEOUT_MS } : undefined);
      // The server's copy of the record, on the card now; the refetch behind
      // it makes the whole store authoritative again.
      if (res.listing) {
        patchListing(item.id, { listing: res.listing,
                                ...(res.status ? { status: res.status } : {}) });
      }
      invalidateListings();
      if (res.ok) {
        setRetry(null);
        toast(res.message || "Saved.", { kind: "success" });
        closeAndRefocus(item.id, onClose);
        return;
      }
      if (outcomeUnknown(res)) {
        // Not refused, and not something to press again blind: the change may
        // well be on eBay already. The sentence says to look.
        toast(res.message, { kind: "warning" });
        closeAndRefocus(item.id, onClose);
        return;
      }
      // Refused. The panel stays open on the reason — eBay's own, improved on
      // where the app worked out a better one (publishShared.blockedReason) —
      // with what was typed still in the boxes, one tap from going again.
      const several = Object.keys(res.results || {}).length > 1;
      setProblem({
        reason: several ? res.message : blockedReason(res, res.message || "It wasn't taken."),
        note: res.saved
          ? "Your change is saved here, and goes with the next update."
          : "Nothing was changed.",
      });
      setRetry(res.saved ? body : null);
    } catch (e) {
      if (e?.unknownOutcome) {
        toast(e.message, { kind: "warning" });
        invalidateListings();
        closeAndRefocus(item.id, onClose);
        return;
      }
      // Refused before anything was written (a value the server will not
      // take, eBay not connected): said in the panel, beside the fields.
      setProblem({ reason: e.message, note: "" });
    } finally {
      setSaving(false);
    }
  };

  const submit = (e) => {
    e.preventDefault();
    if (saving || blocked) return;
    if (changed) send(patch);
    else if (retry) send(retry);
  };

  // Column spans, per layout. A card is two columns; a list row is eight on
  // a wide screen. The spans are never mixed across the two: a list-width
  // span inside a card's two columns makes the grid invent six more.
  const span = {
    half: list ? "col-span-1 sm:col-span-2" : "col-span-1",
    wide: list ? "col-span-2 sm:col-span-4" : "col-span-2",
    full: list ? "col-span-2 sm:col-span-8" : "col-span-2",
  };
  const hint = (field) => problems[field] && (
    <span className="text-[12px] font-medium text-warning">{problems[field]}</span>
  );
  const fmt = listingFormat(l);

  return (
    <form
      id={quickEditPanelId(item.id)}
      aria-label={`Quick edit: ${l.title || item.title || "this listing"}`}
      onSubmit={submit}
      onKeyDown={(e) => {
        if (e.key === "Escape") { e.stopPropagation(); closeAndRefocus(item.id, onClose); }
      }}
      className={cn(
        "rounded-card border border-line bg-card shadow-card p-3.5",
        "grid gap-x-3 gap-y-2.5",
        list ? "grid-cols-2 sm:grid-cols-8" : "grid-cols-2",
        className,
      )}
    >
      {fields.includes("title") && (
        <Field label="Title" hint={`${values.title.length}/${TITLE_MAX}`}
          className={span.wide}>
          {list ? (
            <Input
              className={COMPACT}
              value={values.title}
              maxLength={TITLE_MAX}
              autoFocus
              needsFix={problems.title ? "warn" : undefined}
              onChange={(e) => set({ title: e.target.value })}
            />
          ) : (
            // Three lines at a card's width, so the whole of an 80-character
            // title is on screen while it is edited. Still one line of text:
            // Enter saves, as it does in every other box here.
            <Textarea
              className="py-2 px-3 text-[13px] leading-snug resize-none"
              rows={3}
              value={values.title}
              maxLength={TITLE_MAX}
              autoFocus
              needsFix={problems.title ? "warn" : undefined}
              onChange={(e) => set({ title: e.target.value.replace(/\s*\n\s*/g, " ") })}
              onKeyDown={(e) => {
                if (e.key === "Enter") { e.preventDefault(); e.currentTarget.form?.requestSubmit(); }
              }}
            />
          )}
          {hint("title")}
        </Field>
      )}
      {fields.includes("price") && (
        <Field
          label={fmt === AUCTION_BIN ? `Buy It Now (${l.currency || "USD"})`
            : `Price (${l.currency || "USD"})`}
          className={span.half}>
          <Input
            className={COMPACT}
            type="number" step="0.01" min="0" inputMode="decimal"
            placeholder="0.00"
            value={values.price}
            needsFix={problems.price ? "warn" : undefined}
            onChange={(e) => set({ price: e.target.value })}
          />
          {hint("price")}
        </Field>
      )}
      {fields.includes("quantity") && (
        <Field label={live ? "Available" : "Quantity"}
          help={live ? "How many are still for sale on eBay, after what has sold." : undefined}
          className={span.half}>
          <Input
            className={COMPACT}
            type="number" step="1" min="0" inputMode="numeric"
            value={values.quantity}
            needsFix={problems.quantity ? "warn" : undefined}
            onChange={(e) => set({ quantity: e.target.value })}
          />
          {hint("quantity")}
        </Field>
      )}
      {fields.includes("condition") && (
        // The picker renders its own cells (a trading card grows a second
        // question), each a grid item here; sized down to a card's controls
        // from outside, since it draws its own <select>s.
        <div className={cn("contents [&_select]:h-9 [&_select]:text-[13px] [&_select]:pl-3",
          "[&_input]:h-9 [&_input]:text-[13px] [&_input]:px-3")}>
          <ConditionPicker
            conditions={conditions.list}
            checked={conditions.checked}
            condition={values.condition}
            descriptors={values.condition_descriptors}
            onChange={(next) => set(next)}
            cellClassName={list ? "col-span-2 sm:col-span-2" : span.full}
          />
        </div>
      )}
      {fields.includes("brand") && (
        <Field label="Brand" className={span.half}>
          <Input
            className={COMPACT}
            value={values.brand}
            onChange={(e) => set({ brand: e.target.value })}
          />
        </Field>
      )}
      {fields.includes("purchase_price") && (
        <Field label="You paid"
          help="What it cost you — for the profit on the sale. Only you see this; it never goes to eBay."
          className={span.half}>
          <Input
            className={COMPACT}
            type="number" step="0.01" min="0" inputMode="decimal"
            placeholder="0.00"
            value={values.purchase_price}
            needsFix={problems.purchase_price ? "warn" : undefined}
            onChange={(e) => set({ purchase_price: e.target.value })}
          />
          {hint("purchase_price")}
        </Field>
      )}
      {shipping && (
        <Field label="Shipping policy" className={list ? span.half : span.wide}>
          <ShippingPolicySelect
            className={cn(COMPACT, "pr-9")}
            value={values.fulfillment_policy_id}
            onChange={(id) => set({ fulfillment_policy_id: id })}
          />
        </Field>
      )}
      {isLive(item) && l.has_variations && (
        <p className={cn(span.full, "text-[12px] leading-snug text-ink-secondary")}>
          This listing has size or colour variations. Change its title, price
          and stock in eBay Seller Hub — editing them here could remove a
          variation.
        </p>
      )}

      {problem && (
        <p role="alert" className={cn(span.full,
          "flex items-start gap-1.5 rounded-input border px-2.5 py-2",
          "border-warning/35 bg-warning-soft text-[12px] font-semibold leading-snug text-ink")}>
          <AlertTriangle size={13} className="mt-px shrink-0 text-warning" aria-hidden />
          <span className="min-w-0">
            {problem.reason}
            {problem.note && (
              <span className="font-medium text-ink-secondary"> {problem.note}</span>
            )}
          </span>
        </p>
      )}

      <div className={cn(span.full, "flex flex-wrap items-center justify-end gap-2 pt-0.5")}>
        <Button variant="ghost" size="sm" onClick={() => closeAndRefocus(item.id, onClose)}
          disabled={saving}>
          Cancel
        </Button>
        <Button variant="primary" size="sm" type="submit" loading={saving}
          disabled={blocked || (!changed && !retry)}
          title={blocked ? Object.values(problems)[0]
            : !changed && !retry ? "Change something first" : undefined}>
          {!changed && retry ? "Try again"
            : sends ? `Update ${where}` : "Save"}
        </Button>
      </div>
    </form>
  );
}
