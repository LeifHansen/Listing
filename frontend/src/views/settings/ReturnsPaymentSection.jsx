import { useCallback, useState } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toaster";
import { PolicyTermsDialog } from "@/components/PolicyTermsDialog";
import { useSettings } from "./context";
import { PanelUnavailable } from "./PanelUnavailable";
import { PolicySelect } from "./ShippingSection";

const PAYER_WORDS = { BUYER: "buyer pays return shipping", SELLER: "you pay return shipping" };

/** The promise a return policy makes, in one line under the select. */
function returnTerms(policy) {
  const t = policy?.terms;
  if (!t) return "";
  if (!t.accepted) return "No returns accepted";
  const days = t.days ? `${t.days}-day returns` : "Returns accepted";
  const payer = PAYER_WORDS[t.payer] || "";
  return payer ? `${days} · ${payer}` : days;
}

function paymentTerms(policy) {
  if (!policy || policy.immediate_pay == null) return "";
  return policy.immediate_pay
    ? "Immediate payment required on Buy It Now"
    : "Buyers can commit before paying";
}

/**
 * The return and payment policies every new listing publishes under, with
 * their terms shown where they are chosen -- and, when the account has no
 * policies yet, the three ways to get them.
 */
export function ReturnsPaymentSection() {
  const { ebay, setPoliciesData } = useApp();
  const { toast } = useToast();
  const {
    data, loading, policies, loadPolicies, savePolicy, prefs, loadOverview,
  } = useSettings();
  const [optingIn, setOptingIn] = useState(false);
  const [creating, setCreating] = useState(false);
  const [reviewing, setReviewing] = useState(false);

  // Runs only from the terms dialog's confirm. `options` are the ones the
  // preview described, echoed back so the policies created are the policies
  // shown -- not the server's defaults as they stand a moment later.
  const createPolicies = useCallback(async (options) => {
    setCreating(true);
    try {
      const r = await postJson("/api/ebay/ensure-all-policies",
                               { ...options, accept_terms: true });
      const made = r.created || [];
      const failed = Object.keys(r.errors || {});
      if (failed.length) {
        toast(
          `Couldn't create your ${failed.join(" and ")} policy. `
          + `eBay said: ${r.errors[failed[0]]}`,
          { kind: "error" });
      } else {
        toast(made.length
          ? `Created your ${made.join(", ")} policy — you can publish now.`
          : "You already had all three policies, so nothing was changed.",
          { kind: "success" });
      }
      setReviewing(false);
      setPoliciesData(null);
      loadPolicies();
    } catch (e) {
      toast(e.message, { kind: "error" });
    } finally {
      setCreating(false);
    }
  }, [loadPolicies, setPoliciesData, toast]);

  if (!ebay.connected) {
    return (
      <p className="text-sm text-ink-secondary">
        Connect your eBay account first — your return and payment policies come
        from there.
      </p>
    );
  }
  if (!loading && policies.kind === "unavailable" && !data) {
    return <PanelUnavailable message={policies.message} onRetry={loadPolicies} />;
  }
  if (loading || !data) return <Skeleton className="h-28 rounded-tile" />;

  const selected = data.selected || {};
  const returns = data.policies?.return || [];
  const payment = data.policies?.payment || [];
  const kindLabel = { fulfillment: "shipping policy", payment: "payment policy",
                      return: "return policy" };

  return (
    <div className="flex flex-col gap-6 max-w-lg">
      <PolicySelect
        field="return_policy_id"
        label="Return policy"
        options={returns}
        value={selected.return_policy_id || ""}
        unavailable={policies.kind === "unavailable"}
        note="Shown to buyers on every listing. 30-day returns is what eBay’s Top Rated Plus asks for."
        terms={returnTerms(returns.find((p) => p.id === selected.return_policy_id))}
        onSave={savePolicy}
      />
      <PolicySelect
        field="payment_policy_id"
        label="Payment policy"
        options={payment}
        value={selected.payment_policy_id || ""}
        unavailable={policies.kind === "unavailable"}
        note="eBay managed payments handles the money; the policy only says whether a buyer must pay at checkout."
        terms={paymentTerms(payment.find((p) => p.id === selected.payment_policy_id))}
        onSave={savePolicy}
      />

      <a
        href={data.manage_url} target="_blank" rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-sm font-semibold text-blue self-start"
      >
        Manage policies on eBay <ExternalLink size={13} aria-hidden />
      </a>

      {policies.kind === "unavailable" && (
        <PanelUnavailable message={policies.message} onRetry={loadPolicies} />
      )}

      {policies.kind === "missing" && (
        <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 text-sm">
          <p className="text-ink flex gap-2">
            <AlertTriangle size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
            <span>
              eBay requires shipping, payment &amp; return policies to publish, and your
              account has no{" "}
              {policies.missing.map((k) => kindLabel[k]).join(", ")}
              . That usually means business policies are switched off for the account —
              they’re an eBay seller program, not a default.
            </span>
          </p>
          {/* Opting in is necessary and not sufficient: the account still
              needs one policy of each kind. Separate buttons because eBay's
              opt-in takes up to 24h, so right after it the create will
              legitimately fail -- and saying which half went wrong beats one
              button that hides it. */}
          <div className="flex flex-wrap items-center gap-3 mt-3">
            <Button
              size="sm" variant="soft" disabled={optingIn}
              onClick={async () => {
                setOptingIn(true);
                try {
                  const r = await postJson("/api/ebay/opt-in-policies", {});
                  toast(r.message, { kind: r.already ? "success" : "info" });
                  if (r.already) { loadPolicies(); loadOverview(); }
                } catch (e) {
                  toast(e.message, { kind: "error" });
                } finally {
                  setOptingIn(false);
                }
              }}
            >
              {optingIn ? "Asking eBay…" : "Turn on business policies"}
            </Button>
            {/* Opens the terms first: these policies commit the seller to a
                dispatch deadline eBay scores them on, a return window and
                who pays return postage -- all of it published to buyers. */}
            <Button
              size="sm" variant="soft" disabled={creating}
              onClick={() => setReviewing(true)}
            >
              {creating ? "Creating…" : "Create my policies…"}
            </Button>
          </div>
          {/* The International shipping switch reaches the policy too: the
              terms describe a policy that ships worldwide through eBay when
              it is on, and the create echoes those terms back. */}
          <PolicyTermsDialog
            open={reviewing}
            busy={creating}
            options={{
              international_shipping: Boolean(prefs?.ebay_international_shipping),
            }}
            onClose={() => setReviewing(false)}
            onConfirm={createPolicies}
          />
        </div>
      )}
    </div>
  );
}
