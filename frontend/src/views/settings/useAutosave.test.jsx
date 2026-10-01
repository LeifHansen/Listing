/* The status beside a control that saves itself.
 *
 * idle → saving → saved → idle after two seconds, or error with the reason.
 * The one subtlety is ordering: a slow answer to the FIRST change must not
 * overwrite the status of a second change that already landed. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAutosave } from "./useAutosave";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function harness(save) {
  const seen = { status: null, run: null };
  function Probe() {
    const { status, run } = useAutosave(save);
    seen.status = status;
    seen.run = run;
    return <span data-testid="s">{status.kind}{status.message ? `:${status.message}` : ""}</span>;
  }
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  return { seen, root, host, mount: () => act(async () => { root.render(<Probe />); }) };
}

describe("useAutosave", () => {
  afterEach(() => { vi.useRealTimers(); document.body.innerHTML = ""; });

  it("goes idle → saving → saved → idle", async () => {
    vi.useFakeTimers();
    let resolve;
    const save = () => new Promise((r) => { resolve = r; });
    const h = harness(save);
    await h.mount();
    expect(h.seen.status.kind).toBe("idle");

    let p;
    await act(async () => { p = h.seen.run(1); });
    expect(h.seen.status.kind).toBe("saving");
    await act(async () => { resolve({ ok: true }); await p; });
    expect(h.seen.status.kind).toBe("saved");
    await act(async () => { vi.advanceTimersByTime(2100); });
    expect(h.seen.status.kind).toBe("idle");
    await act(async () => { h.root.unmount(); });
  });

  it("carries the reason when the save fails, and rethrows", async () => {
    const save = () => Promise.reject(new Error("eBay said no"));
    const h = harness(save);
    await h.mount();
    let threw = false;
    await act(async () => { await h.seen.run().catch(() => { threw = true; }); });
    expect(threw).toBe(true);
    expect(h.seen.status).toEqual({ kind: "error", message: "eBay said no" });
    await act(async () => { h.root.unmount(); });
  });

  it("lets a newer change win over a slower older one", async () => {
    const pending = [];
    const save = () => new Promise((resolve, reject) => pending.push({ resolve, reject }));
    const h = harness(save);
    await h.mount();
    let first; let second;
    await act(async () => { first = h.seen.run("a"); second = h.seen.run("b"); });
    // The second answer lands first.
    await act(async () => { pending[1].resolve(); await second; });
    expect(h.seen.status.kind).toBe("saved");
    // The first then fails -- and must not turn a saved second into an error.
    await act(async () => { pending[0].reject(new Error("late")); await first.catch(() => {}); });
    expect(h.seen.status.kind).toBe("saved");
    await act(async () => { h.root.unmount(); });
  });
});
