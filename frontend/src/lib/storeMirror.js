/**
 * What the store-mirror line at the top of the Dashboard may claim.
 *
 * Its resting state is a green tick and "Live mirror of your eBay store",
 * titled "Everything below reflects your actual eBay store". That is the
 * strongest completeness claim in the app, and it was made unconditionally —
 * including when the sync had just told us it could not cover the store.
 *
 * `/api/ebay/sync-listings` answers with `partial`, set when the pass did not
 * cover everything for either of two reasons: it SAMPLED (a status re-check
 * is one eBay call per listing, so a big store is deliberately checked a
 * hundred at a time), or the read itself was capped (the store is bigger than
 * one page of it, so the oldest live listings never reached the sweep at
 * all). Nobody read the flag.
 *
 * A partial mirror is not a broken one and must not read as an error: the
 * records on screen are real, they are just not all of them and not all
 * freshly checked. So it keeps the line and drops the certainty.
 */

export function storeMirrorView({
  user = null, connected = false, syncing = false, error = "",
  partial = false, progress = null,
} = {}) {
  if (!user) return { kind: "hidden" };
  if (!connected) return { kind: "not-connected" };
  if (syncing) return { kind: "syncing", progress };
  if (error) return { kind: "error" };
  if (partial) {
    return {
      kind: "partial",
      text: "Mirroring your eBay store — checked in batches",
      title: "Your store is bigger than one pass can cover, so statuses are "
        + "re-checked a batch at a time. Everything below is real; some of it "
        + "may not have been checked recently.",
    };
  }
  return {
    kind: "mirror",
    text: "Live mirror of your eBay store",
    title: "Everything below reflects your actual eBay store — created here "
      + "or not.",
  };
}
