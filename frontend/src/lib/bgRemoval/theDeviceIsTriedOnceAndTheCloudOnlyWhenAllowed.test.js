/* The background removal service: the device first, the cloud at most once.
 *
 * Every photo goes to this device's own engine (here the iPhone's, through
 * the app's native plugin) and its matte is saved through /api/cutout/apply
 * -- free, nothing sent anywhere. Photoroom is a fallback, and a guarded
 * one: only when the caller says the seller allowed it, only after the
 * device actually failed, and never more than once per photo. Nothing here
 * may loop: the one retry is re-reading a photo that changed between the
 * read and the save.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/platform", async (importOriginal) => ({
  ...(await importOriginal()),
  nativePlatform: () => "ios",
}));

import {
  checkLocal, configureLocal, cutPhoto, getSnapshot, makeMask, previewCut,
  resetLocal,
} from "./service";
import { LocalCutoutError } from "./errors";

const SHA = "a".repeat(64);
let calls;
let applyStatus;
let plugin;

function respond(body, { status = 200, headers = {} } = {}) {
  return Promise.resolve({
    ok: status < 400,
    status,
    statusText: "",
    headers: { get: (k) => headers[k.toLowerCase()] ?? (k === "content-type" ? "application/json" : null) },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
    blob: () => Promise.resolve(new Blob([new Uint8Array([1, 2, 3])], { type: "image/jpeg" })),
  });
}

beforeEach(() => {
  calls = [];
  applyStatus = [200];
  plugin = {
    isSupported: vi.fn(async () => ({ supported: true, ready: true })),
    prepare: vi.fn(async () => ({ supported: true, ready: true })),
    segment: vi.fn(async () => ({ mask: btoa("png"), width: 4, height: 3 })),
  };
  window.Capacitor = { Plugins: { ThryftCutout: plugin } };
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const u = String(url);
    calls.push({ url: u, method: opts.method || "GET", body: opts.body });
    if (u.includes("/api/image/file/")) {
      return respond({}, { headers: { "x-source-sha256": SHA, "x-photo-kind": "" } });
    }
    if (u.includes("/api/cutout/apply")) {
      const status = applyStatus.length > 1 ? applyStatus.shift() : applyStatus[0];
      return status === 200
        ? respond({ ok: true, version: 1, updated_at: "now" })
        : respond({ detail: "This photo changed since its cutout was made" }, { status });
    }
    if (u.includes("/api/cutout/compose")) return respond({ ok: true, image: "data:x", engine: "device" });
    if (u.includes("/api/cutout/cloud")) return respond({ ok: true, version: 2, updated_at: "later" });
    if (u.includes("/api/image/remove-bg")) return respond({ ok: true, image: "data:cloud" });
    return respond({});
  }));
  resetLocal();
  configureLocal({ providers: ["ios"], timeout_ms: 200 });
});

afterEach(() => {
  resetLocal();
  delete window.Capacitor;
  vi.unstubAllGlobals();
});

const hits = (part) => calls.filter((c) => c.url.includes(part));

describe("the device first", () => {
  it("cuts on the device and saves the matte, with nothing sent to the cloud", async () => {
    const out = await cutPhoto({ sessionId: "s1", name: "img_000.jpg", allowCloud: true });
    expect(out.provider).toBe("apple-vision");
    expect(plugin.segment).toHaveBeenCalledTimes(1);
    expect(hits("/api/cutout/apply")).toHaveLength(1);
    const form = hits("/api/cutout/apply")[0].body;
    expect(form.get("source_sha")).toBe(SHA);
    expect(form.get("provider")).toBe("apple-vision");
    expect(form.get("origin")).toBe("queue");
    expect(hits("/api/cutout/cloud")).toHaveLength(0);
  });

  it("reads the photo as shot, never the composite", async () => {
    await cutPhoto({ sessionId: "s1", name: "img_000.jpg" });
    expect(hits("/api/image/file/")[0].url).toContain("which=source");
  });

  it("is off until the server switches the engine on for this platform", async () => {
    configureLocal({ providers: ["android", "web"], timeout_ms: 200 });
    await checkLocal({ force: true });
    expect(getSnapshot().status).toBe("off");
    await expect(cutPhoto({ sessionId: "s1", name: "img_000.jpg" }))
      .rejects.toBeInstanceOf(LocalCutoutError);
    expect(plugin.segment).not.toHaveBeenCalled();
  });
});

describe("the cloud only when allowed, and only once", () => {
  it("never reaches Photoroom for a seller who has not allowed it", async () => {
    plugin.segment.mockRejectedValue(Object.assign(new Error("x"), { code: "inference_failed" }));
    await expect(cutPhoto({ sessionId: "s1", name: "img_000.jpg", allowCloud: false }))
      .rejects.toMatchObject({ code: "inference_failed" });
    expect(hits("/api/cutout/cloud")).toHaveLength(0);
    expect(hits("/api/image/remove-bg")).toHaveLength(0);
  });

  it("falls back once, after one device attempt, when allowed", async () => {
    plugin.segment.mockRejectedValue(Object.assign(new Error("x"), { code: "inference_failed" }));
    const out = await cutPhoto({ sessionId: "s1", name: "img_000.jpg", allowCloud: true });
    expect(out.provider).toBe("photoroom");
    expect(plugin.segment).toHaveBeenCalledTimes(1);
    expect(hits("/api/cutout/cloud")).toHaveLength(1);
    expect(hits("/api/cutout/cloud")[0].body.get("source_sha")).toBe(SHA);
  });

  it("treats a matte the server refused as the device failing", async () => {
    applyStatus = [422];
    const out = await cutPhoto({ sessionId: "s1", name: "img_000.jpg", allowCloud: true });
    expect(out.provider).toBe("photoroom");
    expect(hits("/api/cutout/apply")).toHaveLength(1);
    expect(hits("/api/cutout/cloud")).toHaveLength(1);
  });

  it("gives up on a device that took too long", async () => {
    plugin.segment.mockImplementation(() => new Promise(() => {}));
    await expect(cutPhoto({ sessionId: "s1", name: "img_000.jpg" }))
      .rejects.toMatchObject({ code: "timeout" });
  });

  it("goes nowhere once the seller stopped it", async () => {
    plugin.segment.mockImplementation(() => new Promise(() => {}));
    const stop = new AbortController();
    const run = cutPhoto({ sessionId: "s1", name: "img_000.jpg", allowCloud: true,
                           signal: stop.signal });
    await new Promise((r) => setTimeout(r, 5));
    stop.abort();
    await expect(run).rejects.toMatchObject({ code: "cancelled" });
    expect(hits("/api/cutout/cloud")).toHaveLength(0);
  });
});

describe("no loops", () => {
  it("re-reads a photo that changed once, and only once", async () => {
    applyStatus = [409, 200];
    await cutPhoto({ sessionId: "s1", name: "img_000.jpg" });
    expect(hits("/api/image/file/")).toHaveLength(2);
    expect(hits("/api/cutout/apply")).toHaveLength(2);

    calls = [];
    applyStatus = [409];
    await expect(cutPhoto({ sessionId: "s1", name: "img_000.jpg", allowCloud: true }))
      .rejects.toMatchObject({ status: 409 });
    expect(hits("/api/cutout/apply")).toHaveLength(2);
    expect(hits("/api/cutout/cloud")).toHaveLength(0);
  });

  it("cuts the same bytes once, however often it is asked", async () => {
    let release;
    plugin.segment.mockImplementation(() => new Promise((r) => {
      release = () => r({ mask: btoa("png"), width: 4, height: 3 });
    }));
    const blob = new Blob([new Uint8Array([9])]);
    const a = makeMask(blob, { sourceSha: SHA });
    const b = makeMask(blob, { sourceSha: SHA });
    await new Promise((r) => setTimeout(r, 5));
    release();
    await Promise.all([a, b]);
    const c = await makeMask(blob, { sourceSha: SHA });
    expect(plugin.segment).toHaveBeenCalledTimes(1);
    expect(c.cached).toBe(true);
  });
});

describe("the studio's preview", () => {
  it("cuts the stored photo from its source and keeps the matte for later", async () => {
    const out = await previewCut({ sessionId: "s1", name: "img_000.jpg" });
    expect(out.provider).toBe("apple-vision");
    expect(out.fromSource).toBe(true);
    expect(out.sourceSha).toBe(SHA);
    expect(out.mask).toBeInstanceOf(Blob);
    const form = hits("/api/cutout/compose")[0].body;
    expect(form.get("session_id")).toBe("s1");
    expect(form.get("file")).toBeNull();
  });

  it("cuts an edited canvas as it stands", async () => {
    const canvas = new Blob([new Uint8Array([7])], { type: "image/jpeg" });
    const out = await previewCut({ sessionId: "s1", name: "img_000.jpg", blob: canvas });
    expect(out.fromSource).toBe(false);
    expect(hits("/api/image/file/")).toHaveLength(0);
    expect(hits("/api/cutout/compose")[0].body.get("file")).not.toBeNull();
  });

  it("uses the cloud preview only when allowed", async () => {
    plugin.segment.mockRejectedValue(Object.assign(new Error("x"), { code: "nothing_found" }));
    await expect(previewCut({ sessionId: "s1", name: "img_000.jpg" }))
      .rejects.toBeInstanceOf(LocalCutoutError);
    expect(hits("/api/image/remove-bg")).toHaveLength(0);
    const out = await previewCut({ sessionId: "s1", name: "img_000.jpg", allowCloud: true });
    expect(out.cloud).toBe(true);
    expect(hits("/api/image/remove-bg")).toHaveLength(1);
  });
});
