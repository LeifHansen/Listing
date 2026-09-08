import { useState } from "react";
import { Gavel } from "lucide-react";
import { patchJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Input, Select } from "@/components/ui/fields";
import {
  AUCTION_BIN, FORMAT_HELP, LISTING_FORMATS, isAuctionFormat, listingFormat,
} from "@/lib/listingFormat";

/* How a draft SELLS — Buy It Now, auction, or both — on the face of the card.
 *
 * The choice existed only inside the full editor's Pricing card, so every
 * draft on every grid was a Buy It Now until somebody opened it and changed
 * it one at a time. That is the wrong place for it: format is a decision a
 * seller makes per item while looking at the item (this one is common, run it
 * as a Buy It Now; this one is rare, let it be bid up), which is what a grid
 * of cards is for.
 *
 * Picking an auction reveals the money that format needs, because it is the
 * pick that creates the gap: an auction is priced by its STARTING BID, and a
 * draft switched to one has none until somebody types it. Leaving it to be
 * discovered later would flip a publishable draft to "needs info" with the
 * field that fixes it two screens away. (Auction LENGTH stays in the editor —
 * it defaults to 7 days, and the format chip on the card says which.)
 *
 * `onPick` is handed a patch of changed fields only and owns persistence, the
 * same split CategoryQuickPick uses — the bulk queue saves into its own local
 * copy, a saved draft PATCHes (DraftFormatEdit below).
 */

// A money field that saves when you LEAVE it, not on every keystroke: this
// control patches the server, and a per-character PATCH would send "1", "12",
// "12." and "12.5" on the way to $12.50.
function MoneyField({ label, value, onCommit, disabled, flagged }) {
  const asText = value == null || value === "" ? "" : String(value);
  const [text, setText] = useState(asText);
  // The saved value can change underneath this field — another tab, a sync,
  // the refresh after our own save normalises 12.5 to 12.50. Adjusting state
  // during render (React's documented pattern, as in WorkflowCard) keeps the
  // box showing what is stored without an effect and a second paint.
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

export function FormatQuickPick({ listing, onPick, saving, className }) {
  const l = listing || {};
  const fmt = listingFormat(l);
  const auction = isAuctionFormat(fmt);
  const currency = l.currency || "USD";

  const pickFormat = (next) => {
    if (next === fmt) return;
    // The format alone. Switching does NOT move money between the two fields:
    // a Buy It Now price is what the seller wants for the item and a starting
    // bid is where they are willing to let it open, and quietly copying one
    // into the other would list a $120 jacket opening at $120 (nobody bids)
    // or, the other way, put a 99¢ opener up as the asking price.
    onPick({ listing_format: next });
  };

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-center gap-1.5" title={FORMAT_HELP[fmt]}>
        <Gavel size={14} className="shrink-0 text-ink-faint" aria-hidden />
        <Select
          aria-label="Selling format"
          className="h-9 text-[13px]"
          disabled={saving}
          value={fmt}
          onChange={(e) => pickFormat(e.target.value)}
        >
          {LISTING_FORMATS.map(([value, label]) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </Select>
      </div>
      {auction && (
        <div className="flex items-end gap-2">
          <MoneyField
            label={`Starting bid (${currency})`}
            value={l.auction_start_price}
            disabled={saving}
            flagged={!(Number(l.auction_start_price) > 0)}
            onCommit={(v) => onPick({ auction_start_price: v })}
          />
          {/* Only the format that HAS a Buy It Now shows one. On a plain
              auction `price` is unused, and offering a box for it here would
              invite a number that never reaches eBay. */}
          {fmt === AUCTION_BIN && (
            <MoneyField
              label={`Buy It Now (${currency})`}
              value={l.price}
              disabled={saving}
              flagged={!(Number(l.price) > 0)}
              onCommit={(v) => onPick({ price: v })}
            />
          )}
        </div>
      )}
    </div>
  );
}

/* The same control, wired to a SAVED draft — pick, patch, refresh.
 *
 * Drafts only. eBay does not let a live listing change format (see
 * services/ebay_trading.REVISABLE_FIELDS), so every caller gates this on
 * isDraft the way it already gates DraftCategoryEdit.
 */
export function DraftFormatEdit({ item, className }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const save = async (patch) => {
    setSaving(true);
    try {
      // Named fields only, never the whole listing this card happens to hold
      // — the copy on a card is from the last /api/listings load, and sending
      // it back overwrites anything edited since (see main.patch_listing).
      await patchJson(`/api/listings/${item.id}`, patch);
      // The card's Publish gate reads the format (an auction with no starting
      // bid is blocked), so the cache has to see the change too.
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't save the selling format: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };
  return (
    <FormatQuickPick listing={item.listing} onPick={save} saving={saving}
      className={className} />
  );
}
