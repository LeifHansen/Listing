/**
 * Leaving the uploader gives its previews back.
 *
 * Every staged photo is shown through an object URL, and an object URL keeps
 * the photo in memory until it is revoked. Removing a photo revoked its URL
 * and so did the upload, but walking away from a staged pile — to Manage, to
 * a draft, anywhere — revoked nothing: the shoot stayed in memory for the
 * life of the tab.
 *
 * Two piles must NOT be revoked on the way out: one handed to a bulk upload
 * (the store owns it from then on, and hands it back if the upload fails),
 * and the one a failed batch has just handed back — which StrictMode, in
 * development, greets with a rehearsed unmount on every mount.
 */
import { StrictMode, act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function ok(body, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

// The List tab's arrangement, in miniature: the uploader is on screen until a
// batch takes over, which is what unmounts it mid-upload.
function ListTab() {
  const { activeBulk } = useApp();
  return activeBulk ? <p>batch screen</p> : <UploadPhase />;
}

async function mount({ strict = false } = {}) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const tree = (
    <ToastProvider><AppProvider><ListTab /></AppProvider></ToastProvider>
  );
  await act(async () => { root.render(strict ? <StrictMode>{tree}</StrictMode> : tree); });
  await tick();
}

const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 5)); });

async function pickPhotos(n) {
  const input = host.querySelector('input[type="file"]');
  const files = Array.from({ length: n }, (_, i) =>
    new File([new Uint8Array([1, 2, 3])], `p${i}.jpg`, { type: "image/jpeg" }));
  Object.defineProperty(input, "files", { value: files, configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

async function startBatch(n) {
  await act(async () => {
    [...host.querySelectorAll('input[type="checkbox"]')]
      .find((c) => c.closest("label")?.textContent.includes("Bulk mode"))
      .click();
  });
  await act(async () => {
    [...host.querySelectorAll("button")]
      .find((b) => b.textContent.includes(`Split ${n} photos into listings`))
      .click();
  });
  await tick();
}

const shown = () => [...host.querySelectorAll("img")]
  .map((img) => img.getAttribute("src"))
  .filter((s) => s && s.startsWith("blob:"));

beforeEach(() => {
  localStorage.clear();
  // A stored AI consent, or the upload is refused before it reaches fetch.
  localStorage.setItem("thryft-ai-consent", "yes");
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
  // jsdom has no decoder; a bitmap too small to resize sends the file as is.
  globalThis.createImageBitmap = vi.fn(async () => (
    { width: 10, height: 10, close() {} }));
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
  vi.unstubAllGlobals();
});

describe("the uploader's previews", () => {
  it("are revoked when the uploader goes with a pile still staged", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok({})));
    await mount();
    await pickPhotos(3);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();

    await act(async () => { root.unmount(); });
    root = null;
    await new Promise((r) => setTimeout(r, 5));
    expect(URL.revokeObjectURL.mock.calls.map(([u]) => u).sort())
      .toEqual(["blob:preview-0", "blob:preview-1", "blob:preview-2"]);
  });

  it("stay alive when the pile goes to a bulk upload", async () => {
    // The batch has not answered yet: the store is still holding the pile.
    let answer;
    vi.stubGlobal("fetch", vi.fn((url) => (
      String(url).includes("/api/bulk/upload")
        ? new Promise((r) => { answer = r; }) : ok({}))));
    await mount();
    await pickPhotos(2);
    await startBatch(2);
    expect(host.textContent).toContain("batch screen");   // the uploader is gone
    await tick();
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    // Let the upload finish, so its in-flight guard is free for the next test.
    await act(async () => { answer(await ok({ job_id: "j1" })); });
    await tick();
  });

  it("stay alive when a failed batch hands them back, StrictMode and all", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => (
      String(url).includes("/api/bulk/upload")
        ? ok({ detail: "The server is busy." }, 503) : ok({}))));
    await mount({ strict: true });
    await pickPhotos(2);
    await startBatch(2);
    await tick();
    expect(fetch.mock.calls.some(([u]) => String(u).includes("/api/bulk/upload")))
      .toBe(true);
    // Back on a fresh uploader, with the same pile, still drawable.
    expect(shown()).toEqual(["blob:preview-0", "blob:preview-1"]);
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
  });
});
