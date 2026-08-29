import { motion } from "framer-motion";
import {
  ArrowLeft, ExternalLink, PackageCheck, RefreshCw, Save, Tag, X,
} from "lucide-react";
import { useApp } from "@/store";
import { cn, formatMoney, mediaUrl } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Field, InfoTip, Input } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { LoadingOverlay } from "@/components/ui/AIStatus";
import { hasSalePrice, salePrice, saleDiscount, soldUnits } from "@/lib/sales";

/* SoldArchive — what the editor becomes once a listing has sold.
 *
 * A sold listing is a RECORD, not a draft. It is the app's only memory of
 * what one finished sale was, and eBay's item is over: there is nothing left
 * to edit onto it and nothing left to publish. Opening it in the full
 * workflow was the bug — a finished sale sat there reading "Ready to
 * publish", one tap from re-listing the thing that already sold.
 *
 * So this view is read-only about the ITEM, and keeps exactly two actions:
 *
 *  - the sale's own numbers (what it went for, what it cost) stay editable,
 *    because they are what the profit total is made of and eBay does not
 *    always report a sale amount;
 *  - "Relist as new listing" copies this listing into a fresh draft — the
 *    honest way to sell another one, leaving this record untouched.
 */

const dateLong = (iso) => {
  const t = Date.parse(iso || "");
  if (Number.isNaN(t)) return null;
  try {
    return new Date(t).toLocaleDateString(undefined, {
      year: "numeric", month: "long", day: "numeric",
    });
  } catch (e) {
    return new Date(t).toDateString();
  }
};

// One "label / value" cell in the sale summary.
function Fact({ label, value, tone, hint }) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <span className="text-[12px] font-semibold text-ink-faint inline-flex items-center gap-1">
        {label}{hint && <InfoTip text={hint} />}
      </span>
      <span className={cn(
        "font-display text-lg font-bold tabular-nums truncate",
        tone === "success" ? "text-success"
          : tone === "warning" ? "text-warning" : "text-ink",
      )}>
        {value}
      </span>
    </div>
  );
}

// The photos as they were — no studio, no delete, no reorder. They may well
// be gone: selling purges a session's images to reclaim storage, and only
// eBay-hosted ones (imported listings) survive.
function ArchivePhotos({ sessionId, listing }) {
  const local = (listing.images || []).map((n) => mediaUrl(sessionId, n));
  const shots = local.length ? local : (listing.image_urls || []);
  if (!shots.length) {
    return (
      <p className="text-[13px] text-ink-faint">
        The photos were released when this sold — eBay still has them on the
        original listing.
      </p>
    );
  }
  return (
    <div className="flex gap-2.5 overflow-x-auto pb-1">
      {shots.map((src, i) => (
        <img
          key={src}
          src={src}
          alt={`${listing.title || "Sold item"} — photo ${i + 1}`}
          loading="lazy"
          className="size-24 sm:size-28 rounded-tile object-cover border border-line shrink-0 bg-bg-sunken"
        />
      ))}
    </div>
  );
}

export function SoldArchive({ w }) {
  const { setSession, setView, openListings } = useApp();
  // The form is seeded from the stored record, so it already carries the
  // sale's own fields (sold_at, sold_quantity, view_url) alongside the two
  // money inputs below.
  const listing = w.form;
  const currency = w.form.currency || "USD";
  // What the buyer PAID, which is not the asking price: an accepted offer,
  // an auction close or a markdown all settle below it. Where eBay never
  // reported one, the ask stands in and every number here says "approx".
  const record = {
    ...listing,
    sold_price: w.form.sold_price === "" ? null : Number(w.form.sold_price),
    price: w.form.price === "" ? null : Number(w.form.price),
    purchase_price: w.form.purchase_price === "" ? null : Number(w.form.purchase_price),
  };
  const each = salePrice(record);
  const units = soldUnits(record);
  const known = hasSalePrice(record);
  const discount = saleDiscount(record);
  const proceeds = each == null ? null : each * units;
  const cost = record.purchase_price;
  const profit = (proceeds != null && cost != null) ? proceeds - cost * units : null;
  const soldOn = dateLong(listing.sold_at);
  const itemUrl = listing.view_url
    || (w.ebayListingId ? `https://www.ebay.com/itm/${w.ebayListingId}` : "");

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="flex flex-col gap-4"
    >
      {w.aiBusy && <LoadingOverlay messages={w.aiBusy} />}

      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl sm:text-2xl font-bold text-ink truncate">
            {listing.title || "Sold listing"}
          </h1>
          <div className="flex items-center gap-2 mt-1">
            <span title="This sale is finished — the record is kept as it was">
              <TagPill tone="green">
                <PackageCheck size={12} aria-hidden /> Sold on eBay
              </TagPill>
            </span>
            {soldOn && (
              <span className="text-[13px] text-ink-secondary">{soldOn}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={() => openListings("inactive")}>
            <ArrowLeft aria-hidden /> Inactive
          </Button>
          <Button variant="ghost" onClick={() => { setSession(null); setView("dashboard"); }}
            aria-label="Close this listing">
            <X aria-hidden /> Exit
          </Button>
        </div>
      </div>

      {/* What the sale was. */}
      <Card className="flex flex-col gap-5">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Fact
            label={units > 1 ? `Sold for (×${units})` : "Sold for"}
            value={(known ? "" : "≈ ") + (formatMoney(proceeds, currency) || "—")}
            tone="success"
            hint={known
              ? "What the buyer actually paid, as eBay reported it."
              : "eBay hasn't reported what this went for — showing the asking price. Correct it below and the profit total follows."}
          />
          <Fact label="Asking price" value={formatMoney(record.price, currency) || "—"}
            hint={discount
              ? `It went ${formatMoney(discount.amount, currency)} (${discount.percent}%) under the ask.`
              : "The price the listing carried."} />
          <Fact label="You paid" value={formatMoney(cost, currency) || "—"}
            hint="What the item cost you. Without it there's no profit to work out." />
          <Fact
            label="Profit"
            value={profit == null
              ? "—"
              : `${profit >= 0 ? "+" : "−"}${formatMoney(Math.abs(profit), currency)}`}
            tone={profit == null ? undefined : (profit >= 0 ? "success" : "warning")}
            hint="Sale minus what you paid, before eBay fees and shipping."
          />
        </div>

        {itemUrl && (
          <a
            href={itemUrl} target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-[13px] font-semibold text-blue hover:underline w-fit"
          >
            View the sold listing on eBay <ExternalLink size={13} aria-hidden />
          </a>
        )}
      </Card>

      {/* The two numbers a finished sale can still have wrong — and the only
          fields this view lets through. Everything else describes an item
          that is gone. */}
      <Card className="flex flex-col gap-4">
        <div>
          <h2 className="font-bold text-ink inline-flex items-center gap-2">
            <Tag size={16} className="text-blue" aria-hidden /> Sale figures
          </h2>
          <p className="text-[13px] text-ink-secondary mt-0.5">
            Keep the profit totals honest. eBay reports a sale amount for
            recent transactions only — for anything older, this is the one
            place the real number can live.
          </p>
        </div>
        <div className="grid sm:grid-cols-2 gap-4">
          <Field
            label={`Sold for (${currency})`}
            help="Per item, before shipping — what the buyer actually paid."
          >
            <Input
              type="number" step="0.01" min="0" inputMode="decimal"
              placeholder={record.price != null ? String(record.price) : "0.00"}
              value={w.form.sold_price}
              onChange={(e) => w.set("sold_price", e.target.value)}
            />
          </Field>
          <Field
            label={`You paid (${currency})`}
            help="Per item — your cost basis for this sale."
          >
            <Input
              type="number" step="0.01" min="0" inputMode="decimal"
              placeholder="optional"
              value={w.form.purchase_price}
              onChange={(e) => w.set("purchase_price", e.target.value)}
            />
          </Field>
        </div>
        <div>
          <Button variant="secondary" onClick={w.saveSaleFigures}>
            <Save aria-hidden /> Save sale figures
          </Button>
        </div>
      </Card>

      {/* The item, as it was listed. Read-only on purpose. */}
      <Card className="flex flex-col gap-4">
        <h2 className="font-bold text-ink">How it was listed</h2>
        <ArchivePhotos sessionId={w.sessionId} listing={listing} />
        {listing.brand && (
          <p className="text-sm text-ink-secondary">
            <strong className="text-ink">Brand:</strong> {listing.brand}
          </p>
        )}
        {listing.description && (
          <p className="text-sm text-ink-secondary whitespace-pre-wrap line-clamp-6">
            {listing.description}
          </p>
        )}
        {(listing.item_specifics || []).some((s) => (s.value || "").trim()) && (
          <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-1.5 text-[13px]">
            {listing.item_specifics.filter((s) => (s.value || "").trim()).map((s) => (
              <div key={s.name} className="flex gap-2 min-w-0">
                <dt className="text-ink-faint shrink-0">{s.name}</dt>
                <dd className="text-ink font-medium truncate">{s.value}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>

      {/* Got another one? That's a NEW listing — the sold record stays put. */}
      <div className="sticky bottom-20 md:bottom-4 z-30 pt-1">
        <div className="rounded-card border-2 border-success/45 backdrop-blur shadow-float p-3.5 sm:p-4 bg-card/95 flex flex-col gap-3 sm:flex-row sm:items-center">
          <span className="flex items-center gap-3 min-w-0 flex-1">
            <span className="grid place-items-center size-10 rounded-full shrink-0 bg-success-soft text-success">
              <PackageCheck size={20} aria-hidden />
            </span>
            <span className="min-w-0">
              <span className="block text-[15px] sm:text-base font-bold text-ink leading-tight">
                This one's sold
              </span>
              <span className="block text-[13px] text-ink-secondary leading-snug mt-0.5">
                It's archived under Inactive. Got another? Start a fresh
                listing from this one.
              </span>
            </span>
          </span>
          <Button variant="primary" size="lg" className="shrink-0" onClick={w.relist}>
            <RefreshCw aria-hidden /> Relist as new listing
          </Button>
        </div>
      </div>
    </motion.div>
  );
}
