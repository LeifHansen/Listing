// The shipping services a seller can set as their default. This list is
// carrier-service level (what buyers actually see), independent of whether
// eBay is connected yet — so the preference can be chosen up front and then
// mapped to the matching eBay business policy at publish time.
//
// `token` is a normalized substring matched against an eBay fulfillment
// policy's service names (see policyMatchesService) so "USPS Ground
// Advantage" the preference lines up with the policy that ships with it.
export const SHIPPING_SERVICES = [
  { value: "usps_ground_advantage", label: "USPS Ground Advantage", token: "groundadvantage" },
  { value: "usps_priority", label: "USPS Priority Mail", token: "priority" },
  { value: "usps_priority_express", label: "USPS Priority Mail Express", token: "priorityexpress" },
  { value: "usps_media", label: "USPS Media Mail", token: "media" },
  { value: "ups_ground", label: "UPS Ground", token: "upsground" },
  { value: "ups_2day", label: "UPS 2nd Day Air", token: "ups2" },
  { value: "ups_next_day", label: "UPS Next Day Air", token: "upsnext" },
  { value: "fedex_home", label: "FedEx Home Delivery", token: "fedexhome" },
  { value: "fedex_ground", label: "FedEx Ground", token: "fedexground" },
];

export const DEFAULT_SHIPPING_SERVICE = "usps_ground_advantage";

const norm = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");

// Does this eBay fulfillment policy ship with the given service preference?
export function policyMatchesService(policy, value) {
  const svc = SHIPPING_SERVICES.find((s) => s.value === value);
  if (!svc) return false;
  return (policy.services || []).some((name) => norm(name).includes(svc.token));
}

export function shippingServiceLabel(value) {
  return (SHIPPING_SERVICES.find((s) => s.value === value) || {}).label || "";
}
