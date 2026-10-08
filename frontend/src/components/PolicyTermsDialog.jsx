import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Select, Toggle } from "@/components/ui/fields";

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
  // The terms the seller may change before agreeing: the shipping service,
  // the dispatch window, the return window and who pays return postage, and
  // immediate payment. `options` from the caller are the starting point (the
  // International shipping switch rides in that way); what is picked here
  // layers over it, and the query — which keys the preview — is the merge.
  const [choices, setChoices] = useState({});
  const merged = useMemo(() => ({ ...options, ...choices }), [options, choices]);
  const query = useMemo(() => new URLSearchParams(
    Object.entries(merged).filter(([, v]) => v !== undefined && v !== ""),
  ).toString(), [merged]);
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
  // The choices the server offers ride on the LAST answer, so the pickers
  // stay on screen while a new preview loads instead of blinking away.
  const [lastReady, setLastReady] = useState(null);
  if (state.status === "ready" && state.data !== lastReady) setLastReady(state.data);
  const shown = lastReady;
  const pick = (key, value) => setChoices((c) => ({ ...c, [key]: value }));
  const picked = shown ? { ...shown.options, ...choices } : choices;
  const dayWord = (d) => (d === 0 ? "Same business day" : `${d} business day${d === 1 ? "" : "s"}`);

  return (
    <Dialog open={open} onClose={onClose} wide title="What these policies will say">
      <p className="text-sm text-ink-secondary">
        eBay shows these terms to buyers on every listing that uses the policy, and
        holds you to them. Change any of it here before agreeing, or later in Seller Hub.
      </p>

      {shown && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <Field label="Shipping service">
            <Select value={picked.service_code || ""}
              onChange={(e) => pick("service_code", e.target.value)}>
              {(shown.services || []).map((s) => (
                <option key={s.code} value={s.code}>{s.label}{s.note ? ` — ${s.note}` : ""}</option>
              ))}
            </Select>
          </Field>
          <Field label="Dispatch time" note="A shipping policy you already have is reused as it is; change its handling time from Settings → Shipping.">
            <Select value={String(picked.handling_days ?? "")}
              onChange={(e) => pick("handling_days", Number(e.target.value))}>
              {(shown.choices?.handling_days || []).map((d) => (
                <option key={d} value={String(d)}>{dayWord(d)}</option>
              ))}
            </Select>
          </Field>
          <Field label="Return window">
            <Select value={String(picked.return_days ?? "")}
              onChange={(e) => pick("return_days", Number(e.target.value))}>
              {(shown.choices?.return_days || []).map((d) => (
                <option key={d} value={String(d)}>{d} days</option>
              ))}
            </Select>
          </Field>
          <Field label="Return postage">
            <Select value={picked.return_payer || "BUYER"}
              onChange={(e) => pick("return_payer", e.target.value)}>
              <option value="BUYER">The buyer pays</option>
              <option value="SELLER">I pay</option>
            </Select>
          </Field>
          <Toggle
            className="sm:col-span-2"
            checked={picked.immediate_pay !== false}
            onChange={(on) => pick("immediate_pay", on)}
            label="Require immediate payment on Buy It Now"
            note="The item stays on sale until the buyer actually pays, so an unpaid commitment can’t hold it."
          />
        </div>
      )}

      {state.status === "loading" && (
        <p className="flex items-center gap-2 text-sm text-ink-secondary mt-5">
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
              <p className="text-xs text-ink-secondary mb-2">Named “{kind.name}” on eBay</p>
              <dl className="rounded-tile border border-line divide-y divide-line">
                {kind.terms.map((t) => (
                  <div key={t.label} className="p-3">
                    <div className="flex flex-wrap gap-x-2 text-sm">
                      <dt className="text-ink-secondary">{t.label}:</dt>
                      <dd className="font-semibold text-ink">{t.value}</dd>
                    </div>
                    {t.detail && (
                      <p className="text-xs text-ink-secondary mt-1">{t.detail}</p>
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
