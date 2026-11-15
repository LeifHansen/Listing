/* Waiting on a background job survives a status read that failed.
 *
 * End & relist, Lower prices, Send offers, Enrich all and the crosspost all
 * run on the server and are watched through /api/bulk/status. One failed read
 * of that status — a phone switching networks, the machine restarting for a
 * deploy — used to end the wait with an error while the job ran on.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { pollJob } from "./api";

function reply(status, body) {
  return Promise.resolve(new Response(JSON.stringify(body), {
    status, headers: { "content-type": "application/json" },
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("polling a job", () => {
  it("rides out a dropped connection and a 503 and returns the result", async () => {
    const answers = [
      () => Promise.reject(new TypeError("Failed to fetch")),
      () => reply(503, { detail: "starting up" }),
      () => reply(200, { done: false, phase: "working" }),
      () => reply(200, { done: true, result: { ok: 1 } }),
    ];
    const fetchMock = vi.fn(() => answers.shift()());
    vi.stubGlobal("fetch", fetchMock);
    await expect(pollJob("j1", { intervalMs: 1 })).resolves.toEqual({ ok: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("stops at once on an answer the server meant, like an unknown job", async () => {
    const fetchMock = vi.fn(() => reply(404, { detail: "No such job." }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(pollJob("gone", { intervalMs: 1 })).rejects.toThrow("No such job.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("still gives up when the server stays unreachable past the deadline", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(pollJob("j2", { intervalMs: 1, timeoutMs: 20 })).rejects.toThrow();
  });

  it("reports a job that itself failed", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply(200, { done: true, error: "eBay said no." })));
    await expect(pollJob("j3", { intervalMs: 1 })).rejects.toThrow("eBay said no.");
  });
});
