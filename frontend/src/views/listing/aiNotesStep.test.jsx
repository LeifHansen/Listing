/**
 * The guidance step, from the seller's side.
 *
 * The server stops the pipeline once the photos are ready and asks what each
 * item is. That question is only worth asking if the answer leaves the
 * browser, and the failure mode of a box wired to nothing is silent in both
 * directions: it looks identical to a working one, and the seller finds out
 * only from a draft that ignored everything they typed. Worse, they will keep
 * typing into it — which is exactly why uploadNotes.test.jsx exists for the
 * other box, and why this one reads the body that actually went out.
 *
 * So these mount the real uploader, let the real poll hit a paused job, type
 * into the real box, press the real button, and inspect the request.
 *
 * Two other claims are tested here. It cannot become a wall — a seller who
 * has nothing to add presses on and gets the drafts they got before the step
 * existed. And it cannot lose work: an answer that fails to send, or a seller
 * who wanders off to Drafts and comes back, both find the question (and their
 * typing) where they left it.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { writeLocal } from "@/lib/localPrefs";
import { UploadPhase } from "./UploadPhase";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const LINE = "men's L, bought in Tokyo 2019, small mark on the left cuff";

function ok(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/* A server that pauses for notes, then drafts.
 *
 * The status flips the moment the notes land, which is what the real endpoint
 * does — it sets the phase before it starts the drafting thread, so a poll
 * that crosses with the answer never re-asks the question.
 */
function serverThatAsks() {
  const sent = [];
  let answered = false;
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    const u = String(url);
    if (u.includes("/api/upload")) {
      return ok({ session_id: "s1", job_id: "j1" });
    }
    if (u.includes("/api/bulk/notes/")) {
      sent.push({ url: u, body: JSON.parse(opts.body) });
      answered = true;
      return ok({ ok: true, items: 1 });
    }
    if (u.includes("/api/bulk/status/")) {
      if (!answered) {
        return ok({
          id: "j1", done: false, phase: "awaiting_notes",
          upload: { optimized: ["img_000.jpg"], optimize_results: [] },
          pending_items: [{
            gi: 0, name: "a blue polo", photo_count: 1,
            photos: ["/media/s1/optimized/img_000.jpg"],
          }],
        });
      }
      return ok({ id: "j1", done: true, phase: "done", items: [],
                  result: { listing: { images: [] }, confidence: "medium" } });
    }
    return ok({});
  }));
  return sent;
}

let root;
let host;

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

function button(label) {
  return [...host.querySelectorAll("button")]
    .find((b) => b.textContent.includes(label));
}

async function press(label) {
  await act(async () => { button(label).click(); });
  // Two turns of the loop: the POST, and the poll that follows it.
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

/** Type into a box through React's own value setter. */
async function type(box, text) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

/** Upload one photo and get as far as the question. */
async function reachTheQuestion() {
  const sent = serverThatAsks();
  await mountUploader();
  await pickPhotos();
  await press("Identify with AI");
  return sent;
}

beforeEach(() => {
  localStorage.clear();
  // lib/api gates every photo-bearing call on the AI consent (see
  // lib/aiConsent); without a stored yes the upload never reaches fetch.
  localStorage.setItem("thryft-ai-consent", "yes");
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
  // jsdom has no image decoder, and its <img> fallback fires neither onload
  // nor onerror — so without this the pre-upload downscale waits forever.
  globalThis.createImageBitmap = vi.fn(async () => (
    { width: 10, height: 10, close() {} }));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("the guidance step", () => {
  it("asks about the item the server is about to draft", async () => {
    await reachTheQuestion();

    // The drop zone is gone: the photos are on the server, and the only thing
    // left before the AI writes is what the seller knows.
    expect(host.textContent).not.toContain("Drag photos here");
    expect(host.textContent).toContain("Anything the photos don't show?");
    // The AI's own first guess, shown because it is what the box is there to
    // correct — and the photo it guessed from, served from the session.
    expect(host.textContent).toContain("a blue polo");
    const img = host.querySelector("img");
    expect(img.getAttribute("src")).toContain("/media/s1/optimized/img_000.jpg");
    expect(host.querySelectorAll("textarea")).toHaveLength(1);
  });

  it("sends what the seller typed, keyed to that item", async () => {
    const sent = await reachTheQuestion();
    await type(host.querySelector("textarea"), LINE);

    await press("Write my listing");

    expect(sent).toHaveLength(1);
    expect(sent[0].url).toContain("/api/bulk/notes/j1");
    expect(sent[0].body).toEqual({ notes: { 0: LINE } });
  });

  it("is never a wall: pressing on with an empty box still drafts", async () => {
    const sent = await reachTheQuestion();

    await press("Write my listing");

    expect(sent[0].body).toEqual({ notes: {} });
    // ...and the draft it produced is the one the editor opens, exactly as it
    // would be on a pass that never stopped to ask.
    expect(host.textContent).not.toContain("Anything the photos don't show?");
  });

  it("says the box is optional rather than leaving it to be guessed", async () => {
    await reachTheQuestion();

    expect(host.textContent).toContain("Every box is optional");
  });

  it("keeps the question up when the answer can't be sent", async () => {
    // The photos are on the server and the job is still paused, so the whole
    // recovery is pressing again — which is only possible if the boxes (and
    // what was typed into them) are still there.
    await mountUploader();
    vi.stubGlobal("fetch", vi.fn((url) => {
      const u = String(url);
      if (u.includes("/api/upload")) return ok({ session_id: "s1", job_id: "j1" });
      if (u.includes("/api/bulk/notes/")) {
        return Promise.resolve({
          ok: false, status: 503,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ detail: "nope" }),
          text: () => Promise.resolve('{"detail":"nope"}'),
        });
      }
      if (u.includes("/api/bulk/status/")) {
        return ok({ id: "j1", done: false, phase: "awaiting_notes",
                    pending_items: [{ gi: 0, name: "a blue polo",
                                      photo_count: 0, photos: [] }] });
      }
      return ok({});
    }));
    await pickPhotos();
    await press("Identify with AI");
    await type(host.querySelector("textarea"), LINE);

    await press("Write my listing");

    expect(host.textContent).toContain("Anything the photos don't show?");
    expect(host.querySelector("textarea").value).toBe(LINE);
  });
});

describe("a question the seller walked away from", () => {
  /* The step lives in the uploader, so opening Drafts or reloading unmounts
   * it. That used to cost nothing — the old straight-through pass finished in
   * the background and left a draft behind whether anyone was watching or
   * not. A paused job drafts nothing until it is answered, so an unmount that
   * forgets the question throws away the whole upload: the photos, the
   * optimizing, and the token already charged for the draft. */

  it("puts it back when they return", async () => {
    const sent = await reachTheQuestion();

    await act(() => root.unmount());
    host.remove();
    await mountUploader();

    expect(host.textContent).toContain("Anything the photos don't show?");
    expect(host.querySelectorAll("textarea")).toHaveLength(1);
    // ...and it is the SAME job, so answering it still drafts those photos.
    await type(host.querySelector("textarea"), LINE);
    await press("Write my listing");
    expect(sent[0].url).toContain("/api/bulk/notes/j1");
    expect(sent[0].body).toEqual({ notes: { 0: LINE } });
  });

  it("does not put back one the job has already moved past", async () => {
    // The draft landed in another tab, or the batch was stopped. A stale id
    // must not sit in front of the drop zone forever.
    writeLocal("ask", JSON.stringify({ sessionId: "s1", jobId: "j1" }));
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/api/bulk/status/")) {
        return ok({ id: "j1", done: true, phase: "done", items: [] });
      }
      return ok({});
    }));

    await mountUploader();

    // The uploader is what's on screen instead — folded, the way it opens.
    expect(host.textContent).toContain("Add photos");
    expect(host.textContent).not.toContain("Anything the photos don't show?");
    expect(host.querySelector("textarea")).toBeNull();
  });

  it("does not put back one the server has never heard of", async () => {
    writeLocal("ask", JSON.stringify({ sessionId: "s1", jobId: "gone" }));
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/api/bulk/status/")) {
        return Promise.resolve({
          ok: false, status: 404,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ detail: "Unknown job." }),
          text: () => Promise.resolve('{"detail":"Unknown job."}'),
        });
      }
      return ok({});
    }));

    await mountUploader();

    expect(host.textContent).toContain("Add photos");
    expect(host.querySelector("textarea")).toBeNull();
  });
});

describe("a question the server has already answered", () => {
  it("clears itself rather than refusing every press", async () => {
    // Another tab answered it, or a reload raced the first press. The server
    // refuses a second drafting run over one pile (409), so keeping the boxes
    // up would be a dead end: every press from here is refused, and the draft
    // the seller is waiting for is already in Drafts.
    await mountUploader();
    vi.stubGlobal("fetch", vi.fn((url) => {
      const u = String(url);
      if (u.includes("/api/upload")) return ok({ session_id: "s1", job_id: "j1" });
      if (u.includes("/api/bulk/notes/")) {
        return Promise.resolve({
          ok: false, status: 409,
          headers: { get: () => "application/json" },
          json: () => Promise.resolve({ detail: "already moved on" }),
          text: () => Promise.resolve('{"detail":"already moved on"}'),
        });
      }
      if (u.includes("/api/bulk/status/")) {
        return ok({ id: "j1", done: false, phase: "awaiting_notes",
                    pending_items: [{ gi: 0, name: "a blue polo",
                                      photo_count: 0, photos: [] }] });
      }
      return ok({});
    }));
    await pickPhotos();
    await press("Identify with AI");
    expect(host.querySelectorAll("textarea")).toHaveLength(1);

    await press("Write my listing");

    expect(host.textContent).not.toContain("Anything the photos don't show?");
    expect(host.textContent).toContain("Drag photos here");
  });
});
