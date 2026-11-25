/* An engine that has downloaded its model is ready -- for every photo after.
 *
 * checkLocal() remembers its answer, so a pile of photos asks the device
 * about itself once. But it remembered the SNAPSHOT from when it asked:
 * "needs-download". After the model came down every later photo read that
 * stale answer, went through prepareLocal() again -- the native prepare()
 * once more per photo -- and flashed the shared status to "downloading" and
 * back, re-rendering every screen that shows it, per photo.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/platform", async (importOriginal) => ({
  ...(await importOriginal()),
  nativePlatform: () => "android",
}));

import { configureLocal, getSnapshot, makeMask, resetLocal, subscribe } from "./service";

let plugin;
let statuses;
let unsubscribe;

beforeEach(() => {
  plugin = {
    // Play services has not fetched the model yet; prepare() fetches it.
    isSupported: vi.fn(async () => ({ supported: true, ready: false,
                                      reason: "model_not_downloaded" })),
    prepare: vi.fn(async () => ({ supported: true, ready: true })),
    segment: vi.fn(async () => ({ mask: btoa("png"), width: 4, height: 3 })),
  };
  window.Capacitor = { Plugins: { ThryftCutout: plugin } };
  resetLocal();
  configureLocal({ providers: ["android"], timeout_ms: 500 });
  statuses = [];
  unsubscribe = subscribe(() => statuses.push(getSnapshot().status));
});

afterEach(() => {
  unsubscribe();
  resetLocal();
  delete window.Capacitor;
});

describe("the model, once downloaded", () => {
  it("is not prepared again for the next photo", async () => {
    await makeMask(new Blob(["one"]));
    expect(plugin.prepare).toHaveBeenCalledTimes(1);
    expect(getSnapshot().status).toBe("ready");

    statuses.length = 0;
    await makeMask(new Blob(["two"]));
    await makeMask(new Blob(["three"]));
    expect(plugin.prepare).toHaveBeenCalledTimes(1);
    expect(statuses).not.toContain("downloading");
    expect(plugin.segment).toHaveBeenCalledTimes(3);
  });
});
