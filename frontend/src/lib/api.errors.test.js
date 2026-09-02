/* A seller-facing message should not have an HTTP status stapled to it.
 *
 * `api()` built every error as `(${res.status}) ${detail}`. That was the right
 * shape when `detail` was a raw exception string and the status was the only
 * information in it. It is the wrong shape now: P2-07 went through the backend
 * turning those into sentences written for the seller, and the client was
 * still prefixing each one with a number the seller cannot act on.
 *
 * The result was visible on the most ordinary failure there is. A store read
 * that could not run rendered as:
 *
 *   We couldn’t load your listings ((503) We couldn’t load your listings just
 *   now.). This doesn’t mean you don’t have any — try again in a moment.
 *
 * — a sentence inside a sentence, the same one twice, wrapped around a status
 * code. Exactly the shape P2-07 exists to remove, arrived at from the other
 * end: the server's copy got better and the client kept dressing it up.
 *
 * So: when the server sent a `detail`, that IS the message. The status stays
 * on `err.status`, where the code that branches on it already looks, and the
 * `(status) statusText` form remains for a body that carried no detail — a
 * proxy's HTML error page, a gateway timeout — where the number really is all
 * there is.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

function reply(status, body, { json = true } = {}) {
  return Promise.resolve({
    ok: status < 400,
    status,
    statusText: status === 503 ? "Service Unavailable" : "Error",
    headers: { get: () => "application/json" },
    json: () => (json ? Promise.resolve(body) : Promise.reject(new Error("not json"))),
    text: () => Promise.resolve(String(body)),
  });
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("the error a failed request throws", () => {
  it("is the sentence the server wrote, with nothing added", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply(503, {
      detail: "We couldn’t load your listings just now — this doesn’t mean you don’t have any. Try again in a moment.",
    })));
    await expect(api("/api/listings")).rejects.toThrow(
      "We couldn’t load your listings just now — this doesn’t mean you don’t have any. Try again in a moment.");
  });

  it("does not carry the status code into the message", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply(503, { detail: "Try again in a moment." })));
    const err = await api("/api/listings").catch((e) => e);
    expect(err.message).not.toMatch(/50\d/);
    expect(err.message).not.toMatch(/[()]/);
  });

  it("still carries the status for the code that branches on it", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply(503, { detail: "Try again in a moment." })));
    const err = await api("/api/listings").catch((e) => e);
    expect(err.status).toBe(503);
  });

  it("falls back to the status when the body says nothing", async () => {
    // A proxy's HTML error page, a gateway timeout: here the number really is
    // all there is, and dropping it would leave the seller with "Error".
    vi.stubGlobal("fetch", vi.fn(() => reply(502, "<html>bad gateway</html>",
                                             { json: false })));
    const err = await api("/api/listings").catch((e) => e);
    expect(err.message).toContain("502");
  });

  it("still opens the buy-tokens dialog on a 402 about tokens", async () => {
    // The one place the message is inspected rather than shown.
    const seen = [];
    const listener = (e) => seen.push(e.detail);
    window.addEventListener("tokens:needed", listener);
    vi.stubGlobal("fetch", vi.fn(() => reply(402, {
      detail: "You're out of AI tokens — top up to keep going.",
    })));
    await api("/api/tokens/history").catch(() => {});
    window.removeEventListener("tokens:needed", listener);
    expect(seen).toHaveLength(1);
  });
});
