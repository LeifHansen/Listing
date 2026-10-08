/**
 * What the policies panel on Settings is allowed to say.
 *
 * A policies fetch that FAILED left the dropdowns empty, and empty dropdowns
 * render as "you don't have business policies, here's a button to create
 * them" — the app stating something about the seller's eBay account that it
 * had just failed to find out. (The page-wide Save that used to be tested
 * beside this is gone: every Settings control saves itself now.)
 */
import { describe, expect, it } from "vitest";

import { policyView } from "./settingsSections.js";

describe("what the policies panel is allowed to say", () => {
  const three = { fulfillment: [{ id: "1" }], payment: [{ id: "2" }], return: [{ id: "3" }] };

  it("says nothing about the account while it is still loading", () => {
    expect(policyView({ status: "loading" }).kind).toBe("loading");
  });

  it("does not report a failed load as an account with no policies", () => {
    // The finding. This rendered the "you have no business policies" tile.
    const v = policyView({ status: "unavailable", error: "network" });
    expect(v.kind).toBe("unavailable");
    expect(v.message).toMatch(/couldn.t|could not/i);
    expect(v.message.toLowerCase()).not.toContain("you don't have");
  });

  it("reports genuinely missing policies once eBay has actually answered", () => {
    const v = policyView({ status: "ready", policies: { ...three, payment: [] } });
    expect(v.kind).toBe("missing");
    expect(v.missing).toEqual(["payment"]);
  });

  it("is quiet when all three exist", () => {
    expect(policyView({ status: "ready", policies: three }).kind).toBe("ok");
  });

  it("treats a missing policies object as unknown, not as empty", () => {
    // `data` is seeded from a shared cache, so it can be truthy having loaded
    // nothing here. Absent is not the same as answered-with-none.
    expect(policyView({ status: "ready" }).kind).toBe("unavailable");
  });
});
