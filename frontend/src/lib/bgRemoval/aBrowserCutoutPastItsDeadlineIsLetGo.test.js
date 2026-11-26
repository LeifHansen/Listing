/* A browser cutout that runs past its deadline is let go, not left running.
 *
 * The service stops WAITING for a device cutout after its deadline, but the
 * run itself went on in the worker. The queue then handed the worker the
 * next photo, which waited behind the abandoned run -- on WebAssembly the
 * worker does one at a time -- with its own deadline already counting, so it
 * timed out too, and so did the one after: each photo marked given-up (or
 * sent to Photoroom, spending the month's budget) while the machine was busy
 * on photos nobody was waiting for. And a run that slow on WebAssembly is
 * the "too slow" the capability check means, but it was only remembered for
 * the next visit.
 *
 * Now the worker is let go at the deadline, a WebAssembly run that missed it
 * marks this machine too slow at once, and the device is asked about itself
 * again before the next photo.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/platform", async (importOriginal) => ({
  ...(await importOriginal()),
  nativePlatform: () => "web",
}));

// The capability check, stood in for: a desktop without WebGPU, which the
// real check sends to WebAssembly -- until it is remembered as too slow.
const machine = vi.hoisted(() => ({ tooSlow: false }));
vi.mock("./web/capabilities", () => ({
  webCapability: async () => (machine.tooSlow
    ? { supported: false, reason: "too_slow" }
    : { supported: true, backend: "wasm", fp16: false, reason: "" }),
  rememberTooSlow: () => { machine.tooSlow = true; },
  rememberedTooSlow: () => machine.tooSlow,
}));

import { configureLocal, makeMask, resetLocal } from "./service";

let workers;

class StalledWorker {
  constructor() {
    this.posted = [];
    this.terminated = false;
    workers.push(this);
  }
  postMessage(msg) {
    this.posted.push(msg.type);
    // The model loads; a photo is never answered.
    if (msg.type === "load") queueMicrotask(() => this.onmessage?.({ data: { type: "loaded" } }));
  }
  terminate() { this.terminated = true; }
}

beforeEach(() => {
  workers = [];
  machine.tooSlow = false;
  vi.stubGlobal("Worker", StalledWorker);
  resetLocal();
  configureLocal({ providers: ["web"], timeout_ms: 30,
                   web_model: { url: "https://models.example/birefnet.onnx", sha256: "a".repeat(64) } });
});
afterEach(() => {
  resetLocal();
  vi.unstubAllGlobals();
});

describe("a WebAssembly cutout that misses its deadline", () => {
  it("lets the worker go, and does not queue the next photo behind it", async () => {
    await expect(makeMask(new Blob(["one"]))).rejects.toMatchObject({ code: "timeout" });
    expect(workers).toHaveLength(1);
    expect(workers[0].terminated).toBe(true);

    // The next photo is not handed to a worker still busy on the last one:
    // the machine has shown it is too slow, and says so.
    await expect(makeMask(new Blob(["two"]))).rejects.toMatchObject({ code: "too_slow" });
    expect(workers[0].posted.filter((t) => t === "segment")).toHaveLength(1);
  });
});
