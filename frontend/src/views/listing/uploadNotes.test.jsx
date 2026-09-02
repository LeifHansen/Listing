/**
 * The hints the seller types on the uploader have to leave the browser.
 *
 * "Notes for the AI" is a box that collects text and a button that starts an
 * upload, and the failure mode of that pair is silent in both directions: a
 * box wired to nothing looks identical to a working one, and the seller finds
 * out only from a draft that ignored everything they said. Worse, they will
 * keep typing into it.
 *
 * So these mount the real uploader, type into the real box, press the real
 * button, and read the multipart body that actually went out — for the single
 * listing and for the bulk pile, which post to different endpoints and reached
 * them by different code paths (the bulk one runs from the store, because the
 * batch screen unmounts this component mid-upload).
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const HINTS = "one vintage ralph lauren polo, two lacoste polos different size color";

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
      // A job id for the pipeline upload, and one for the batch. The poll
      // below answers done immediately so neither hangs the test.
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

/** Hand the hidden file input a photo, the way the file picker would. */
async function pickPhotos(n = 1) {
  const input = host.querySelector('input[type="file"]');
  const files = Array.from({ length: n }, (_, i) =>
    new File([new Uint8Array([1, 2, 3])], `p${i}.jpg`, { type: "image/jpeg" }));
  Object.defineProperty(input, "files", { value: files, configurable: true });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** Type into the notes box through React's own value setter. */
async function typeNotes(text) {
  const box = host.querySelector("textarea");
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return box;
}

function button(label) {
  return [...host.querySelectorAll("button")]
    .find((b) => b.textContent.includes(label));
}

async function press(label) {
  await act(async () => { button(label).click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => {
  localStorage.clear();
  // lib/api gates every photo-bearing call on the AI consent (see
  // lib/aiConsent). Without a stored yes the upload is refused before it
  // reaches fetch, and these tests are about the body of that request.
  localStorage.setItem("thryft-ai-consent", "yes");
  // jsdom has neither, and the uploader makes one preview URL per photo — so
  // they have to differ, or two tiles collide on the same React key.
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
  // The pre-upload downscale decodes every photo first. jsdom has no decoder
  // at all, and its <img> fallback never fires onload OR onerror — so without
  // this the upload waits on a promise that can never settle. A bitmap small
  // enough to need no resize sends the original file, which is what the
  // multipart body under test is made of.
  globalThis.createImageBitmap = vi.fn(async () => (
    { width: 10, height: 10, close() {} }));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("notes for the AI", () => {
  it("only appears once there are photos to describe", async () => {
    await mountUploader();
    expect(host.querySelector("textarea")).toBeNull();
    await pickPhotos();
    expect(host.querySelector("textarea")).not.toBeNull();
  });

  it("rides the single-listing upload", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();
    await typeNotes(HINTS);
    await press("Identify with AI");

    const upload = sent.find((s) => s.url.includes("/api/upload"));
    expect(upload, "no upload was sent").toBeTruthy();
    expect(upload.form.get("notes")).toBe(HINTS);
  });

  it("rides the bulk upload, which posts somewhere else entirely", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos(2);
    await typeNotes(HINTS);
    // Bulk mode: the pile has several items, which is where the hints say how
    // many drafts to expect.
    await act(async () => {
      host.querySelectorAll('input[type="checkbox"]')[1].click();
    });
    await press("Split 2 photos into listings");

    const batch = sent.find((s) => s.url.includes("/api/bulk/upload"));
    expect(batch, "no batch was sent").toBeTruthy();
    expect(batch.form.get("notes")).toBe(HINTS);
  });

  it("sends an empty field when the seller typed nothing", async () => {
    const sent = recordUploads();
    await mountUploader();
    await pickPhotos();
    await press("Identify with AI");

    expect(sent[0].form.get("notes")).toBe("");
  });

  it("counts the hints as they are typed, so the commas teach themselves", async () => {
    await mountUploader();
    await pickPhotos();
    expect(host.textContent).toContain("the AI works from the photos alone");

    await typeNotes("one vintage polo");
    expect(host.textContent).toContain("1 hint —");

    await typeNotes(HINTS);
    expect(host.textContent).toContain("2 hints —");
  });

  it("stops taking characters at the length the server keeps", async () => {
    await mountUploader();
    await pickPhotos();
    const box = await typeNotes("x".repeat(1500));
    expect(box.value.length).toBe(1000);
    expect(host.textContent).toContain("0 characters left");
  });
});
