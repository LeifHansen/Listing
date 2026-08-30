/**
 * Has the seller agreed to their photos being sent to the AI provider?
 *
 * Apple's guideline 5.1.2(i) — and plain courtesy — require an explicit yes
 * before the FIRST transmission, and lib/api gates every photo-bearing call on
 * this one answer so a new upload flow is covered without remembering to add a
 * check.
 *
 * Split out of lib/api so it can be tested on its own: the interesting cases
 * are all about a browser that will not cooperate, and reproducing those means
 * replacing localStorage — which is awkward to do around a module that also
 * owns fetch, timeouts and the token gate.
 *
 * The rule this module exists to hold: a browser that will not let us remember
 * a yes has not given one. `hasAiConsent` used to answer TRUE when the read
 * threw, and localStorage throws in ordinary places — Safari with site data
 * blocked, an iOS WKWebView configured without storage, any browser set to
 * refuse it. This app ships to iOS. In every one of those the dialog never
 * appeared and the first upload sent the photos anyway, which is the one
 * outcome the choke point exists to prevent.
 *
 * A yes given in such a browser is still a real yes, so it is kept in memory
 * for the session: the seller is asked once, not once per upload. It is
 * deliberately not persisted anywhere else — a consent that outlives the
 * browser's own storage settings is a consent nobody can withdraw.
 */

const AI_CONSENT_KEY = "thryft-ai-consent";

// This session's answer, for a browser that cannot keep one. Never read as a
// substitute for a stored "yes" — only as a supplement to it.
let granted = false;

function store() {
  // Reading `localStorage` at all can throw (not just its methods), and it is
  // simply absent in some embedded webviews.
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch (e) {
    return null;
  }
}

export function hasAiConsent() {
  if (granted) return true;
  try {
    return store()?.getItem(AI_CONSENT_KEY) === "yes";
  } catch (e) {
    // Unreadable. Not evidence of a yes — see the module note.
    return false;
  }
}

export function grantAiConsent() {
  granted = true;
  try {
    store()?.setItem(AI_CONSENT_KEY, "yes");
  } catch (e) {
    // Nowhere to keep it past this tab. The in-memory flag above still means
    // the seller is asked once per session rather than once per upload.
  }
}

/**
 * Resolve once the seller has agreed (now, or previously); reject otherwise.
 *
 * The dialog lives in the app shell and listens for "ai-consent:needed"; this
 * raises it and waits for the verdict.
 *
 * If the ask cannot be DELIVERED, this rejects. Two ways that happens, and
 * the old version got both wrong:
 *
 *   * nothing is listening. dispatchEvent does not throw for that — it
 *     returns normally — so the promise was never settled either way and the
 *     upload hung forever, with a comment claiming this case was handled. The
 *     dialog now marks the event synchronously (listeners run inside the
 *     dispatch), so "did anyone take this?" is answerable.
 *   * raising the event throws (no CustomEvent, no window). That was caught
 *     and RESOLVED, which is the fail-open this module exists to remove: a
 *     browser we could not ask had, by that logic, agreed.
 *
 * Rejecting is not bricking the app. It refuses one upload with a reason,
 * which is exactly what a decline already does.
 */
export function ensureAiConsent() {
  if (hasAiConsent()) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const refuse = () => reject(new Error(
      "Photos aren't analyzed without your OK — you can agree any time."));
    const detail = {
      shown: false,
      accept: () => { grantAiConsent(); resolve(); },
      decline: refuse,
    };
    try {
      window.dispatchEvent(new CustomEvent("ai-consent:needed", { detail }));
    } catch (e) {
      refuse();       // could not even raise the ask. Not a yes.
      return;
    }
    // Raised, but did the dialog take it? Listeners run inside the dispatch,
    // so by here the answer is already recorded.
    if (!detail.shown) refuse();
  });
}

/** Test seam: forget the in-memory answer between cases. */
export function forgetAiConsentForTests() {
  granted = false;
}
