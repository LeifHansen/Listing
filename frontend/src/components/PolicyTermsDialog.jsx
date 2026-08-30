import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";

/**
 * What "Create my policies" is about to promise on the seller's behalf.
 *
 * An eBay business policy is not a preference. It is published with every
 * listing that references it, and eBay scores the seller against it: dispatch
 * later than the policy says and it counts against their standing. The app
 * chose a 2-day dispatch window, 30-day returns, buyer-paid return postage
 * and required immediate payment behind a button that said none of it, and
 * the seller found out by reading it back off eBay afterwards.
 *
 * So the terms are shown first, and the create only goes out if the seller
 * agrees to them here.
 */
export function PolicyTermsDialog({ open, onClose, onConfirm, options = {}, busy = false }) {
  const query = useMemo(() => new URLSearchParams(
    Object.entries(options).filter(([, v]) => v !== undefined && v !== ""),
  ).toString(), [options]);
  // Keyed by the query rather than reset inside the effect: an answer to a
  // previous set of options must not be shown as if it described these ones.
  // Anything not keyed to the query in flight reads as still loading, which
  // also keeps the confirm button disabled while it is.
  const [answer, setAnswer] = useState({});

  useEffect(() => {
    if (!open) return undefined;
    let live = true;
    api(`/api/ebay/policy-preview${query ? `?${query}` : ""}`)
      .then((data) => live && setAnswer({ key: query, status: "ready", data }))
      // A preview that cannot be loaded must not become a create. The button
      // stays disabled and says why, rather than falling back to "just do it"
      // — that is the exact shape of the bug this screen exists to fix.
      .catch((e) => live
        && setAnswer({ key: query, status: "error", message: e.message }));
    return () => { live = false; };
  }, [open, query]);

  const state = answer.key === query ? answer : { status: "loading" };
  const kinds = state.status === "ready"
    ? ["fulfillment", "payment", "return"].map((k) => [k, state.data.kinds[k]])
    : [];

  return (
    <Dialog open={open} onClose={onClose} wide title="What these policies will say">
      <p className="text-sm text-ink-soft">
        eBay shows these terms to buyers on every listing that uses the policy, and
        holds you to them. You can change any of it later in Seller Hub.
      </p>

      {state.status === "loading" && (
        <p className="flex items-center gap-2 text-sm text-ink-soft mt-5">
          <Loader2 size={15} className="animate-spin" aria-hidden />
          Loading the terms…
        </p>
      )}

      {state.status === "error" && (
        <p className="flex gap-2 text-sm text-ink mt-5 rounded-tile bg-warning-soft border border-warning/30 p-4">
          <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <span>We couldn’t load the terms, so nothing was created. {state.message}</span>
        </p>
      )}

      {state.status === "ready" && (
        <div className="mt-5 space-y-5">
          {kinds.map(([key, kind]) => (
            <section key={key}>
              <h3 className="font-bold text-ink text-sm">{kind.title}</h3>
              <p className="text-xs text-ink-soft mb-2">Named “{kind.name}” on eBay</p>
              <dl className="rounded-tile border border-line divide-y divide-line">
                {kind.terms.map((t) => (
                  <div key={t.label} className="p-3">
                    <div className="flex flex-wrap gap-x-2 text-sm">
                      <dt className="text-ink-soft">{t.label}:</dt>
                      <dd className="font-semibold text-ink">{t.value}</dd>
                    </div>
                    {t.detail && (
                      <p className="text-xs text-ink-soft mt-1">{t.detail}</p>
                    )}
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      )}

      <div className="flex flex-wrap justify-end gap-3 mt-6">
        <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button
          disabled={state.status !== "ready" || busy}
          onClick={() => onConfirm(state.data.options)}
        >
          {busy ? "Creating…" : "Create these policies"}
        </Button>
      </div>
    </Dialog>
  );
}
