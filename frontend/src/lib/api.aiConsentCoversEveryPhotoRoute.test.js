/**
 * Every route that hands the seller's photos to the AI asks first.
 *
 * The consent gate in lib/api is a list of paths, and it named only the
 * upload routes. Others read the seller's photos into the AI with no upload
 * on this device in front of them: the dashboard's "Finish all" (and the
 * older capped enrich it replaced) fills item specifics from each listing's
 * own photos, and a split rewrites both drafts from theirs. An imported eBay
 * store has photos without ever passing through the uploader -- the server
 * fetches them from eBay for the fill -- so a seller who connected eBay and
 * pressed "Finish all" sent their whole store to the AI having never been
 * asked.
 *
 * The specifics fill and the guidance step's bulk/notes routes are left
 * ungated on purpose: the app only calls them straight after an identify or
 * an upload in the same tab, which already asked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api.js";
import { forgetAiConsentForTests } from "./aiConsent.js";

const ok = () => Promise.resolve({
  ok: true, status: 200, headers: { get: () => null },
  json: () => Promise.resolve({}),
});

// Storage that remembers nothing, and no dialog listening: an ask that
// cannot be delivered is refused, which is how a missing gate shows up here
// as a request that went out anyway.
beforeEach(() => {
  forgetAiConsentForTests();
  vi.stubGlobal("localStorage", {
    getItem: () => null, setItem: () => {}, removeItem: () => {},
  });
  vi.stubGlobal("fetch", vi.fn(ok));
});
afterEach(() => {
  vi.unstubAllGlobals();
  forgetAiConsentForTests();
});

describe("routes that send photos to the AI", () => {
  for (const path of [
    "/api/upload",
    "/api/upload-more/s1",
    "/api/bulk/upload",
    "/api/shelf-scan",
    "/api/identify/s1",
    "/api/identify-async/s1",
    "/api/listings/finish-all",
    "/api/listings/enrich",
    "/api/listings/s1/split",
  ]) {
    it(`asks before ${path}`, async () => {
      await expect(api(path, { method: "POST" })).rejects.toThrow(/without your OK/);
      expect(fetch).not.toHaveBeenCalled();
    });
  }
});

describe("routes that do not", () => {
  for (const path of [
    "/api/listings",
    "/api/listings/s1",
    "/api/listings/s1/duplicate",
    "/api/refine",
    "/api/ebay/end-and-relist",
  ]) {
    it(`does not ask before ${path}`, async () => {
      await api(path, { method: "POST" });
      expect(fetch).toHaveBeenCalledTimes(1);
    });
  }
});
