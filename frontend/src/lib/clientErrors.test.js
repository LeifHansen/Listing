/* The reporter must never become the thing being reported.
 *
 * A crash handler that posts to the server sits one mistake away from a loop:
 * report fails → failure is logged → the log handler reports → and the page
 * spends its remaining life talking to /api/client-errors at render speed.
 * Four guards stop that, and each is cheap to remove by accident, so each is
 * pinned here.
 *
 * It also deliberately does not use lib/api. That wrapper throws on failure
 * and dispatches `auth:expired` on a 401 and `tokens:needed` on a 402 — any of
 * which, reached from inside an error handler, re-enters the app while it is
 * already failing.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  _resetForTests, installClientErrorReporting, noteRequestId,
  reportClientError,
} from "@/lib/clientErrors";

let posts;

beforeEach(() => {
  _resetForTests();
  posts = [];
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    posts.push({ url, body: JSON.parse(opts.body) });
    return Promise.resolve({ ok: true, status: 202 });
  }));
});

afterEach(() => { vi.unstubAllGlobals(); });

describe("what it sends", () => {
  it("posts the crash with the screen and the build it came from", () => {
    noteRequestId("a1b2c3d4");
    reportClientError("react", new TypeError("cannot read title"),
                      { componentStack: "\n    at ListingCard" });

    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain("/api/client-errors");
    expect(posts[0].body.message).toBe("cannot read title");
    expect(posts[0].body.name).toBe("TypeError");
    expect(posts[0].body.component_stack).toContain("ListingCard");
    expect(posts[0].body.request_id).toBe("a1b2c3d4");
  });

  it("never rides on lib/api's wrapper", () => {
    // credentials omitted and keepalive set is the signature of the bare
    // fetch; api() sends neither.
    reportClientError("react", new Error("x"));
    expect(fetch.mock.calls[0][1]).toMatchObject({
      keepalive: true, credentials: "omit",
    });
  });
});

describe("the guards", () => {
  it("caps how many reports one page load can send", () => {
    for (let i = 0; i < 50; i += 1) {
      reportClientError("react", new Error(`distinct crash ${i}`));
    }
    expect(posts.length).toBeLessThanOrEqual(5);
  });

  it("sends one report per distinct crash, however often it repeats", () => {
    const err = new Error("the same one");
    err.stack = "Error: the same one\n    at Card (index.js:1:2)";
    for (let i = 0; i < 20; i += 1) reportClientError("react", err);

    expect(posts).toHaveLength(1);
  });

  it("refuses to report a failure that came from itself", () => {
    const err = new Error("boom");
    err.stack = "Error: boom\n    at fetch (/api/client-errors:1:1)";

    expect(reportClientError("react", err)).toBe(false);
    expect(posts).toHaveLength(0);
  });

  it("stays silent when the report itself fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
    // The assertion is that nothing throws and nothing is retried: an
    // unhandled rejection here would be caught by the very handler this
    // module installs, which is the loop.
    expect(() => reportClientError("react", new Error("x"))).not.toThrow();
    await Promise.resolve();
  });

  it("does not throw when there is no error to report", () => {
    expect(() => reportClientError("react", undefined)).not.toThrow();
    expect(() => reportClientError("react", null)).not.toThrow();
  });
});

describe("installation", () => {
  it("reports an unhandled window error", () => {
    installClientErrorReporting();
    window.dispatchEvent(Object.assign(new Event("error"),
                                       { error: new Error("global boom") }));

    expect(posts).toHaveLength(1);
    expect(posts[0].body.kind).toBe("window.onerror");
  });

  it("installs only once, however many times it is called", () => {
    installClientErrorReporting();
    installClientErrorReporting();
    installClientErrorReporting();
    window.dispatchEvent(Object.assign(new Event("error"),
                                       { error: new Error("once please") }));

    expect(posts).toHaveLength(1);
  });
});
