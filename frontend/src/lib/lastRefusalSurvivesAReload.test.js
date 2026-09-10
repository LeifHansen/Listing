/* A refused draft still says why once the page has been reloaded.
 *
 * A seller published a draft, watched it stay in Drafts, and reported the app
 * as broken: "I published it and it's not clearing." The publish had been
 * REFUSED -- theirs to fix -- and the app said so in a toast that is gone in
 * seconds and in a React state map that is gone on reload. What was left was
 * a card indistinguishable from one nobody had ever tried to publish.
 *
 * The server now records the reason on the listing (Listing.publish_error);
 * this is the rule the card reads it back by. */
import { describe, it, expect } from "vitest";
import { lastRefusal } from "./listingsView";

const draft = (publish_error = "") => ({ id: "a", listing: { publish_error } });

describe("lastRefusal", () => {
  it("reads the reason the server recorded, which is what survives a reload", () => {
    expect(lastRefusal(draft("Add a shipping weight."))).toBe(
      "Add a shipping weight.");
  });

  it("prefers this page's own verdict over the recorded one", () => {
    expect(lastRefusal(draft("An older refusal."), { local: "eBay just said no." }))
      .toBe("eBay just said no.");
  });

  it("says nothing while a retry is in flight", () => {
    expect(lastRefusal(draft("Add a shipping weight."), { inFlight: true })).toBe("");
    expect(lastRefusal(draft(""), { local: "no", inFlight: true })).toBe("");
  });

  it("is empty for a draft no publish has ever been refused on", () => {
    expect(lastRefusal(draft())).toBe("");
    expect(lastRefusal({ id: "a" })).toBe("");
    expect(lastRefusal(undefined)).toBe("");
  });
});
