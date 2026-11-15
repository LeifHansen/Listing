import { Gavel } from "lucide-react";
import { cn } from "@/lib/utils";
import { Select } from "@/components/ui/fields";
import {
  FORMAT_HELP, LISTING_FORMATS, listingFormat,
} from "@/lib/listingFormat";
import { useDraftPatch } from "./CategoryQuickPick";

/* How a draft SELLS — Buy It Now, auction, or both — on the face of the card.
 *
 * The choice existed only inside the full editor's Pricing card, so every
 * draft on every grid was a Buy It Now until somebody opened it and changed
 * it one at a time. That is the wrong place for it: format is a decision a
 * seller makes per item while looking at the item (this one is common, run it
 * as a Buy It Now; this one is rare, let it be bid up), which is what a grid
 * of cards is for.
 *
 * The MONEY the pick needs is next door, in PriceQuickEdit. It used to be
 * here, because picking an auction is what creates the gap — a draft switched
 * to one is priced by a starting bid it does not have yet, and leaving that
 * to be discovered later flips a publishable draft to "needs info" with the
 * field that fixes it two screens away. That still holds; what changed is
 * that the price control sits on every card now and follows the format, so
 * the starting-bid box appears the moment this select changes and there is no
 * second copy of it to drift. (Auction LENGTH stays in the editor — it
 * defaults to 7 days, and the format chip on the card says which.)
 *
 * `onPick` is handed a patch of changed fields only and owns persistence, the
 * same split CategoryQuickPick uses — the editor holds the change in its
 * form, a saved draft PATCHes (DraftFormatEdit below).
 */
export function FormatQuickPick({ listing, onPick, saving, className }) {
  const fmt = listingFormat(listing);

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
    <div className={cn("flex items-center gap-1.5", className)}
      title={FORMAT_HELP[fmt]}>
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
  );
}

/* The same control, wired to a SAVED draft — pick, patch, refresh.
 *
 * Drafts only. eBay does not let a live listing change format (see
 * services/ebay_trading.REVISABLE_FIELDS), so every caller gates this on
 * isDraft the way it already gates DraftCategoryEdit.
 */
export function DraftFormatEdit({ item, className }) {
  const { saving, save } = useDraftPatch(item, "the selling format");
  return (
    <FormatQuickPick listing={item.listing} onPick={save} saving={saving}
      className={className} />
  );
}
