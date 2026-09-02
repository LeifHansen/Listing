/**
 * When rebuilding the eBay mirror is worth its quota, and when it is not.
 *
 * The mirror is DURABLE — it lives in the database — so showing the seller
 * their store costs nothing. Rebuilding it costs one eBay GetItem per
 * listing, and the app rebuilt on every session: a second tab, a phone, a
 * reload and a redeploy each spent up to 2,500 calls against a default
 * allowance of 5,000 a DAY, plus a concurrent forced status sweep. None of it
 * was asked for, and a seller who simply kept the app open could exhaust the
 * day's quota without ever pressing anything.
 *
 * So an automatic rebuild now runs only when skipping it would show the seller
 * nothing or something stale. "Sync with eBay" is unaffected — that is someone
 * asking.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { autoSyncDue, markAutoSynced } from "./store.jsx";

const USER = "user-1";
const HOUR = 60 * 60 * 1000;

beforeEach(() => {
  localStorage.clear();
});

describe("an automatic mirror rebuild", () => {
  it("runs on the first load after connecting", () => {
    // Without this the seller opens the app to an empty store. It is the one
    // automatic rebuild that earns its cost.
    expect(autoSyncDue(USER)).toBe(true);
  });

  it("does not run again on the next tab, reload or phone", () => {
    markAutoSynced(USER);
    expect(autoSyncDue(USER)).toBe(false);
  });

  it("runs again once the mirror is properly stale", () => {
    const now = Date.now();
    markAutoSynced(USER, now - 7 * HOUR);
    expect(autoSyncDue(USER, now)).toBe(true);
  });

  it("still skips a mirror synced an hour ago", () => {
    const now = Date.now();
    markAutoSynced(USER, now - HOUR);
    expect(autoSyncDue(USER, now)).toBe(false);
  });
});

describe("whose mirror it is", () => {
  it("is tracked per user, so a different account still gets its first run", () => {
    markAutoSynced(USER);
    expect(autoSyncDue("someone-else")).toBe(true);
    expect(autoSyncDue(USER)).toBe(false);
  });
});

describe("when the record cannot be trusted", () => {
  it("treats a corrupt timestamp as due", () => {
    localStorage.setItem(`thryft-last-store-sync:${USER}`, "not-a-number");
    expect(autoSyncDue(USER)).toBe(true);
  });

  it("honours a record written under the pre-rename key", () => {
    // The storage keys were renamed from `quickflip-*` to `thryft-*`. If the
    // read did not fall back, every existing seller would look like a first
    // load on the release -- and a first load is the one case that spends a
    // full store rebuild, up to 2,500 GetItem calls, unasked, on every
    // account at once.
    localStorage.setItem(`quickflip-last-store-sync:${USER}`, String(Date.now()));
    expect(autoSyncDue(USER)).toBe(false);
  });

  it("treats a future timestamp as due", () => {
    const now = Date.now();
    markAutoSynced(USER, now + 100 * HOUR);
    expect(autoSyncDue(USER, now)).toBe(true);
  });

  it("still syncs when storage is unavailable", () => {
    // Private mode, blocked site data. Erring toward "due" keeps the app
    // usable; erring the other way would leave it permanently empty.
    const spy = vi.spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => { throw new Error("blocked"); });
    try {
      expect(autoSyncDue(USER)).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });

  it("does not throw when storage rejects a write", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => { throw new Error("blocked"); });
    try {
      expect(() => markAutoSynced(USER)).not.toThrow();
    } finally {
      spy.mockRestore();
    }
  });
});
