/* Backgrounds taken off ON THE DEVICE: offered without a switch, free, and
 * asked for in a way none of the cloud's gates can mistake for a paid cutout.
 *
 * The cloud (Photoroom) needs the seller to switch it on in Settings, because
 * that is where they agree to their photos being sent there. The device
 * needs nothing of the kind -- nothing leaves it, nothing is charged -- so
 * where this phone or browser can cut, the uploader offers the toggle to
 * everyone, says where the work happens, and asks for it as remove_bg=false
 * plus cutout=device: a request the server's consent, token and monthly
 * gates all read as "no paid cutout", and a server from before this existed
 * reads as no cutout at all.
 */
import { vi } from "vitest";

let device = { status: "ready", usable: true, ready: true, provider: "apple-vision",
               reason: "", progress: null };
vi.mock("@/lib/bgRemoval/useLocalCutout", () => ({
  useLocalCutout: () => device,
}));

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function ok(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

let root;
let host;
// What GET /api/bg-removal/status answers. Switched on unless a test says
// otherwise: most of these are about the toggle itself.
let bgStatus;
const BG_ON = { available: true, active: true, needs_activation: true, used: 0, limit: 1000 };

/** Every upload the app made, as {url, form}. */
function recordUploads() {
  const sent = [];
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    const u = String(url);
    if (u.includes("/api/bg-removal/status")) return ok(bgStatus);
    if (u.includes("/api/upload") || u.includes("/api/bulk/upload")) {
      sent.push({ url: u, form: opts.body });
      return ok({ session_id: "s1", job_id: "j1" });
    }
    if (u.includes("/api/bulk/status/")) {
      return ok({ id: "j1", done: true, phase: "done", items: [],
                  result: { listing: { images: [] }, confidence: "medium" } });
    }
    return ok({});
  }));
  return sent;
}

async function mountUploader() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <UploadPhase />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function pickPhotos(n = 1) {
  const input = host.querySelector('input[type="file"]');
  const files = Array.from({ length: n }, (_, i) =>
    new File([new Uint8Array([1, 2, 3])], `p${i}.jpg`, { type: "image/jpeg" }));
  Object.defineProperty(input, "files", { value: files, configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** The cutout toggle — first checkbox on the card, ahead of bulk mode. */
function cutoutToggle() {
  return [...host.querySelectorAll('input[type="checkbox"]')]
    .find((c) => c.closest("label")?.textContent.includes("Remove background"));
}

async function press(label) {
  const b = [...host.querySelectorAll("button")]
    .find((x) => x.textContent.includes(label));
  await act(async () => { b.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => {
  bgStatus = BG_ON;
  recordUploads();
  localStorage.clear();
  // lib/api refuses a photo-bearing call without the stored AI consent, and
  // these tests are about the body of that call.
  localStorage.setItem("thryft-ai-consent", "yes");
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
  // jsdom decodes nothing, and the pre-upload downscale waits on a decode.
  globalThis.createImageBitmap = vi.fn(async () => (
    { width: 10, height: 10, close() {} }));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});


describe("the device takes the backgrounds off", () => {
  it("is offered with no switch to turn on, and asked for as a device cutout", async () => {
    bgStatus = { available: true, active: false, needs_activation: true };
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();

    expect(cutoutToggle(), "the device's toggle was not offered").toBeTruthy();
    expect(cutoutToggle().checked).toBe(false);
    expect(host.textContent).toContain("Done on this device");
    await act(async () => { cutoutToggle().click(); });
    await press("Identify with AI");

    expect(sent[0].form.get("remove_bg")).toBe("false");
    expect(sent[0].form.get("cutout")).toBe("device");
  });

  it("is offered even where the server has no cloud at all", async () => {
    bgStatus = { available: false, active: false, needs_activation: true };
    await mountUploader();
    await pickPhotos();
    expect(cutoutToggle()).toBeTruthy();
  });

  it("says when the cloud will step in for what the device can't do", async () => {
    bgStatus = BG_ON;
    await mountUploader();
    await pickPhotos();
    expect(host.textContent).toContain("Photoroom steps in");
  });

  it("asks for the bulk pile the same way", async () => {
    bgStatus = { available: false, active: false, needs_activation: true };
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos(2);
    await act(async () => { cutoutToggle().click(); });
    await act(async () => {
      [...host.querySelectorAll('input[type="checkbox"]')]
        .find((c) => c.closest("label")?.textContent.includes("Bulk mode"))
        .click();
    });
    await press("Split 2 photos into listings");

    const batch = sent.find((s) => s.url.includes("/api/bulk/upload"));
    expect(batch, "no batch was sent").toBeTruthy();
    expect(batch.form.get("remove_bg")).toBe("false");
    expect(batch.form.get("cutout")).toBe("device");
  });

  it("sends nothing to cut when the toggle is left alone", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();
    await press("Identify with AI");
    expect(sent[0].form.get("remove_bg")).toBe("false");
    expect(sent[0].form.get("cutout")).toBeNull();
  });

  it("falls back to the cloud, as before, on a device that can't cut", async () => {
    device = { status: "unsupported", usable: false, ready: false, provider: "apple-vision",
               reason: "os_version", progress: null };
    bgStatus = BG_ON;
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();
    expect(host.textContent).not.toContain("Done on this device");
    await act(async () => { cutoutToggle().click(); });
    await press("Identify with AI");
    expect(sent[0].form.get("remove_bg")).toBe("true");
    expect(sent[0].form.get("cutout")).toBeNull();
    device = { status: "ready", usable: true, ready: true, provider: "apple-vision",
               reason: "", progress: null };
  });
});
