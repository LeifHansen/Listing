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

/**
 * The same question, one branch down.
 *
 * A timeout is not the only way a request ends without an answer. A dropped
 * connection, a machine restarting mid-request, a phone changing networks —
 * fetch rejects with a TypeError rather than an AbortError, and that branch
 * said, for every method: "Network error — the server may be starting up. Try
 * again in a few seconds."
 *
 * On a POST that publishes a listing, "try again" is the one instruction that
 * can cost money. The browser cannot tell a request
 * that never left from one whose reply was lost, so a write has to assume the
 * second — the same asymmetry the Trading client applies on the server side.
 */
function failFetch() {
  vi.stubGlobal("fetch", () => Promise.reject(
    new TypeError("Failed to fetch")));
}

describe("what a dropped connection may tell the seller", () => {
  it("does not tell a write to just try again", async () => {
    failFetch();
    const err = await api("/api/publish", { method: "POST" })
      .then(() => null, (e) => e);
    expect(err).toBeTruthy();
    expect(err.message.toLowerCase()).not.toMatch(/try again in a few seconds/);
    expect(err.message.toLowerCase()).toMatch(/check/);
  });

  it("still tells a read to try again", async () => {
    failFetch();
    const err = await api("/api/listings").then(() => null, (e) => e);
    expect(err.message.toLowerCase()).toMatch(/try again/);
  });

  it("treats a method-less call as the read it is", async () => {
    failFetch();
    const err = await api("/api/listings", {}).then(() => null, (e) => e);
    expect(err.message.toLowerCase()).toMatch(/try again/);
  });
});

/**
 * The deadline covers the answer, not just its first line.
 *
 * fetch resolves as soon as the HEADERS arrive, and the timer was cleared
 * right there -- so a body that stalled half way (a phone between networks,
 * a few MB of /api/listings on a weak signal) was read with no deadline at
 * all, and the spinner or the skeletons stayed up for good: the thing the
 * deadline exists to prevent. A body that never finishes now ends the same
 * way a request that never answers does, and a connection that drops part
 * way through the body says what a dropped connection says.
 */
function stallBody() {
  vi.stubGlobal("fetch", (_url, opts) => Promise.resolve({
    ok: true, status: 200, headers: { get: () => null },
    json: () => new Promise((_resolve, reject) => {
      opts?.signal?.addEventListener("abort", () => {
        const err = new Error("aborted");
        err.name = "AbortError";
        reject(err);
      });
    }),
  }));
}

describe("an answer whose body stalls", () => {
  it("is given up on at the same deadline", async () => {
    stallBody();
    await expect(api("/api/listings", { timeoutMs: 10 }))
      .rejects.toThrow(/Nothing was lost/);
  });

  it("is a lost answer for a write, not a refusal", async () => {
    stallBody();
    const err = await api("/api/publish", { method: "POST", timeoutMs: 10 })
      .catch((e) => e);
    expect(err.message).toMatch(/may still have gone through/);
    expect(err.unknownOutcome).toBe(true);
  });

  it("says a connection dropped part way through, in words", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve({
      ok: true, status: 200, headers: { get: () => null },
      json: () => Promise.reject(new TypeError("network error")),
    }));
    const err = await api("/api/listings").catch((e) => e);
    expect(err.message).toMatch(/try again/i);
  });

  it("still lets a body that is simply not JSON say so", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve({
      ok: true, status: 200, headers: { get: () => null },
      json: () => Promise.reject(new SyntaxError("Unexpected token <")),
    }));
    await expect(api("/api/listings")).rejects.toThrow(SyntaxError);
  });
});
