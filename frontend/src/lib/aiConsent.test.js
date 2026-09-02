/**
 * A browser that won't let us remember a "yes" has not given one.
 *
 * `hasAiConsent()` reads a localStorage flag and answered TRUE when the read
 * threw. localStorage throws in real, ordinary places -- Safari with site
 * data blocked, an iOS WKWebView configured without storage, a browser set to
 * refuse it -- and this app ships to iOS. In every one of those, the consent
 * dialog never appeared and the first upload transmitted the seller's photos
 * to the AI provider regardless.
 *
 * The comment three lines above the bug cites Apple's guideline 5.1.2(i),
 * which requires explicit consent before that first transmission, and says
 * "one choke point here beats a check in every upload flow". The choke point
 * was open.
 *
 * "We could not find out" is not a yes. A storage failure now reads as NOT
 * consented, which costs a dialog and nothing else -- and a consent given in
 * such a browser is remembered for the session in memory, so the seller is
 * asked once rather than on every upload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ensureAiConsent, forgetAiConsentForTests, grantAiConsent, hasAiConsent,
} from "./aiConsent.js";

function storageThatThrows() {
  const boom = () => { throw new DOMException("denied", "SecurityError"); };
  vi.stubGlobal("localStorage", { getItem: boom, setItem: boom, removeItem: boom });
}

function workingStorage(initial = {}) {
  const data = { ...initial };
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => { data[k] = String(v); },
    removeItem: (k) => { delete data[k]; },
  });
  return data;
}

beforeEach(() => forgetAiConsentForTests());
afterEach(() => { vi.unstubAllGlobals(); forgetAiConsentForTests(); });

describe("what counts as consent to send photos to the AI", () => {
  it("does not read an unreadable browser as a yes", () => {
    storageThatThrows();
    expect(hasAiConsent()).toBe(false);
  });

  it("does not read a missing flag as a yes", () => {
    workingStorage();
    expect(hasAiConsent()).toBe(false);
  });

  it("honours a stored yes", () => {
    workingStorage({ "thryft-ai-consent": "yes" });
    expect(hasAiConsent()).toBe(true);
  });

  it("does not read some other stored value as a yes", () => {
    workingStorage({ "thryft-ai-consent": "no" });
    expect(hasAiConsent()).toBe(false);
  });

  it("remembers a yes for the session when it cannot be written down", () => {
    storageThatThrows();
    expect(hasAiConsent()).toBe(false);
    grantAiConsent();
    // Asked once, not once per upload. The answer is real -- the person gave
    // it -- so the only thing missing is somewhere to keep it past this tab.
    expect(hasAiConsent()).toBe(true);
  });

  it("survives a browser with no localStorage at all", () => {
    vi.stubGlobal("localStorage", undefined);
    expect(hasAiConsent()).toBe(false);
    expect(() => grantAiConsent()).not.toThrow();
    expect(hasAiConsent()).toBe(true);
  });
});

describe("asking for consent when nobody can answer", () => {
  function noStorage() {
    vi.stubGlobal("localStorage", undefined);
  }

  it("refuses rather than consents when the ask cannot be delivered", async () => {
    noStorage();
    // No listener mounted. dispatchEvent does NOT throw for that -- it
    // returns normally -- so the old code neither resolved nor rejected and
    // the upload hung forever, while its comment claimed it was handling
    // exactly this case. Either way the answer is the same: we did not ask,
    // so we do not have a yes.
    vi.stubGlobal("window", { dispatchEvent: () => true });
    await expect(ensureAiConsent()).rejects.toThrow();
    expect(hasAiConsent()).toBe(false);
  });

  it("refuses when raising the ask throws", async () => {
    noStorage();
    vi.stubGlobal("window", {
      dispatchEvent: () => { throw new Error("no CustomEvent here"); },
    });
    await expect(ensureAiConsent()).rejects.toThrow();
    expect(hasAiConsent()).toBe(false);
  });

  it("waits for the person when the dialog is listening", async () => {
    noStorage();
    vi.stubGlobal("window", {
      dispatchEvent: (e) => {
        e.detail.shown = true;                 // what the dialog marks
        setTimeout(() => e.detail.accept(), 0);
        return true;
      },
    });
    await expect(ensureAiConsent()).resolves.toBeUndefined();
    expect(hasAiConsent()).toBe(true);
  });

  it("passes a decline back to the caller", async () => {
    noStorage();
    vi.stubGlobal("window", {
      dispatchEvent: (e) => {
        e.detail.shown = true;
        setTimeout(() => e.detail.decline(), 0);
        return true;
      },
    });
    await expect(ensureAiConsent()).rejects.toThrow(/without your OK/i);
    expect(hasAiConsent()).toBe(false);
  });

  it("does not ask again once consent is held", async () => {
    workingStorage({ "thryft-ai-consent": "yes" });
    let asked = 0;
    vi.stubGlobal("window", { dispatchEvent: () => { asked += 1; return true; } });
    await expect(ensureAiConsent()).resolves.toBeUndefined();
    expect(asked).toBe(0);
  });
});
