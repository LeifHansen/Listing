import { Search } from "lucide-react";
import { cn, formatMoney, mediaUrl } from "@/lib/utils";
import { conditionLabel } from "@/lib/conditions";
import { AUCTION, isAuctionFormat, normalizeFormat } from "@/lib/listingFormat";
import { usePrefs } from "@/lib/prefs";
import { useApp } from "@/store";
import { TITLE_MAX } from "./blockers";

/* How this listing will look in eBay's search results, as the seller types.
 *
 * The title is the single biggest lever a seller has -- every guide says so,
 * and eBay's own numbers do -- and until now the editor showed it as a text
 * box with a character count. This shows it as a buyer meets it: the first
 * photo, the title cut where eBay cuts it, the condition, the price, and the
 * shipping and offers lines the account defaults will put under it. None of
 * the competing listing tools draws this, and it costs nothing: every value
 * is already in the form or the account's policies.
 *
 * Read-only, and honest about what it does not know: the shipping line is
 * built from the policy the listing will go out with (the listing's own, or
 * the account default), and when neither is known it says nothing rather
 * than "Free shipping". */
export function SearchPreview({ w }) {
  const { policiesData } = useApp();
  const prefs = usePrefs();
  const f = w.form;

  const images = f.images || [];
  const first = images[0]
    ? `${mediaUrl(w.sessionId, images[0])}?v=${w.imageVersions[images[0]] || w.imageBase}`
    : (f.image_urls || [])[0] || "";

  const fmt = normalizeFormat(f.listing_format);
  const auction = isAuctionFormat(fmt);
  const currency = f.currency || "USD";
  const price = formatMoney(f.price, currency);
  const opener = formatMoney(f.auction_start_price, currency);

  // The policy this listing goes out with: its own override, else the
  // account default from Settings. Its summary names the carrier.
  const policyId = f.fulfillment_policy_id || policiesData?.selected?.fulfillment_policy_id;
  const policy = policyId
    ? (policiesData?.policies?.fulfillment || []).find((p) => p.id === policyId) : null;
  const shipping = policy?.summary ? policy.summary.split(" · ")[0] : "";
  const offers = !auction && !!prefs?.allow_offers;

  const title = (f.title || "").trim();
  const over = title.length > TITLE_MAX;

  return (
    <div
      className="bg-card rounded-card border border-line shadow-card p-4 sm:p-5"
      data-search-preview
    >
      <p className="flex items-center gap-1.5 text-[12.5px] font-bold text-ink-secondary uppercase tracking-wide mb-3">
        <Search size={13} aria-hidden /> How it looks in search
      </p>
      <div className="flex gap-3.5">
        <div className="size-24 sm:size-28 shrink-0 rounded-tile overflow-hidden bg-bg-sunken grid place-items-center">
          {first ? (
            <img src={first} alt="" className="size-full object-contain" />
          ) : (
            <span className="text-[11px] text-ink-faint text-center px-2">No photo yet</span>
          )}
        </div>
        <div className="min-w-0 flex-1 flex flex-col gap-1">
          {/* Two lines, like eBay's result card; the counter on the title
              field says how many characters are left, this says what the
              buyer sees. */}
          <p
            className={cn("text-[15px] leading-snug text-ink line-clamp-2",
              !title && "text-ink-faint italic")}
            data-preview-title
          >
            {title ? title.slice(0, TITLE_MAX) : "Your title appears here"}
          </p>
          {over && (
            <p className="text-[11.5px] text-error font-semibold">
              Cut at {TITLE_MAX} characters — eBay refuses a longer title.
            </p>
          )}
          <p className="text-[12.5px] text-ink-secondary">
            {conditionLabel(f.condition) || "Condition"}
          </p>
          <p className="font-display text-[19px] font-bold text-ink leading-tight" data-preview-price>
            {auction
              ? (opener ? `${opener}` : "Starting bid")
              : (price || "Price")}
            {auction && <span className="text-[12px] font-semibold text-ink-secondary"> starting bid</span>}
            {fmt !== AUCTION && auction && price && (
              <span className="text-[12px] font-semibold text-ink-secondary"> · Buy It Now {price}</span>
            )}
          </p>
          {(shipping || offers) && (
            <p className="text-[12px] text-ink-secondary" data-preview-extras>
              {[shipping, offers ? "or Best Offer" : ""].filter(Boolean).join(" · ")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
