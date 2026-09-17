import { useState } from "react";
import { Loader2, Tag, TrendingUp } from "lucide-react";
import { patchJson, postJson } from "@/lib/api";
import { priceView } from "@/lib/priceLookup";
import { cn, formatMoney } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Input } from "@/components/ui/fields";
import {
  AUCTION, AUCTION_BIN, isAuctionFormat, listingFormat,
} from "@/lib/listingFormat";

/* What a draft is ASKING, on the face of the card — and, for an auction, what
 * eBay's own market says it should open at.
 *
 * Price was the last thing on a draft card that could only be changed by
 * opening the editor. Category and format moved out here because they are
 * decisions made per item while looking at the item; price is the same kind
 * and the most common of the three. A seller reviewing a batch of fresh
 * drafts is reading a grid of numbers the AI chose, and the whole job is
 * changing the two or three it got wrong — which cost a round trip into the
 * editor and back per item.
 *
 * WHICH FIELD it edits is the format's answer, not this component's guess
 * (lib/listingFormat): a Buy It Now has a price, a plain auction has only a
 * starting bid, and an auction with a Buy It Now has both. Showing a `price`
 * box on a plain auction would invite a number eBay never reads.
 *
 * `onPick` is handed a patch of changed fields only and owns persistence —
 * the same split CategoryQuickPick and FormatQuickPick use. A card holds a
 * SUMMARY of a listing, loaded whenever /api/listings last ran, so writing
 * the whole thing back is how an edit made in the editor or pulled in by a
 * sync gets overwritten (see main.patch_listing).
 */

// A money field that saves when you LEAVE it, not on every keystroke: this
// control patches the server, and a per-character PATCH would send "1", "12",
// "12." and "12.5" on the way to $12.50.
function MoneyField({ label, value, onCommit, disabled, flagged }) {
  const asText = value == null || value === "" ? "" : String(value);
  const [text, setText] = useState(asText);
  // The saved value can change underneath this field — another tab, a sync,
  // the refresh after our own save normalises 12.5 to 12.50, a suggestion
  // applied from the panel below. Adjusting state during render (React's
  // documented pattern, as in WorkflowCard) keeps the box showing what is
  // stored without an effect and a second paint.
  const [seen, setSeen] = useState(asText);
  if (asText !== seen) { setSeen(asText); setText(asText); }

  const commit = () => {
    const trimmed = text.trim();
    if (trimmed === "") {
      if (value != null && value !== "") onCommit(null);
      return;
    }
    const next = Number(trimmed);
    // Not a number, or a negative one: put the stored value back rather than
    // sending something the listing model will reject.
    if (!Number.isFinite(next) || next < 0) { setText(asText); return; }
    if (next !== Number(value)) onCommit(next);
  };

  return (
    <label className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="text-[11px] font-semibold text-ink-faint">{label}</span>
      <Input
        type="number" step="0.01" min="0" inputMode="decimal"
        className="h-9 text-[13px]"
        aria-label={label}
        placeholder="0.00"
        disabled={disabled}
        needsFix={flagged ? "warn" : undefined}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }}
      />
    </label>
  );
}

/** The two numbers a listing can be asking, and whether its format uses each.
 *  One place, so the boxes above and the suggestion rows below cannot drift
 *  into two different answers about what a plain auction has. */
function priceFields(listing) {
  const fmt = listingFormat(listing);
  return {
    format: fmt,
    // A plain auction has no Buy It Now at all — `price` is unused, and eBay
    // never reads it for that format.
    bin: fmt !== AUCTION,
    start: isAuctionFormat(fmt),
  };
}

/* The eBay-backed recommendation, once the seller asks for it.
 *
 * Two numbers off ONE lookup, because eBay sells an item two ways and they
 * are not the same number (backend services/pricing.auction_start): the Buy
 * It Now price is what the seller wants for the item, the opening bid is the
 * floor under a sale the bidders price — and a no-reserve auction that draws
 * one bidder ends AT that floor. Quoting the first as the second opens a $120
 * jacket at $120; quoting the second as the first sells it for a dollar.
 */
function SuggestionPanel({ data, fields, currency, onApply, applied }) {
  const say = (text) => (
    <p className="px-1 text-[12px] leading-snug text-ink-secondary">{text}</p>
  );
  // Something this screen knows without asking eBay (there was no title to
  // search on). Answered before priceView, whose job is to report what a
  // LOOKUP found — handed this it would read it as a market with nothing
  // comparable in it, and send the seller off to edit a title that is the
  // thing it just asked for.
  if (data.note) return say(data.note);
  // "We couldn't check" and "the market has nothing like this" are different
  // answers, and only one of them is about the listing. See lib/priceLookup.
  const view = priceView(data);
  if (view.kind !== "estimate") return say(view.message);

  const rows = [];
  if (fields.start && data.auction) {
    rows.push({
      key: "auction_start_price",
      label: "Open the bidding at",
      amount: data.auction.start_price,
      why: data.auction.basis,
    });
  }
  if (fields.bin && data.suggestion) {
    rows.push({
      key: "price",
      label: fields.start ? "Buy It Now at" : "Price it at",
      amount: data.suggestion.price,
      why: data.suggestion.basis,
    });
  }
  if (!rows.length) return null;

  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((row) => (
        <button
          key={row.key}
          type="button"
          onClick={() => onApply(row.key, row.amount)}
          title={row.why}
          className={cn(
            "w-full flex items-center justify-between gap-2 text-left rounded-input border",
            "px-2.5 py-1.5 text-[12px] cursor-pointer transition-colors duration-150",
            applied[row.key] === row.amount
              ? "border-blue bg-blue-soft"
              : "border-line bg-card hover:border-line-strong",
          )}
        >
          <span className="min-w-0 text-ink leading-snug">{row.label}</span>
          <span className="shrink-0 font-display font-bold text-blue tabular-nums">
            {formatMoney(row.amount, currency)}
          </span>
        </button>
      ))}
      {/* The basis, once, under the rows it explains — a number a seller can
          overrule on purpose is one whose evidence is on screen beside it. */}
      <p className="px-1 text-[11px] leading-snug text-ink-faint">{rows[0].why}</p>
    </div>
  );
}

export function PriceQuickEdit({ listing, onPick, saving, className }) {
  const l = listing || {};
  const { health } = useApp();
  const currency = l.currency || "USD";
  const fields = priceFields(l);
  // null | {loading} | {note} | the /api/price-suggestions answer (which
  // carries `checked`, so a failed lookup can say so rather than passing
  // itself off as an empty market).
  const [market, setMarket] = useState(null);

  const check = async () => {
    // Brand and title, the same query the editor's "Check market price" runs
    // (useListingForm.checkMarketPrice) — one question, one answer, wherever
    // it is asked from.
    const query = [l.brand, l.title].filter(Boolean).join(" ").trim();
    if (!query) {
      setMarket({ note: "Add a title first — the price check runs on it." });
      return;
    }
    setMarket({ loading: true });
    try {
      const data = await postJson("/api/price-suggestions", {
        query,
        category_id: l.category_id || null,
        condition: l.condition || null,
      });
      setMarket(data);
    } catch {
      // NOT "no comparable listings": a lookup that never ran is not evidence
      // about the market. `checked: false` is how priceView says so, and this
      // is the case where the request itself threw.
      setMarket({ checked: false });
    }
  };

  // Which suggestion row, if any, the listing is already sitting on — read
  // off the stored numbers, so applying one and the refresh landing marks it
  // without this component tracking what it sent.
  const applied = {
    price: Number(l.price),
    auction_start_price: Number(l.auction_start_price),
  };

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-end gap-2">
        <Tag size={14} className="mb-2.5 shrink-0 text-ink-faint" aria-hidden />
        {fields.start && (
          <MoneyField
            label={`Starting bid (${currency})`}
            value={l.auction_start_price}
            disabled={saving}
            flagged={!(Number(l.auction_start_price) > 0)}
            onCommit={(v) => onPick({ auction_start_price: v })}
          />
        )}
        {fields.bin && (
          <MoneyField
            label={fields.format === AUCTION_BIN
              ? `Buy It Now (${currency})` : `Price (${currency})`}
            value={l.price}
            disabled={saving}
            flagged={!(Number(l.price) > 0)}
            onCommit={(v) => onPick({ price: v })}
          />
        )}
      </div>

      {/* The market, on request rather than on render: every press is a live
          eBay call against an allowance shared by every seller (see
          main._taxonomy_guard), and a grid of thirty draft cards that each
          asked on sight would spend it in one screen. */}
      {health.taxonomy_configured && (
        <button
          type="button"
          onClick={check}
          disabled={saving || !!market?.loading}
          className={cn(
            "inline-flex items-center gap-1.5 self-start rounded-input px-1 py-1",
            "text-[12px] font-semibold cursor-pointer transition-colors duration-150",
            "text-blue hover:bg-bg-sunken disabled:opacity-60",
          )}
        >
          {market?.loading
            ? <Loader2 size={13} className="animate-spin" aria-hidden />
            : <TrendingUp size={13} aria-hidden />}
          {market?.loading
            ? "Checking eBay…"
            : fields.start
              // Named for the number it is about to recommend. An auction is
              // started, not priced, and "Suggest a price" on a format whose
              // only field is an opening bid is a promise about the wrong one.
              ? "Suggest an opening bid"
              : "Suggest a price"}
        </button>
      )}
      {market && !market.loading && (
        <SuggestionPanel
          data={market}
          fields={fields}
          currency={currency}
          applied={applied}
          onApply={(field, amount) => onPick({ [field]: amount })}
        />
      )}
    </div>
  );
}

/* The same control, wired to a SAVED draft — type, patch, refresh.
 *
 * Drafts only, like the category and format controls it sits with. A live
 * listing's price is revisable on eBay, but only through a revise: a PATCH
 * here would change the number this app shows and leave eBay showing the old
 * one, with nothing on either screen saying they disagree. Repricing a live
 * listing keeps its existing routes — the editor's save, and the dashboard's
 * "Lower prices" group (POST /api/ebay/lower-prices) — both of which push the
 * change to eBay.
 */
export function DraftPriceEdit({ item, className }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const save = async (patch) => {
    setSaving(true);
    try {
      // Named fields only, never the whole listing this card happens to hold.
      await patchJson(`/api/listings/${item.id}`, patch);
      // The card's Publish gate reads these numbers (a draft with no price is
      // blocked, and so is an auction with no starting bid), so the cache has
      // to see the change too.
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't save the price: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };
  return (
    <PriceQuickEdit listing={item.listing} onPick={save} saving={saving}
      className={className} />
  );
}
