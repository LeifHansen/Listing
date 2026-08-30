/**
 * A green tick is a claim.
 *
 * "Live mirror of your eBay store" — with a check mark, titled "Everything
 * below reflects your actual eBay store" — was shown whenever a sync was not
 * actively running and had not errored. `/api/ebay/sync-listings` answers with
 * `partial` for exactly the case where that is false, and nothing read it.
 *
 * `partial` is set for two reasons and both matter: the sweep SAMPLES (one
 * eBay call per listing, so a big store is checked a hundred at a time), and
 * the list it samples is itself a capped read, so on a store bigger than one
 * page the oldest live listings never reach the sweep at all.
 */
import { describe, expect, it } from "vitest";

import { storeMirrorView } from "./storeMirror.js";

const USER = { id: "u1" };

describe("what the store-mirror line may claim", () => {
  it("claims a live mirror when the sync really covered the store", () => {
    const v = storeMirrorView({ user: USER, connected: true });
    expect(v.kind).toBe("mirror");
    expect(v.text).toMatch(/live mirror/i);
  });

  it("does not claim a live mirror when the sync said it was partial", () => {
    const v = storeMirrorView({ user: USER, connected: true, partial: true });
    expect(v.kind).toBe("partial");
    expect(v.text.toLowerCase()).not.toContain("live mirror");
    expect(v.title.toLowerCase()).not.toContain("everything below reflects");
  });

  it("does not turn a partial sync into an error", () => {
    // The records on screen are real. Only the certainty is gone.
    const v = storeMirrorView({ user: USER, connected: true, partial: true });
    expect(v.kind).not.toBe("error");
    expect(v.title.toLowerCase()).toContain("real");
  });

  it("still prefers a real failure over the partial wording", () => {
    expect(storeMirrorView({ user: USER, connected: true, partial: true,
                             error: "boom" }).kind).toBe("error");
  });

  it("still prefers the live spinner over both", () => {
    expect(storeMirrorView({ user: USER, connected: true, partial: true,
                             syncing: true }).kind).toBe("syncing");
  });

  it("says nothing at all without a user or a connection", () => {
    expect(storeMirrorView({ user: null, connected: true }).kind).toBe("hidden");
    expect(storeMirrorView({ user: USER, connected: false }).kind)
      .toBe("not-connected");
  });
});
