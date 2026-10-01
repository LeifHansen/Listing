/**
 * What the policies panel on Settings may state about the seller's eBay
 * account.
 *
 * A policies fetch that failed used to leave the dropdowns empty, and empty
 * dropdowns are rendered as "your eBay account has no business policies".
 * The app was making a claim about the seller's account on the strength of
 * having failed to find out. (The page-wide Save that once lived beside this
 * is gone: every control on Settings now saves itself, to one system, and
 * reports on its own.)
 */

const POLICY_KEYS = ["fulfillment", "payment", "return"];

/**
 * What the policies panel may state about the seller's eBay account.
 *
 * Three outcomes are deliberately kept apart, and only one of them is a
 * statement about the account:
 *
 *   loading      — say nothing yet.
 *   unavailable  — we couldn't ask. Not "you have none": we don't know, and
 *                  offering to create policies on that basis is how a seller
 *                  ends up with duplicates of ones they already had.
 *   missing/ok   — eBay answered, so the answer can be reported.
 */
export function policyView({ status, error, policies } = {}) {
  if (status === "loading" || !status) return { kind: "loading" };
  if (status === "unavailable" || !policies) {
    return {
      kind: "unavailable",
      message: error
        ? `We couldn’t load your eBay policies (${error}). This doesn’t mean `
          + `you don’t have any — try again in a moment.`
        : "We couldn’t load your eBay policies. This doesn’t mean you don’t "
          + "have any — try again in a moment.",
    };
  }
  const missing = POLICY_KEYS.filter((k) => !(policies[k] || []).length);
  return missing.length ? { kind: "missing", missing } : { kind: "ok" };
}
