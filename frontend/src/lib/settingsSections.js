/**
 * Settings writes to two systems. It has to report on them separately.
 *
 * One Save button sent the seller's local packing defaults to this app and
 * their publish defaults to eBay, in one try block, and reported one outcome.
 * The first can commit and the second fail, and when it did the seller was
 * told "Couldn't save" — about work that had, in fact, been saved. The
 * obvious response to that message is to type it all again.
 *
 * The loading side had the same shape: a policies fetch that failed left the
 * dropdowns empty, and empty dropdowns are rendered as "your eBay account has
 * no business policies". The app was making a claim about the seller's
 * account on the strength of having failed to find out.
 */

/** Text for a thrown value that may not be an Error. */
function reason(e) {
  const msg = (e && e.message) || String(e || "");
  return msg || "something went wrong";
}

function list(names) {
  if (names.length <= 1) return names[0] || "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * Run each section's save independently and say what happened to each.
 *
 * Sequential rather than Promise.allSettled: these are ordinary writes on a
 * seller's own account, and firing them together only makes the failure modes
 * harder to reason about. What matters is that one failing does not skip the
 * others, and that the answer names which was which.
 *
 * @param {{name: string, when?: boolean, run: () => Promise<any>}[]} sections
 */
export async function saveSections(sections) {
  const saved = [];
  const failed = [];
  for (const section of sections) {
    if (section.when === false) continue;
    try {
      await section.run();
      saved.push(section.name);
    } catch (e) {
      failed.push({ name: section.name, message: reason(e) });
    }
  }

  let message;
  if (!failed.length) {
    message = "Defaults saved — they apply to every new listing you draft and publish.";
  } else if (!saved.length) {
    message = `Couldn't save ${list(failed.map((f) => f.name))}: ${failed[0].message}`;
  } else {
    // The half that committed is named first and explicitly, because the
    // seller's next decision is whether to retype it.
    message = `${list(saved)} saved. Couldn't save `
      + `${list(failed.map((f) => f.name))}: ${failed[0].message}`;
  }
  return { ok: !failed.length, saved, failed, message };
}

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
