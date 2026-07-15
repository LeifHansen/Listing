import { useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Rocket, Save, Truck, RotateCcw, CreditCard, Tag } from "lucide-react";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { mediaUrl, formatMoney } from "@/lib/utils";

function prettyCondition(c) {
  if (!c) return "";
  return c.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (m) => m.toUpperCase());
}

function policyName(data, key, field) {
  if (!data) return null;
  return ((data.policies?.[key] || []).find((p) => p.id === data.selected?.[field]) || {}).name || null;
}

/* PreviewPage — a full-page look at the listing roughly as buyers will see it
   on eBay: gallery, title, price box, specifics, description, and the
   shipping/payment/returns policies that will apply. The last stop before
   going live: Publish, Save as Draft, or Back to Editing. */
export function PreviewPage({ w, onPublish, onSaveDraft, onBack }) {
  const { policiesData, ebay } = useApp();
  const f = w.form;
  const images = f.images || [];
  const [active, setActive] = useState(0);
  const specifics = (f.item_specifics || []).filter((s) => s.name && s.value);
  const ship = policyName(policiesData, "fulfillment", "fulfillment_policy_id");
  const pay = policyName(policiesData, "payment", "payment_policy_id");
  const ret = policyName(policiesData, "return", "return_policy_id");
  const mainImg = images[active] || images[0];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.22, ease: "easeOut" }}
      className="flex flex-col gap-5"
    >
      {/* Header: back + actions */}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="ghost" onClick={onBack}>
          <ArrowLeft aria-hidden /> Back to Editing
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-ink">Preview</h1>
          <p className="text-sm text-ink-secondary">
            Roughly how buyers will see it on eBay — give it one last look.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="lg" onClick={onSaveDraft}>
            <Save aria-hidden /> Save as Draft
          </Button>
          <Button variant="primary" size="lg" onClick={onPublish}>
            <Rocket aria-hidden /> Publish
          </Button>
        </div>
      </div>

      {/* eBay-style listing layout */}
      <div className="bg-card border border-line rounded-card shadow-card p-5 sm:p-7">
        <div className="grid lg:grid-cols-2 gap-7">
          {/* Gallery */}
          <div className="flex flex-col gap-3 min-w-0">
            <div className="rounded-tile border border-line bg-bg-sunken aspect-square grid place-items-center overflow-hidden">
              {mainImg ? (
                <img
                  src={`${mediaUrl(w.sessionId, mainImg)}?v=${w.imageVersion}`}
                  alt=""
                  className="size-full object-contain"
                />
              ) : (
                <span className="text-sm text-ink-faint">No photos yet</span>
              )}
            </div>
            {images.length > 1 && (
              <div className="flex gap-2 overflow-x-auto pb-1">
                {images.map((name, i) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => setActive(i)}
                    aria-label={`Photo ${i + 1}`}
                    className={`h-16 w-16 shrink-0 rounded-[10px] border-2 overflow-hidden bg-bg-sunken cursor-pointer ${
                      i === active ? "border-blue" : "border-line"}`}
                  >
                    <img
                      src={`${mediaUrl(w.sessionId, name)}?v=${w.imageVersion}`}
                      alt=""
                      className="size-full object-cover"
                    />
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Buy box */}
          <div className="flex flex-col gap-4 min-w-0">
            <h2 className="text-lg sm:text-xl font-bold text-ink leading-snug">
              {f.title || "(untitled listing)"}
            </h2>
            {f.subtitle && <p className="text-sm text-ink-secondary -mt-2">{f.subtitle}</p>}
            <div className="flex items-center gap-3 text-sm text-ink-secondary">
              {f.condition && (
                <span className="inline-flex items-center gap-1.5">
                  <Tag size={14} aria-hidden /> {prettyCondition(f.condition)}
                </span>
              )}
              <span>Qty {f.quantity || 1}</span>
            </div>

            <div className="rounded-tile border border-line bg-bg-sunken p-4">
              <p className="text-[28px] font-bold text-ink leading-none">
                {formatMoney(f.price) || "$—"}
              </p>
              {f.best_offer_enabled && (
                <p className="text-sm text-ink-secondary mt-1.5">or Best Offer</p>
              )}
              {f.promote_enabled && (
                <p className="text-xs text-ink-faint mt-1.5">
                  Will be promoted{f.promote_recommended
                    ? " at eBay's recommended rate"
                    : f.promote_percent ? ` at ${f.promote_percent}% ad rate` : ""}
                </p>
              )}
            </div>

            <div className="flex flex-col gap-2 text-sm">
              <p className="flex items-center gap-2 text-ink-secondary">
                <Truck size={15} className="shrink-0" aria-hidden />
                <span><strong className="text-ink">Shipping:</strong> {ship || (ebay.connected ? "account default" : "set after connecting eBay")}</span>
              </p>
              <p className="flex items-center gap-2 text-ink-secondary">
                <CreditCard size={15} className="shrink-0" aria-hidden />
                <span><strong className="text-ink">Payment:</strong> {pay || "eBay Managed Payments"}</span>
              </p>
              <p className="flex items-center gap-2 text-ink-secondary">
                <RotateCcw size={15} className="shrink-0" aria-hidden />
                <span><strong className="text-ink">Returns:</strong> {ret || "per your eBay policy"}</span>
              </p>
            </div>
          </div>
        </div>

        {/* Item specifics */}
        {specifics.length > 0 && (
          <div className="mt-7 border-t border-line pt-5">
            <h3 className="font-bold text-ink mb-3">Item specifics</h3>
            <div className="grid sm:grid-cols-2 gap-x-8 gap-y-2">
              {specifics.map((s) => (
                <div key={s.name} className="text-sm flex gap-2 min-w-0">
                  <span className="text-ink-secondary w-2/5 shrink-0">{s.name}</span>
                  <span className="text-ink font-medium min-w-0 break-words">{s.value}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Description */}
        <div className="mt-7 border-t border-line pt-5">
          <h3 className="font-bold text-ink mb-3">Item description from the seller</h3>
          <p className="text-[15px] text-ink-secondary whitespace-pre-line leading-relaxed">
            {f.description || "No description yet."}
          </p>
          {f.condition_description && (
            <p className="text-sm text-ink-secondary mt-3">
              <strong className="text-ink">Condition notes:</strong> {f.condition_description}
            </p>
          )}
        </div>
      </div>

      {/* Bottom actions (mirror of the top, for after scrolling the preview) */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button variant="ghost" size="lg" onClick={onBack}>
          <ArrowLeft aria-hidden /> Back to Editing
        </Button>
        <Button variant="secondary" size="lg" onClick={onSaveDraft}>
          <Save aria-hidden /> Save as Draft
        </Button>
        <Button variant="primary" size="lg" onClick={onPublish}>
          <Rocket aria-hidden /> Publish
        </Button>
      </div>
    </motion.div>
  );
}
