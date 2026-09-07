/* "Remove background & replace with white" is the seller's call, every time.
 *
 * The checkbox used to open pre-ticked from whatever the seller last chose
 * (lib/photoPrefs, now gone), so one pile they wanted cut out turned it on
 * for every pile after it — months of uploads deciding on their own to
 * replace a background, spend a credit per photo, and hand back a photo that
 * is not the one that was taken. Reported as: don't have it toggled on by
 * default, I want the user to do this.
 *
 * So: the toggle opens off, the request says false unless it was ticked for
 * THIS pile, and a stored preference from before the change cannot tick it.
 * The single listing and the bulk pile post to different endpoints by
 * different code paths, so both are read off the wire.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

/** Every upload the app made, as {url, form}. */
function recordUploads() {
  const sent = [];
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    const u = String(url);
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

describe("background removal is opt-in", () => {
  it("opens unticked", async () => {
    await mountUploader();
    await pickPhotos();
    expect(cutoutToggle().checked).toBe(false);
  });

  it("stays unticked for a seller who used it on an earlier pile", async () => {
    // The preference the uploader used to seed itself from, in both the
    // current key and the pre-rename one lib/localPrefs still migrates.
    localStorage.setItem("thryft-remove-bg", "yes");
    localStorage.setItem("quickflip-remove-bg", "yes");
    await mountUploader();
    await pickPhotos();
    expect(cutoutToggle().checked).toBe(false);
  });

  it("sends the photos as shot when nobody ticked it", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();
    await press("Identify with AI");

    expect(sent[0].form.get("remove_bg")).toBe("false");
  });

  it("still cuts them out when the seller asks for this pile", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();
    await act(async () => { cutoutToggle().click(); });
    await press("Identify with AI");

    expect(sent[0].form.get("remove_bg")).toBe("true");
  });

  it("holds for the bulk pile, which posts somewhere else entirely", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos(2);
    // Bulk mode: the second toggle on the card once there are two photos.
    await act(async () => {
      host.querySelectorAll('input[type="checkbox"]')[1].click();
    });
    await press("Split 2 photos into listings");

    const batch = sent.find((s) => s.url.includes("/api/bulk/upload"));
    expect(batch, "no batch was sent").toBeTruthy();
    expect(batch.form.get("remove_bg")).toBe("false");
  });
});
