import { Rocket } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
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

/* A last look before the listing goes live on eBay — exactly what buyers will
   see (photos, title, price, condition, key specifics, description) plus which
   business policies apply. Confirm publishes; cancel keeps editing. */
export function PublishPreview({ w, policiesData, open, onClose, onConfirm }) {
  const f = w.form;
  const images = f.images || [];
  const specifics = (f.item_specifics || []).filter((s) => s.name && s.value);
  const ship = policyName(policiesData, "fulfillment", "fulfillment_policy_id");
  const pay = policyName(policiesData, "payment", "payment_policy_id");
  const ret = policyName(policiesData, "return", "return_policy_id");

  return (
    <Dialog open={open} onClose={onClose} title="Preview before publishing" wide>
      <div className="flex flex-col gap-5">
        {images.length > 0 && (
          <div className="flex gap-2 overflow-x-auto pb-1">
            {images.map((name, i) => (
              <img
                key={name}
                src={`${mediaUrl(w.sessionId, name)}?v=${w.imageVersion}`}
                alt=""
                className={`h-28 w-28 object-contain rounded-tile border shrink-0 bg-bg-sunken ${
                  i === 0 ? "border-blue" : "border-line"}`}
              />
            ))}
          </div>
        )}

        <div>
          <h3 className="text-lg font-bold text-ink leading-snug">{f.title || "(untitled)"}</h3>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="text-xl font-bold text-blue">
              {formatMoney(f.price) || "—"}
            </span>
            {f.condition && (
              <span className="text-ink-secondary">{prettyCondition(f.condition)}</span>
            )}
            <span className="text-ink-faint">Qty {f.quantity || 1}</span>
            {f.best_offer_enabled && (
              <span className="text-ink-secondary">· Offers allowed</span>
            )}
            {f.promote_enabled && (
              <span className="text-ink-secondary">· Promoted</span>
            )}
          </div>
        </div>

        {specifics.length > 0 && (
          <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
            {specifics.slice(0, 8).map((s) => (
              <div key={s.name} className="text-sm flex gap-2 min-w-0">
                <span className="text-ink-secondary shrink-0">{s.name}:</span>
                <span className="text-ink font-medium truncate">{s.value}</span>
              </div>
            ))}
          </div>
        )}

        {f.description && (
          <div>
            <p className="text-xs font-semibold text-ink-faint uppercase tracking-wide mb-1">Description</p>
            <p className="text-sm text-ink-secondary whitespace-pre-line line-clamp-6">{f.description}</p>
          </div>
        )}

        {(ship || pay || ret) && (
          <p className="text-[13px] text-ink-secondary border-t border-line pt-3">
            {ship && <><strong className="text-ink">Shipping:</strong> {ship} · </>}
            {pay && <><strong className="text-ink">Payment:</strong> {pay} · </>}
            {ret && <><strong className="text-ink">Returns:</strong> {ret}</>}
          </p>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" size="lg" onClick={onClose}>Keep editing</Button>
          <Button variant="primary" size="lg" onClick={onConfirm}>
            <Rocket aria-hidden /> Publish to eBay
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
