/**
 * What a timeout is allowed to tell the seller.
 *
 * Every timed-out request used to say "Nothing was lost — try again",
 * whatever it was. That is true of a read and false of a write: giving up on
 * a request is not the same as the server giving up on it, and a publish, a
 * promotion, a policy creation or a delete may already have reached eBay and
 * succeeded, with only the response lost.
 *
 * Telling that seller nothing was lost invites them to publish again, which is
 * how one item becomes two live listings — the duplicate the create-side
 * idempotency work exists to prevent, arriving instead through the copy.
 */
import { describe, expect, it, vi, afterEach } from "vitest";

import { api, isRepeatable } from "./api.js";

afterEach(() => {
  vi.restoreAllMocks();
});

/** Make fetch hang, so the request's own deadline is what ends it. */
function stallFetch() {
  vi.stubGlobal("fetch", (_url, opts) =>
    new Promise((_resolve, reject) => {
      opts?.signal?.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    }));
}

describe("isRepeatable", () => {
  it("treats reads as safe to repeat", () => {
    expect(isRepeatable("GET")).toBe(true);
    expect(isRepeatable("head")).toBe(true);
  });

  it("treats every write as unsafe to repeat", () => {
    for (const method of ["POST", "PUT", "PATCH", "DELETE", "post"]) {
      expect(isRepeatable(method)).toBe(false);
    }
  });

  it("defaults a missing method to GET, which is what fetch does", () => {
    expect(isRepeatable(undefined)).toBe(true);
    expect(isRepeatable("")).toBe(true);
  });
});

describe("a timed-out request", () => {
  it("tells a reader nothing was lost", async () => {
    stallFetch();
    await expect(api("/api/listings", { timeoutMs: 10 }))
      .rejects.toThrow(/Nothing was lost/);
  });

  it("never tells a writer nothing was lost", async () => {
    stallFetch();
    const err = await api("/api/publish", { method: "POST", timeoutMs: 10 })
      .catch((e) => e);

    expect(err.message).not.toMatch(/Nothing was lost/);
    expect(err.message).toMatch(/may still have gone through/);
  });

  it("tells a writer to check before repeating it", async () => {
    stallFetch();
    const err = await api("/api/listings/abc", { method: "DELETE", timeoutMs: 10 })
      .catch((e) => e);

    expect(err.message).toMatch(/check before trying again/i);
  });
});
