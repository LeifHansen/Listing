import { memo, useCallback, useState } from "react";
import { motion } from "framer-motion";
import {
  ImageOff, ArrowRight, Trash2, Eye, Heart, RotateCcw, RotateCw, Loader2,
  SkipForward, Undo2, Clock, AlertTriangle, Ban, HandCoins, Gavel, Timer,
} from "lucide-react";
import { cn, formatMoney, mediaUrl, timeUntil } from "@/lib/utils";
import { auctionEndLabel, useAuctionCountdown } from "@/lib/auctionClock";
import {
  ConfidenceChip, FormatBadge, OriginBadge, PriceBadge, StatusBadge,
} from "@/components/ui/badges";
import { hasSalePrice, saleDiscount, salePrice } from "@/lib/sales";
import { askingPrice, formatSummary, isAuctionFormat } from "@/lib/listingFormat";
import { reviewAspectCount } from "@/views/listing/specifics";
import { useOptimisticTurn } from "@/views/listing/useOptimisticTurn";
import { buyerWaiting, keptWhenEnded } from "@/lib/listingsView";

// Views / watchers on a live listing — eBay's traffic, where we have it.
function MetricsRow({ views, watchers, className }) {
  if (views == null && watchers == null) return null;
  return (
    <div className={cn(
      "flex items-center gap-3.5 text-[12px] font-medium text-ink-secondary", className)}>
      {views != null && (
        <span className="inline-flex items-center gap-1" title="Views (last 90 days)">
          <Eye size={13} aria-hidden /> {views}
        </span>
      )}
      {watchers != null && (
        <span className="inline-flex items-center gap-1" title="Watchers">
          <Heart size={13} aria-hidden /> {watchers}
        </span>
      )}
    </div>
  );
}

// Cross-posting chips: where else this listing lives (Etsy, Depop, ...).
// eBay stays implied by the origin/status badges.
function MarketplaceChips({ listing }) {
  const others = Object.entries(listing.marketplaces || {})
    .filter(([key, st]) => key !== "ebay" && st && (st.status || st.error));
  if (!others.length) return null;
  const label = (key) => key.charAt(0).toUpperCase() + key.slice(1);
  return (
    <>
      {others.map(([key, st]) => (
        <span
          key={key}
          title={st.error ? `${label(key)}: ${st.error}` : `${label(key)}: ${st.status}`}
          className={cn(
            "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-bold",
            st.error
              ? "bg-warning-soft border-warning/40 text-warning"
              : st.status === "published"
                ? "bg-success-soft border-success/30 text-success"
                : "bg-bg-sunken border-line text-ink-secondary",
          )}
        >
          {label(key)} {st.error ? "!" : st.status === "published" ? "✓" : st.status}
        </span>
      ))}
    </>
  );
}

// Live for 60+ days: old listings sink in eBay search, so relisting fresh is
// worth surfacing on the card itself.
function StaleChip({ className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-yellow-soft border border-warning/30",
        "px-2 py-0.5 text-[11px] font-bold text-warning", className)}
      title="Live for 60+ days — old listings sink in eBay search. Open it and relist fresh for a placement boost."
    >
      <Clock size={11} aria-hidden /> stale
    </span>
  );
}

// A buyer is waiting on an answer.
//
// A pending Best Offer is the only thing on a live card that EXPIRES: eBay
// gives an offer 48 hours, after which it lapses whether or not the seller
// ever saw it. Views, watchers and a stale date all keep until the next time
// the grid is opened; money on the table does not. So this is the one chip
// drawn filled rather than tinted — on a grid of twenty live listings it has
// to be the thing the eye lands on first.
//
// The app reads offers; it does not answer them. Accepting, countering and
// declining all happen in eBay's own flow, so the tooltip sends the seller
// there rather than implying a control here that doesn't exist.
function OfferChip({ count, top, currency, expiresAt, className }) {
  const money = formatMoney(top, currency || "USD");
  const soon = timeUntil(expiresAt);
  // With exactly one offer the AMOUNT is the fact worth reading and the count
  // says nothing ("1 offer" of what?). With more than one, the count is what
  // changes what the seller does next, and the money shown is the best of
  // them. Either way the chip stays short enough to sit on a photo.
  const label = count === 1
    ? (money ? `Offer ${money}` : "1 offer")
    : (money ? `${count} offers · ${money}` : `${count} offers`);
  const worth = count === 1
    ? (money ? `A buyer offered ${money}.` : "A buyer has made an offer.")
    : (money
      ? `${count} buyers have made offers, the highest ${money}.`
      : `${count} buyers have made offers.`);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-blue border border-blue",
        "px-2 py-0.5 text-[11px] font-bold text-on-accent tabular-nums", className)}
      title={`${worth} Accept, counter or decline it in eBay — `
        + (soon ? `the first one expires ${soon}.` : "offers expire after 48 hours.")}
    >
      <HandCoins size={11} aria-hidden /> {label}
    </span>
  );
}

// Somebody has bid. The auction's own version of the offer above: the
// listing has stopped being an item nobody wanted and become a sale in
// progress, and the card glows green and rises to the top of the grid for
// it. Unlike an offer a bid needs no answer from the seller, so the tooltip
// says what happens next rather than sending them anywhere. Drawn filled in
// the same green as the glow, so the chip is what NAMES the colour — the
// glow alone reads to nobody who can't see it.
function BidChip({ count, high, currency, className }) {
  const money = formatMoney(high, currency || "USD");
  const label = `${count} ${count === 1 ? "bid" : "bids"}` + (money ? ` · ${money}` : "");
  const worth = count === 1
    ? (money ? `A buyer has bid ${money} on this auction.` : "A buyer has bid on this auction.")
    : (money
      ? `${count} bids on this auction — the high bid is ${money}.`
      : `${count} bids on this auction.`);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-green border border-green",
        "px-2 py-0.5 text-[11px] font-bold text-on-accent tabular-nums", className)}
      title={`${worth} It sells to the highest bidder when the auction ends.`}
    >
      <Gavel size={11} aria-hidden /> {label}
    </span>
  );
}

// How long this auction has left, counting itself down.
//
// An auction is the one listing in the app with a DEADLINE, and until now the
// grid never mentioned it: a card with a bid on it looked the same three days
// out as it did four minutes out, which are not the same listing to a seller.
// The bid chip above says the auction is worth something; this says how long
// that is still true for.
//
// Quiet until it matters. For most of an auction's life the clock is a fact
// alongside the views and watchers it sits with — "82 views, 10 watchers, 2d
// 4h left" is one sentence about how the listing is doing. Inside the final
// hour it becomes a chip, turns amber, and starts counting seconds: the same
// threshold does all three, so the colour never needs explaining, and a grid
// of twenty auctions highlights the one that is actually happening rather
// than shouting about all of them.
//
// Past the deadline it says so plainly rather than vanishing. The auction is
// over, the app finds out on the next sweep of eBay, and in between "Ended"
// is the true thing to say — a clock that disappeared would leave the card
// looking like the auction had simply never had one.
function AuctionClock({ endsAt, className }) {
  const left = useAuctionCountdown(endsAt);
  // No readable deadline, so no clock: on this card an absent number always
  // means the app could not ask, never that the answer is nothing.
  if (!left) return null;
  const when = auctionEndLabel(endsAt);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-[12px] tabular-nums",
        left.ended
          ? "font-medium text-ink-faint"
          : left.endingSoon
            ? "rounded-full bg-yellow-soft px-2 py-0.5 font-bold text-warning"
            : "font-medium text-ink-secondary",
        className)}
      title={left.ended
        ? `This auction ended ${when}. The next sync with eBay will say whether it sold.`
        : `This auction ends ${when} — it sells to the highest bidder then.`}
    >
      <Timer size={13} aria-hidden />
      {left.ended ? "Auction ended" : `${left.text} left`}
    </span>
  );
}

// What the amber card means, in words — for the tooltip and the accessible
// name, so the state is never carried by colour alone.
const NEEDS_INFO_LABEL =
  "Needs info before it can go on eBay — open it to finish the listing.";

// The amber card's own chip. Colour reads at a glance across a grid; this is
// what says the same thing to a screen reader, to anyone who can't separate
// amber from cream, and to the seller who wants it named rather than implied.
function NeedsInfoChip({ className, title }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-card border border-warning/50",
        "px-2 py-0.5 text-[11px] font-bold text-warning", className)}
      title={title || NEEDS_INFO_LABEL}
    >
      <AlertTriangle size={11} aria-hidden /> needs info
    </span>
  );
}

function ReviewChip({ count, className }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full bg-yellow-soft border border-warning/30",
        "px-2 py-0.5 text-[11px] font-bold text-warning", className)}
      title="AI-inferred item specifics worth a glance before publishing"
    >
      <AlertTriangle size={11} aria-hidden /> {count} to review
    </span>
  );
}

// What it actually went for. An accepted offer settles BELOW the asking price
// and eBay never moves the listing's own price, so a sold card showing `price`
// was showing what was asked. The ask stays visible, struck through, with how
// far under it landed — plus the profit where a cost basis was recorded.
function SoldLines({ listing: l, soldFor, knownSale, discount, className }) {
  const paid = l.purchase_price;
  if (!discount && !(paid != null && soldFor > 0)) return null;
  return (
    <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1", className)}>
      {discount && (
        <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-secondary"
          title={`Sold for ${formatMoney(soldFor, l.currency)} — ${formatMoney(discount.amount, l.currency)} (${discount.percent}%) under the ${formatMoney(l.price, l.currency)} asking price.`}>
          <span className="line-through text-ink-faint tabular-nums">
            {formatMoney(l.price, l.currency)}
          </span>
          <span className="rounded-full bg-blue-soft px-1.5 py-0.5 text-[11px] font-bold text-blue tabular-nums">
            −{discount.percent}%
          </span>
        </p>
      )}
      {/* Profit framework: a sold item with a recorded cost basis shows what
          it made (sale price − what you paid, before fees). */}
      {paid != null && soldFor > 0 && (
        <p className="text-[12px] font-semibold"
          title={`Sold ${knownSale ? "" : "~"}${formatMoney(soldFor, l.currency)} − paid ${formatMoney(paid, l.currency)}, before fees & shipping`}>
          <span className={soldFor - Number(paid) >= 0 ? "text-success" : "text-warning"}>
            {soldFor - Number(paid) >= 0 ? "+" : "−"}
            {formatMoney(Math.abs(soldFor - Number(paid)), l.currency)} profit
          </span>
        </p>
      )}
    </div>
  );
}

// The one-line "what happens if you click this" for the card's status.
// Sold says "View sale", never "Relist": it opens as an archive of a finished
// sale, and selling another one is a fresh listing (the archive's own "Relist
// as new listing"). Promising a relist here is what made a sold item look
// publishable.
const CTA = {
  unlisted: "Finish & list", published: "Edit live", live: "Edit live",
  ended: "Relist", sold: "View sale",
};

function CtaHint({ status, className }) {
  const text = CTA[status];
  if (!text) return null;
  return (
    <span className={cn("inline-flex items-center gap-1 text-xs font-semibold text-blue", className)}>
      {text} <ArrowRight size={13} aria-hidden />
    </span>
  );
}

// The turn a rotate shows ahead of the server, as inline style on the box
// around the card's photo. A quarter turn swaps the photo's width and
// height, and in the 4:3 tile that means the box has to be re-cut before it
// is turned: object-cover crops the picture to the box it sits in, and the
// box that turns INTO a 4:3 frame is a 3:4 one — 75% wide, 133% tall,
// centred (the translate is in the box's own units: 1/6 of its width right,
// 1/8 of its height up). Cut that way, what is on screen after the turn is
// exactly the crop the re-encoded file gets: the same scale and the same
// centre, only from the file the server now holds, so the swap when it
// loads moves no pixel. The list row's thumbnail is square, so there the
// box already is its own turned self and only the rotation is needed. A
// half turn (two taps before the first has loaded) keeps the frame's shape
// either way.
function turnStyle(spin, square) {
  if (!spin) return undefined;
  const transform = `rotate(${spin}deg)`;
  if (square || (spin / 90) % 2 === 0) return { transform };
  return {
    width: "75%", height: "133.333%",
    transform: `translate(16.667%, -12.5%) ${transform}`,
  };
}

// ListingCard — one saved listing, as a grid tile (`layout="grid"`, the
// default) or as a compact row (`layout="list"`). Click opens it in the
// workflow; when onDelete is provided, a trash button removes it. The delete
// control is a sibling of the card button (not nested) so it stays valid,
// focusable HTML — in list layout it sits beside the row instead of over it.
// `metrics` (optional) shows eBay views/watchers for a live listing, and
// lights the card green when it carries a bid or a pending offer.
// `selectable` puts a checkbox on the card: ticking it reports through
// `onSelect` and shows through `selected`, which is what powers the bulk
// actions in Drafts (publish / merge / delete selected). It is a tick box,
// not a mode — the card goes on opening when the card is clicked.
// `onRotate(id, name)` puts a rotate button on the photo: one tap turns the
// listing's main photo 90° clockwise on the server, so a photo that is
// visibly sideways on the grid is fixed there rather than in the full editor.
// It resolves with the rotated file's version and rejects if the turn did
// not happen. Callers pass it for drafts: the corner it takes on the tile is
// the origin badge's once a listing has been on eBay, and a live listing's
// photos are already eBay's copy anyway.
// `needsInfo` paints the whole card amber: this listing will not reach eBay
// until something on it is filled in. See the cardClass comment below.
// memo'd, and it earns it: the app context holds the 60s notification poll, so
// every unread-count refresh re-rendered the whole tree -- one framer-motion
// card per listing, reconciled once a minute for a bell badge, and once every
// 1.5s while a bulk batch polls. The props are primitives plus callbacks the
// parents already keep stable.
export const ListingCard = memo(function ListingCard({
  item, onOpen, onDelete, onEnd, ending, onStartOver, startingOver, onSkip, skipped,
  onRotate, stale, metrics, needsInfo, needsInfoWhy, selectable, selected, onSelect,
  layout = "grid", className,
}) {
  const list = layout === "list";
  const l = item.listing || {};
  const draft = item.status === "draft" || item.status === "dry_run";
  // Drafts with AI-inferred specifics awaiting a glance get a ⚠ count chip —
  // review those fields and the draft is publish-ready.
  const reviewCount = draft ? reviewAspectCount(l.item_specifics) : 0;
  // How sure the AI was of what this IS when it drafted it (see
  // badges.ConfidenceChip). Drafts only: once a listing is live the seller
  // has stood behind it, and the AI's doubts about the first draft are not
  // a fact about the listing that is selling.
  const confidence = draft ? l.ai_confidence : "";
  const isLive = item.status === "published" || item.status === "live";
  const hasMetrics = isLive && metrics
    && (metrics.views != null || metrics.watchers != null);
  // Buyers waiting on an answer. Live listings only — an offer on anything
  // else is settled history. `> 0` is deliberately the whole test: the count
  // is ABSENT, not zero, when eBay couldn't be asked (see services/metrics),
  // so a missing number draws nothing rather than "no offers".
  const offers = isLive && metrics && metrics.offers > 0 ? metrics.offers : 0;
  // Bids on an auction, on the same terms: live only, and absent-is-unknown.
  const bids = isLive && metrics && metrics.bids > 0 ? metrics.bids : 0;
  // When that auction stops taking them, from the same read — so the same
  // rule again: live only, and a card with no answer shows no clock rather
  // than a deadline it made up. eBay reports this for auctions and nothing
  // else (backend/services/ebay_trading._auction_ends_at), which is what
  // keeps a Buy It Now from counting down to its own renewal date.
  const endsAt = (isLive && metrics && metrics.ends_at) || "";
  // Either one lights the card: a buyer has put money on this listing. The
  // same test decides its place in the grid (lib/listingsView.orderListings),
  // so the card that glows is always the card that was lifted.
  const lit = buyerWaiting(item, metrics);
  // Version thumbnails by updated_at so a rotate/clean-up busts the hour-long
  // /media cache the moment the listing is touched — without killing caching.
  const ver = Date.parse(item.updated_at || "") || undefined;
  // The photo this card shows, when the listing has one on the server.
  // Listings imported from eBay have no local files — their photos are
  // eBay-hosted absolute URLs, used as-is (and cannot be rotated from here).
  const photoName = (l.images && l.images[0]) || null;
  // A rotate from this card rewrites that file, and the card must never ask
  // for it again by a version it has already shown: a browser reuses an
  // image it has loaded in this page for an identical URL without asking, so
  // the old `?v=` would paint the old orientation over a file that is turned.
  // The server's bump of updated_at is the version every card gets
  // eventually, but it lands in the background, and the listings refetch a
  // rotate triggers can come back BEFORE it — with the updated_at the card
  // already had. So the card keeps the rotated file's own version, taken from
  // the server's answer, for as long as updated_at is the one it was rotated
  // under. The moment updated_at moves on (that bump, or any later edit),
  // the record is authoritative again and the override is let go.
  const [turned, setTurned] = useState(null);   // { version, at: updated_at }
  const photoVersion = turned && turned.at === (item.updated_at || "")
    ? turned.version : ver;
  const thumb = photoName
    ? mediaUrl(item.id, photoName, photoVersion)
    : (l.image_urls && l.image_urls[0]) || null;
  // One tap, one quarter turn, shown before the server has answered and
  // held until the rotated file is the one on screen — see useOptimisticTurn.
  const rotatePhoto = useCallback(async () => {
    const version = await onRotate(item.id, photoName);
    setTurned({
      // A version the server did not send is still a version this photo has
      // never been asked for: later than anything the card has shown.
      version: Number.isFinite(version)
        ? version : Math.max(Date.now(), (photoVersion || 0) + 1),
      at: item.updated_at || "",
    });
  }, [onRotate, item.id, item.updated_at, photoName, photoVersion]);
  const turn = useOptimisticTurn({
    version: photoVersion,
    onRotate: onRotate && photoName ? rotatePhoto : undefined,
  });
  const inventory = item.status === "unlisted";
  // A sold listing is shown at what the buyer PAID (l.sold_price) whenever
  // eBay reported it; without that we fall back to the asking price and mark
  // the badge approximate rather than quietly claiming it as the take.
  const sold = item.status === "sold";
  const soldFor = sold ? salePrice(l) : null;
  const knownSale = hasSalePrice(l);
  const discount = sold ? saleDiscount(l) : null;
  const fromEbay = (l.source || "") === "ebay";
  // eBay's own watch count rides along on imported listings, so those cards
  // aren't blank while the metrics endpoint only covers app-created ones.
  const watchers = hasMetrics ? metrics.watchers : (fromEbay ? l.watch_count : null);
  // An image that 404s (e.g. an older photo lost from ephemeral storage) shows
  // the placeholder instead of a blank tile. Reset when the src changes: thumb
  // is versioned by updated_at, so a rotate or a re-upload is a NEW url, and
  // without this the card stayed stuck on the placeholder for the rest of its
  // life -- most visible during the first store sync, when /media is still
  // being written and a miss is expected.
  const [imgFailed, setImgFailed] = useState(false);
  const [failedFor, setFailedFor] = useState(thumb);
  if (thumb !== failedFor) { setFailedFor(thumb); setImgFailed(false); }
  // Origin — created here vs. imported from eBay — only where the distinction
  // matters (the listing exists on eBay). Hover for exactly what each allows.
  const showOrigin = isLive || item.status === "ended" || item.status === "sold";

  // The turn goes on a box around the <img>, not on the <img>: the image
  // animates its own transform for the hover zoom, and a turn coming off
  // through that transition would show the rotated file spinning back.
  const photo = thumb && !imgFailed ? (
    <span className="block size-full" style={turnStyle(turn.spin, list)}>
      <img
        src={thumb}
        alt=""
        loading="lazy"
        className={cn("size-full object-cover transition-transform duration-200",
          !list && "group-hover:scale-[1.03]")}
        onLoad={turn.settle}
        onError={(e) => { turn.settle(e); setImgFailed(true); }}
      />
    </span>
  ) : (
    <div className="grid place-items-center size-full text-ink-faint">
      <ImageOff size={list ? 20 : 28} aria-hidden />
    </div>
  );

  // What this listing is asking, in whichever field its format keeps it.
  // A plain auction has no `price` at all -- an imported one arrives with it
  // null -- so every auction in the store used to show "no price yet" beside
  // a starting bid it was never asked for. See lib/listingFormat.askingPrice.
  const asking = askingPrice(l);
  // The chip is silent on Buy It Now (see badges.FormatBadge), so this is
  // "does this listing sell in a way the price alone doesn't explain".
  const showFormat = isAuctionFormat(l.listing_format);
  const price = (
    <PriceBadge
      value={sold ? soldFor : asking.amount}
      currency={l.currency}
      prefix={sold ? undefined : asking.prefix}
      approx={inventory || (sold && !knownSale)}
      title={sold
        ? (knownSale
          ? "What this actually sold for"
          : "eBay hasn't reported what this sold for — showing the asking price")
        // Only where the number needs explaining. A Buy It Now price is the
        // price; spelling that out on every card in the store is a tooltip
        // nobody needed and one more thing to read wrong.
        : showFormat
          ? formatSummary(l, (v) => formatMoney(v, l.currency || "USD"))
          : undefined}
    />
  );

  // Rotate: turn the photo the card is showing, right here. Only where there
  // is a photo of the listing's own to turn and it is actually on screen (a
  // turn applied to the placeholder is a turn applied blind). In the grid it
  // sits on the photo itself, in the corner a draft leaves free; in a list
  // row the thumbnail is too small to carry it, so it joins the row's
  // controls beside the skip and start-over buttons.
  const rotatable = !!onRotate && !!photoName && !!thumb && !imgFailed;
  const rotateButton = rotatable && (
    <button
      type="button"
      onClick={turn.rotate}
      disabled={turn.rotating}
      aria-label="Rotate photo 90°"
      title="Rotate the photo 90° clockwise"
      className={cn(
        "grid place-items-center size-8 rounded-full cursor-pointer",
        "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
        "hover:text-blue hover:border-blue/40 transition-colors",
        turn.rotating && "cursor-wait text-blue",
        !list && "absolute bottom-3 right-3 pointer-events-auto",
      )}
    >
      {turn.rotating
        ? <Loader2 size={15} className="animate-spin" aria-hidden />
        : <RotateCw size={15} aria-hidden />}
    </button>
  );

  const actions = (onDelete || onEnd || onStartOver || onSkip || (list && rotatable)) && (
    <>
      {list && rotateButton}
      {/* Skip: set this draft aside. It stays in Drafts, but the queue
          after a publish stops offering it as the next one to work on. */}
      {onSkip && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onSkip(item); }}
          aria-pressed={!!skipped}
          aria-label={skipped ? "Un-skip this draft" : "Skip this draft"}
          title={skipped
            ? "Skipped — won't be offered as the next draft. Click to undo."
            : "Skip — set aside so it isn't offered as the next draft"}
          className={cn(
            "grid place-items-center size-8 rounded-full cursor-pointer",
            "backdrop-blur border shadow-card transition-colors",
            skipped
              ? "bg-blue border-blue text-on-accent"
              : "bg-card/85 border-line text-ink-faint hover:text-blue hover:border-blue/40",
          )}
        >
          {skipped ? <Undo2 size={15} aria-hidden /> : <SkipForward size={15} aria-hidden />}
        </button>
      )}
      {/* Start over: re-run the AI on this listing's own photos, replacing
          the drafted content. Drafts only — it would overwrite a live
          listing's copy with a fresh guess. */}
      {onStartOver && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onStartOver(item); }}
          disabled={startingOver}
          aria-label="Start over — re-run the AI on these photos"
          title="Start over — re-run the AI on these photos"
          className={cn(
            "grid place-items-center size-8 rounded-full cursor-pointer",
            "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
            "hover:text-blue hover:border-blue/40 transition-colors",
            startingOver && "cursor-wait text-blue",
          )}
        >
          {startingOver
            ? <Loader2 size={15} className="animate-spin" aria-hidden />
            : <RotateCcw size={15} aria-hidden />}
        </button>
      )}
      {/* A LIVE listing's card action is End, never Delete: the listing is a
          real thing on eBay, and the button has to take it off eBay before
          anything happens to the card. What happens next depends on whose
          work the record holds — kept under Inactive for a month, or removed
          on the spot when it is only the sync's copy of an eBay listing — so
          the tooltip and the dialog behind it say which. Delete stays for
          drafts and finds, which were never on eBay at all. */}
      {onEnd ? (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onEnd(item); }}
          disabled={ending}
          aria-label="End listing on eBay"
          title={keptWhenEnded(item)
            ? "End this listing on eBay — it moves to Inactive, where you can relist it"
            : "End this listing on eBay — its card is removed from here too"}
          className={cn(
            "grid place-items-center size-8 rounded-full cursor-pointer",
            "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
            "hover:text-error hover:border-error/40 transition-colors",
            ending && "cursor-wait text-error",
          )}
        >
          {ending
            ? <Loader2 size={15} className="animate-spin" aria-hidden />
            : <Ban size={15} aria-hidden />}
        </button>
      ) : onDelete && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onDelete(item); }}
          aria-label="Delete listing"
          title="Delete listing"
          className={cn(
            "grid place-items-center size-8 rounded-full cursor-pointer",
            "bg-card/85 backdrop-blur border border-line shadow-card text-ink-faint",
            "hover:text-error hover:border-error/40 transition-colors",
          )}
        >
          <Trash2 size={15} aria-hidden />
        </button>
      )}
    </>
  );

  const cardClass = cn(
    // h-full fills whatever height the caller gave the card, so a grid that
    // passes `className="h-full"` gets a row of cards that line up even when
    // one carries an extra line (a sold card's discount and profit rows).
    // Callers that put their own controls UNDER the card (the drafts strip)
    // leave it alone and keep a card sized to its own content.
    "w-full h-full text-left bg-card rounded-card border shadow-card overflow-hidden",
    "flex cursor-pointer",
    list ? "flex-row items-center gap-3 p-2.5 sm:p-3" : "flex-col",
    selected ? "border-blue ring-2 ring-blue/60" : "border-line",
    // A listing eBay won't take, or has already refused, over something the
    // seller has to fill in. The warning line under the card says WHICH
    // field, but that line is one small row among many on a grid of twenty
    // cards -- easy to publish straight past, and easy to miss entirely when
    // the refusal came from a toast that has since gone. The card itself
    // carries the state instead: amber card, amber edge, readable across the
    // whole grid at a glance. Warning, not error -- nothing is broken and
    // nothing is lost; the listing needs updating before it can be posted.
    // (cn is tailwind-merge, so this wins over bg-card/border-line above.)
    needsInfo && "bg-warning-soft border-warning/45",
    // A buyer has acted on this listing — a bid on the auction, or an offer
    // waiting for an answer. The card is lit from behind in green: a ring
    // and a soft halo (tokens.css --glow-buyer), which is what makes it the
    // card the eye lands on in a grid of twenty, on top of the grid lifting
    // it to the front. The chip on the photo says which and how much; the
    // colour is never the whole message. Below the amber above on purpose:
    // a live listing cannot need info, so the two never meet, but if they
    // ever did the money on the table is the thing to see.
    lit && "card-buyer-glow border-green/60",
    // A skipped draft stays fully usable — just visibly set aside.
    skipped && "opacity-60",
  );

  const motionProps = {
    type: "button",
    onClick: () => onOpen?.(item.id),
    // Colour alone is never the whole message: it can't be read by a screen
    // reader and it isn't there for anyone who can't tell amber from cream.
    // The same fact reaches the accessible name and the hover tooltip.
    title: needsInfo ? (needsInfoWhy || NEEDS_INFO_LABEL) : undefined,
    // The glow lives in box-shadow, and so does the hover lift, so the lit
    // card has to carry both: the hover swaps the halo for a wider one
    // rather than replacing it with a plain shadow, and `animate` names the
    // resting value so the card returns to its glow — or loses it the moment
    // the bid or the offer is gone from the next read — instead of holding
    // whatever the last hover left behind.
    animate: { boxShadow: lit ? "var(--glow-buyer)" : "var(--shadow-card)" },
    whileHover: {
      y: -2,
      boxShadow: lit ? "var(--glow-buyer-hover)" : "var(--shadow-card-hover)",
    },
    whileTap: { scale: 0.985 },
    transition: { duration: 0.18, ease: "easeOut" },
    className: cardClass,
  };

  // List layout: one scannable row per listing. Everything the tile stacks —
  // status, origin, warnings, traffic — moves onto a single wrapping badge
  // line, and the price/CTA pair sits at the end of the row where the eye
  // lands after the title.
  const body = list ? (
    <motion.button {...motionProps}>
      <span className="relative size-16 sm:size-20 shrink-0 overflow-hidden rounded-tile bg-bg-sunken">
        {photo}
      </span>
      <span className="min-w-0 flex-1 flex flex-col gap-1.5">
        <span className="font-semibold text-sm text-ink line-clamp-2">
          {l.title || item.title || "(untitled)"}
        </span>
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <StatusBadge status={item.status} />
          {/* Beside the status, because it changes how the price beside it
              is read: "Bid $0.99" on an auction is an opening bid, not a
              99-cent item. */}
          {showFormat && <FormatBadge listing={l} />}
          {/* Ahead of origin, staleness and traffic: those describe the
              listing, this one is a person waiting on the seller. */}
          {offers > 0 && (
            <OfferChip count={offers} top={metrics.top_offer}
              currency={metrics.offer_currency || l.currency}
              expiresAt={metrics.offer_expires_at} />
          )}
          {bids > 0 && (
            <BidChip count={bids} high={metrics.high_bid}
              currency={metrics.bid_currency || l.currency} />
          )}
          {showOrigin && <OriginBadge item={item} />}
          {stale && <StaleChip />}
          {/* One or the other, never both: "3 to review" is advice, and it
              must not sit next to (or in place of) the reason eBay is
              refusing the listing outright. */}
          {needsInfo
            ? <NeedsInfoChip title={needsInfoWhy} />
            : reviewCount > 0 && <ReviewChip count={reviewCount} />}
          {/* After the blockers and the review count: those say what to
              fix, this says how far to trust the rest. */}
          {confidence && <ConfidenceChip level={confidence} />}
          <MarketplaceChips listing={l} />
          {(hasMetrics || watchers != null) && (
            <MetricsRow views={hasMetrics ? metrics.views : null} watchers={watchers} />
          )}
          {/* Last on the line, beside the traffic it belongs with — and the
              one thing here that turns amber on its own when the auction is
              nearly over, which is what finds it in a row of chips. */}
          {endsAt ? <AuctionClock endsAt={endsAt} /> : null}
        </span>
        {sold && (
          <SoldLines listing={l} soldFor={soldFor} knownSale={knownSale} discount={discount} />
        )}
        {/* Phone: the price/CTA column would squeeze the badge line into one
            chip per row, so it folds into the content instead. */}
        <span className="flex sm:hidden items-center gap-2.5">
          {price}
          <CtaHint status={item.status} className="whitespace-nowrap" />
        </span>
      </span>
      <span className="hidden sm:flex shrink-0 flex-col items-end gap-1.5 pl-1">
        {price}
        <CtaHint status={item.status} className="whitespace-nowrap" />
      </span>
    </motion.button>
  ) : (
    <motion.button {...motionProps}>
      <div className="aspect-[4/3] bg-bg-sunken relative overflow-hidden">
        {photo}
        {/* The status and, when a buyer is waiting, the offer — one wrapping
            row so the offer chip never lands under the delete/end buttons in
            the opposite corner (which is what the pr-* allowance is for).
            The bottom-left corner keeps stale/needs-info: a live listing can
            be all three at once. */}
        <div className={cn(
          "absolute top-3 left-3 right-3 flex flex-wrap items-start gap-1.5 pr-16",
          // The tick sits in this corner (see the checkbox below), so the
          // badges start after it rather than under it.
          selectable && "pl-8",
        )}>
          <StatusBadge status={item.status} className="shadow-card" />
          {showFormat && <FormatBadge listing={l} className="shadow-card bg-card/95" />}
          {offers > 0 && (
            <OfferChip count={offers} top={metrics.top_offer}
              currency={metrics.offer_currency || l.currency}
              expiresAt={metrics.offer_expires_at} className="shadow-card" />
          )}
          {bids > 0 && (
            <BidChip count={bids} high={metrics.high_bid}
              currency={metrics.bid_currency || l.currency} className="shadow-card" />
          )}
        </div>
        {stale && <StaleChip className="absolute bottom-3 left-3 shadow-card" />}
        {needsInfo ? (
          <NeedsInfoChip title={needsInfoWhy}
            className="absolute bottom-3 left-3 shadow-card" />
        ) : reviewCount > 0 && (
          <ReviewChip count={reviewCount} className="absolute bottom-3 left-3 shadow-card" />
        )}
        {showOrigin && (
          <OriginBadge item={item}
            className="absolute bottom-3 right-3 [&>span]:shadow-card [&>span]:bg-card/95 [&>span]:border [&>span]:border-line" />
        )}
      </div>
      <div className="p-4 flex flex-col gap-2 flex-1">
        <p className="font-semibold text-sm text-ink line-clamp-2">
          {l.title || item.title || "(untitled)"}
        </p>
        {/* Traffic and the auction clock on one line: "82 views, 10 watchers,
            2d 4h left" is one sentence about how this listing is doing, and
            the deadline is the half of it that decides what to do next. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 empty:hidden">
          {(hasMetrics || watchers != null) && (
            <MetricsRow views={hasMetrics ? metrics.views : null} watchers={watchers} />
          )}
          {endsAt ? <AuctionClock endsAt={endsAt} /> : null}
        </div>
        <div className="flex flex-wrap items-center gap-1.5 empty:hidden">
          {/* In the body, not on the photo: the photo's corners already
              hold the status and the needs-info / review chip, and on a
              phone-width tile a third would sit on top of one of them. */}
          {confidence && <ConfidenceChip level={confidence} />}
          <MarketplaceChips listing={l} />
        </div>
        {sold && (
          <SoldLines listing={l} soldFor={soldFor} knownSale={knownSale} discount={discount}
            className="flex-col items-start" />
        )}
        <div className="mt-auto flex items-center justify-between gap-2">
          {price}
          <CtaHint status={item.status} />
        </div>
      </div>
    </motion.button>
  );

  return (
    <div className={cn("relative group", list && "flex items-center gap-2", className)}>
      {body}
      {/* The rotate button, on the photo's own bottom-right corner. A sibling
          of the card button like every other control here (a button inside a
          button is invalid HTML and drops out of the tab order), laid over
          the card in a box the exact shape of the photo — the card's width,
          inside its border, at the photo's 4:3 — so "bottom-right of the
          photo" is a place the wrapper can name without knowing how tall the
          photo is. The box itself lets clicks through to the card. */}
      {!list && rotateButton && (
        <div className="absolute inset-x-px top-px aspect-[4/3] z-10 pointer-events-none">
          {rotateButton}
        </div>
      )}
      {/* The tick that puts this listing into a bulk action. Standing, not a
          mode: it is on the card from the moment the grid offers bulk
          actions, so picking three drafts to merge costs three clicks rather
          than a button press, three clicks and a way back out. A sibling of
          the card button (never nested — a button inside a button is invalid
          HTML and drops out of the tab order), so ticking and opening are
          separate actions and neither is ever an accident: the checkbox
          ticks, the card opens. Opposite corner from the card's own buttons,
          which it shares the tile with. */}
      {selectable && (
        <label
          title="Select this listing for a bulk action"
          className={cn(
            "z-10 flex items-center cursor-pointer",
            list
              ? "shrink-0 order-first pl-0.5"
              : "absolute top-2.5 left-2.5 rounded-md p-1 bg-card/90 backdrop-blur border border-line shadow-card",
          )}
        >
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onSelect?.()}
            aria-label={`Select "${l.title || item.title || "this listing"}"`}
            className="size-4 accent-(--brand-blue) cursor-pointer"
          />
        </label>
      )}
      {actions && (
        <div className={cn(
          "z-10 flex items-center gap-1.5",
          list ? "shrink-0" : "absolute top-3 right-3",
        )}>
          {actions}
        </div>
      )}
    </div>
  );
});
