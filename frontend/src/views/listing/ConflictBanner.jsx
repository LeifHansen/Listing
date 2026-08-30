import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import { postJson } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toaster";

/**
 * Fields the seller and eBay have both changed, and the choice between them.
 *
 * The sync records these and sends NEITHER value to eBay — picking one
 * silently is how a fix made in Seller Hub gets overwritten by a stale copy.
 * That part is right. What was missing is this: nothing told the seller. They
 * edited a title here, pressed Update, were told "Your eBay listing has been
 * updated", and their title never left the building. The next time they
 * looked at eBay it was gone, with no reason given.
 *
 * An unanswered conflict is an edit that will never reach eBay, so the
 * question stays on screen until it is answered.
 */
export function ConflictBanner({ conflicts, sessionId, onResolved }) {
  const { toast } = useToast();
  const [busy, setBusy] = useState("");
  const items = conflicts || [];
  if (!items.length || !sessionId) return null;

  const answer = async (field, choice) => {
    setBusy(`${field}:${choice}`);
    try {
      const r = await postJson(`/api/listings/${sessionId}/resolve-conflict`,
                               { field, choice });
      toast(r.message, { kind: "success" });
      onResolved?.(r);
    } catch (e) {
      // Never swallowed: an answer that did not save means the seller's value
      // is still not going to eBay, which is the silence this exists to end.
      toast(`Couldn't save that choice: ${e.message}`, { kind: "error" });
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="rounded-tile bg-warning-soft border border-warning/30 p-4">
      <p className="text-sm text-ink flex gap-2">
        <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
        <span>
          <strong>
            {items.length === 1
              ? "One field changed in two places"
              : `${items.length} fields changed in two places`}
          </strong>
          {" — you edited "}
          {items.length === 1 ? "it" : "them"}
          {" here and "}
          {items.length === 1 ? "it" : "they"}
          {" also changed on eBay. We haven’t sent "}
          {items.length === 1 ? "it" : "them"}
          {" either way. Pick which version to keep."}
        </span>
      </p>

      <ul className="mt-3 flex flex-col gap-3">
        {items.map((c) => (
          <li key={c.field} className="rounded-tile bg-card border border-line p-3">
            <p className="text-xs font-bold uppercase tracking-wide text-ink-secondary">
              {c.label}
            </p>
            <div className="mt-2 flex flex-col gap-2">
              <Choice
                who="Yours" keeps={`Keep your ${c.label.toLowerCase()}`}
                value={c.mine}
                busy={busy === `${c.field}:mine`} disabled={!!busy}
                onPick={() => answer(c.field, "mine")}
              />
              <Choice
                who="On eBay" keeps={`Keep eBay’s ${c.label.toLowerCase()}`}
                value={c.ebay}
                busy={busy === `${c.field}:ebay`} disabled={!!busy}
                onPick={() => answer(c.field, "ebay")}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One side of one conflict.
 *
 * `keeps` is the accessible name and names both the side and the field. On
 * screen "Keep this" is right — the button sits beside the value it keeps and
 * a sentence each would crowd out the values the seller is comparing. To
 * anything reading the page by its controls, though, two buttons called "Keep
 * this" (four, with two conflicted fields) are the same question with no
 * answer: whichever is picked is what eventually reaches the seller's live
 * listing, and picking the wrong one overwrites a fix they made in Seller Hub
 * with a stale copy from here.
 */
function Choice({ who, keeps, value, busy, disabled, onPick }) {
  return (
    <div className="flex flex-wrap items-start gap-2 justify-between">
      <div className="min-w-0">
        <span className="text-xs text-ink-secondary">{who}: </span>
        <span className="text-sm text-ink break-words">
          {value || <em className="text-ink-secondary">empty</em>}
        </span>
      </div>
      <Button size="sm" variant="soft" loading={busy} disabled={disabled}
        aria-label={keeps} onClick={onPick}>
        Keep this
      </Button>
    </div>
  );
}
