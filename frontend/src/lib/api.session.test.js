/* A session that ended has to end the app's idea of it too.
 *
 * This branch made sessions revocable — a seller can press "Sign out
 * everywhere", and a stolen token can be cancelled. What it did not do is
 * teach the app what a 401 means. Nothing in the client reads the status: the
 * cached `user` stays on screen, the top bar still shows the account, and
 * every fetch behind it fails with "Log in first." rendered as an error on
 * whichever card asked.
 *
 * So the seller who pressed the button on their phone sees, on the laptop,
 * their own email above a store that will not load, with no prompt to sign in
 * and nothing saying why. The revocation worked; the app just never noticed.
 *
 * The signal is an event rather than a store import, for the same reason the
 * out-of-tokens 402 already uses one: this module is the choke point every
 * request goes through, and it must not depend on React.
 *
 * The auth endpoints are deliberately exempt. A 401 from /api/auth/login is a
 * wrong password — the form says so — and treating it as an expiry would sign
 * the seller out of the session they are in the middle of starting.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";

function reply(status, detail) {
  return Promise.resolve({
    ok: status < 400,
    status,
    statusText: "",
    json: () => Promise.resolve(detail ? { detail } : {}),
  });
}

let seen;

beforeEach(() => {
  seen = [];
  globalThis.addEventListener("auth:expired", (e) => seen.push(e.detail));
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  seen = [];
});

describe("a request refused because the session is gone", () => {
  it("says so once, to whoever is listening", async () => {
    fetch.mockReturnValue(reply(401, "Log in first."));
    await expect(api("/api/listings")).rejects.toThrow(/log in first/i);
    expect(seen.length).toBe(1);
  });

  it("still throws, so the caller's own error path runs", async () => {
    fetch.mockReturnValue(reply(401, "Log in first."));
    await expect(api("/api/tokens")).rejects.toMatchObject({ status: 401 });
  });

  it("does not fire for a wrong password", async () => {
    // The login form owns that message. Signing the seller out of a session
    // they are in the middle of starting would be its own bug.
    fetch.mockReturnValue(reply(401, "Invalid email or password"));
    await expect(api("/api/auth/login", { method: "POST" })).rejects.toThrow();
    expect(seen).toEqual([]);
  });

  it("does not fire when a password is re-checked mid-session", async () => {
    // Deleting an account asks for the password again, and answers 401 when
    // it does not match. Treating that as an expired session signed the
    // seller out and closed the dialog they were standing in — which is what
    // the delete-account browser journey caught the first time this landed.
    fetch.mockReturnValue(reply(401, "That password doesn't match. Try again."));
    await expect(api("/api/account/delete", { method: "POST" })).rejects.toThrow();
    expect(seen).toEqual([]);
  });

  it("does not fire for any other refusal", async () => {
    // 403 is "not yours", 404 is "not there". Neither means the session died,
    // and signing someone out over one would lose their work.
    for (const status of [400, 403, 404, 500, 503]) {
      fetch.mockReturnValue(reply(status, "nope"));
      await expect(api("/api/listings")).rejects.toThrow();
    }
    expect(seen).toEqual([]);
  });
});
