/**
 * A publish that outran the wait is not a publish that failed.
 *
 * The report: a bulk run of seven listings came back with five published and
 * "two left behind". Both halves of that are this file.
 *
 * The 90-second default deadline was the cause. Every publish went out on it,
 * and 90 seconds is not the shape of the work: server-side, the Trading create
 * alone is capped at 45s — and eBay ingests every photo in the listing inside
 * that one call — with the token refresh, the business policies, the ship-from
 * lookup, the location ensure and the promotion capped at 30s each around it.
 * The two slowest items in a batch are the ones that cross 90s, which is
 * exactly the two that "failed".
 *
 * What the client does next was the damage. Giving up on a request does not
 * stop the server: those publishes ran to the end and very likely went live,
 * while the queue showed them as refused drafts — and the one thing a seller
 * does with a refused draft is publish it again. That is a second live listing
 * for one item, the duplicate the whole create-side idempotency story exists
 * to prevent, arriving through the copy that reports the result.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { PUBLISH_TIMEOUT_MS } from "@/lib/api";
import {
  publishListing, resolveLostPublish, UNCONFIRMED_PUBLISH,
} from "./publishShared";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const OLD_DEFAULT_MS = 90000;

function jsonOk(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
  });
}

function jsonErr(status, detail) {
  return Promise.resolve({
    ok: false,
    status,
    statusText: "Bad Request",
    headers: { get: () => "application/json" },
    json: () => Promise.resolve({ detail }),
  });
}

/** Route stubbed fetch by path; anything unrouted resolves empty. */
function routeFetch(routes) {
  vi.stubGlobal("fetch", (url, opts) => {
    const path = String(url);
    const hit = Object.keys(routes).find((k) => path.includes(k));
    return hit ? routes[hit](opts, path) : jsonOk({});
  });
}

/** A request that never answers, ended only by its own deadline. */
function stall(seen) {
  return (opts) => new Promise((_resolve, reject) => {
    opts?.signal?.addEventListener("abort", () => {
      if (seen) seen.aborted = true;
      const err = new Error("aborted");
      err.name = "AbortError";
      reject(err);
    });
  });
}

describe("the deadline a publish runs on", () => {
  it("outlasts the 90s default the batch was losing items to", async () => {
    expect(PUBLISH_TIMEOUT_MS).toBeGreaterThan(OLD_DEFAULT_MS);
  });

  it("is still waiting where the default would have given up", async () => {
    vi.useFakeTimers();
    const publish = { aborted: false };
    routeFetch({
      "/api/save/": () => jsonOk({ ok: true }),
      "/api/publish": stall(publish),
      // The record never turns live, so the lost answer stays unresolved and
      // the original error is what reaches the caller.
      "/api/listings/": () => jsonOk({ status: "draft", listing: {} }),
    });

    const settled = publishListing("l1", { title: "A lamp" }, null).catch((e) => e);

    await vi.advanceTimersByTimeAsync(OLD_DEFAULT_MS + 1000);
    expect(publish.aborted).toBe(false);

    await vi.advanceTimersByTimeAsync(PUBLISH_TIMEOUT_MS);
    expect(publish.aborted).toBe(true);

    // Drain the record polls that follow a lost answer.
    await vi.advanceTimersByTimeAsync(60000);
    const err = await settled;
    expect(err.message).toMatch(/may still have gone through/);
    expect(err.unknownOutcome).toBe(true);
  });

  it("leaves the save on the default — it waits on us, not on eBay", async () => {
    vi.useFakeTimers();
    const save = { aborted: false };
    routeFetch({ "/api/save/": stall(save) });

    const settled = publishListing("l1", {}, null).catch((e) => e);
    await vi.advanceTimersByTimeAsync(OLD_DEFAULT_MS + 1000);

    expect(save.aborted).toBe(true);
    await settled;
  });
});

describe("resolveLostPublish", () => {
  const now = () => Promise.resolve();   // no real waiting between polls

  it("waits for the record to settle rather than asking once", async () => {
    let reads = 0;
    routeFetch({
      "/api/listings/": () => {
        reads += 1;
        return jsonOk(reads < 3
          ? { status: "draft", listing: {} }
          : { status: "published", listing: { ebay_listing_id: "1122334455" } });
      },
    });

    expect(await resolveLostPublish("l1", { wait: now }))
      .toEqual({ published: true, listing_id: "1122334455" });
    expect(reads).toBe(3);
  });

  it("takes the store's own spelling of live", async () => {
    routeFetch({
      "/api/listings/": () => jsonOk({
        status: "live", listing: { ebay_listing_id: "999" },
      }),
    });
    expect((await resolveLostPublish("l1", { wait: now })).published).toBe(true);
  });

  it("will not call it live without the item id to prove it", async () => {
    routeFetch({
      "/api/listings/": () => jsonOk({ status: "published", listing: {} }),
    });
    expect(await resolveLostPublish("l1", { wait: now }))
      .toEqual({ published: false });
  });

  it("says it does not know rather than guessing", async () => {
    routeFetch({ "/api/listings/": () => jsonOk({ status: "draft", listing: {} }) });
    expect(await resolveLostPublish("l1", { wait: now }))
      .toEqual({ published: false });
  });

  it("keeps asking when a read itself fails — that proves nothing either way",
    async () => {
      let reads = 0;
      routeFetch({
        "/api/listings/": () => {
          reads += 1;
          return reads < 3
            ? Promise.reject(new TypeError("Failed to fetch"))
            : jsonOk({ status: "published", listing: { ebay_listing_id: "77" } });
        },
      });
      expect((await resolveLostPublish("l1", { wait: now })).published).toBe(true);
    });
});

describe("a publish whose answer was lost", () => {
  it("is reported as the live listing it turned out to be", async () => {
    vi.useFakeTimers();
    routeFetch({
      "/api/save/": () => jsonOk({ ok: true }),
      // A dropped connection: sent, or sent-ness unproven. Same unknown
      // outcome as the timeout, one branch down in api.js.
      "/api/publish": () => Promise.reject(new TypeError("Failed to fetch")),
      "/api/listings/": () => jsonOk({
        status: "published", listing: { ebay_listing_id: "1122334455" },
      }),
    });

    const settled = publishListing("l1", {}, null);
    await vi.advanceTimersByTimeAsync(60000);
    const res = await settled;

    expect(res.published).toBe(true);
    expect(res.listing_id).toBe("1122334455");
  });

  it("is never resolved this way for a draft save — nothing goes to eBay",
    async () => {
      vi.useFakeTimers();
      let reads = 0;
      routeFetch({
        "/api/save/": () => jsonOk({ ok: true }),
        "/api/publish": () => Promise.reject(new TypeError("Failed to fetch")),
        "/api/listings/": () => { reads += 1; return jsonOk({}); },
      });

      const settled = publishListing("l1", {}, null, "draft").catch((e) => e);
      await vi.advanceTimersByTimeAsync(60000);

      expect((await settled).unknownOutcome).toBe(true);
      expect(reads).toBe(0);
    });

  it("carries the flag the queues count it apart by", async () => {
    vi.useFakeTimers();
    routeFetch({
      "/api/save/": () => jsonOk({ ok: true }),
      "/api/publish": () => Promise.reject(new TypeError("Failed to fetch")),
      "/api/listings/": () => jsonOk({ status: "draft", listing: {} }),
    });

    const settled = publishListing("l1", {}, null).catch((e) => e);
    await vi.advanceTimersByTimeAsync(60000);

    expect((await settled).unknownOutcome).toBe(true);
    expect(UNCONFIRMED_PUBLISH).toMatch(/check your store/i);
  });
});

describe("a publish eBay actually refused", () => {
  it("stays a refusal — no waiting, no second-guessing the record",
    async () => {
      let reads = 0;
      routeFetch({
        "/api/save/": () => jsonOk({ ok: true }),
        "/api/publish": () => jsonErr(400, "Add a package weight."),
        "/api/listings/": () => { reads += 1; return jsonOk({}); },
      });

      const err = await publishListing("l1", {}, null).catch((e) => e);

      expect(err.message).toBe("Add a package weight.");
      expect(err.unknownOutcome).toBeUndefined();
      expect(reads).toBe(0);
    });
});
