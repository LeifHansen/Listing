import { useEffect } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/store";
import { Select } from "@/components/ui/fields";

/* The one shipping picker.
 *
 * On eBay there is exactly ONE object here: a fulfillment (shipping) business
 * policy, which is what names the carrier service(s) a listing can ship with.
 * The app used to call it three things — "Shipping policy" in Settings,
 * "Shipping service" on the listing, and "Add a shipping service" for the
 * button that creates one — with three separate copies of the same dropdown.
 * It reads as three settings that have to agree; it's one setting.
 *
 * So: one component, one word ("shipping policy"), used by the editor, the
 * drafts strip and the bulk queue.
 *
 * The empty value is meaningful and selectable: "" means "whatever Settings
 * says", which is what most listings want and what the server already
 * understands (Listing.fulfillment_policy_id = "" -> account default). The
 * old copies had no such option and rendered `value={value || accountDefault}`
 * instead, so when the stored id wasn't among the options — every listing
 * after connecting a different eBay account, since policy ids belong to one
 * account — the <select> displayed its first entry while the listing still
 * carried the stale id. The seller read "USPS Ground Advantage" and eBay was
 * handed a policy from a store they'd disconnected.
 */

// One request for the page, however many ask. Every draft card mounts this
// hook through its shipping dropdown, and each one asked on its own while
// nothing had answered yet -- a grid of thirty drafts sent thirty identical
// /api/ebay/policies requests on its first paint, and every one of those is a
// live round of eBay account calls (list_business_policies), against the same
// app-wide allowance everything else here spends carefully. The editor's
// PublishCard asks through here too. Cleared when it settles, so a later ask
// (a reconnect, a retry after a failure) goes out fresh.
let policiesInFlight = null;
export function loadPolicies() {
  if (!policiesInFlight) {
    policiesInFlight = api("/api/ebay/policies")
      .finally(() => { policiesInFlight = null; });
  }
  return policiesInFlight;
}

export function useFulfillmentPolicies() {
  const { ebay, policiesData, setPoliciesData } = useApp();
  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    loadPolicies().then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);
  return {
    connected: ebay.connected,
    policies: policiesData?.policies?.fulfillment || [],
    accountDefaultId: policiesData?.selected?.fulfillment_policy_id || "",
  };
}

export function policyLabel(p) {
  return `${p.name}${p.summary ? ` · ${p.summary}` : ""}`;
}

/* `value` is the listing's own fulfillment_policy_id ("" = follow Settings).
 * Renders nothing until eBay is connected and at least one policy exists —
 * there is nothing to choose between otherwise. */
export function ShippingPolicySelect({ value, onChange, ...rest }) {
  const { connected, policies, accountDefaultId } = useFulfillmentPolicies();
  if (!connected || !policies.length) return null;

  const current = value || "";
  const defaultPolicy = policies.find((p) => p.id === accountDefaultId);
  // An override pointing at a policy this account doesn't have (a different
  // eBay account's id, or one deleted on eBay). Never silently swallowed: it
  // gets its own option so the <select> shows what the listing actually holds.
  const orphaned = current && !policies.some((p) => p.id === current);

  return (
    <Select
      aria-label="Shipping policy"
      value={current}
      onChange={(e) => onChange(e.target.value)}
      {...rest}
    >
      <option value="">
        {defaultPolicy
          ? `Default — ${policyLabel(defaultPolicy)}`
          : "Default from Settings"}
      </option>
      {policies.map((p) => (
        <option key={p.id} value={p.id}>
          {policyLabel(p)}
        </option>
      ))}
      {orphaned && (
        <option value={current}>
          Unavailable policy — pick another
        </option>
      )}
    </Select>
  );
}

/* True when this listing's override names a policy the connected account
 * doesn't have, so callers can warn instead of letting the publish fail. */
export function usePolicyIsOrphaned(value) {
  const { connected, policies } = useFulfillmentPolicies();
  return Boolean(connected && value && policies.length
    && !policies.some((p) => p.id === value));
}
