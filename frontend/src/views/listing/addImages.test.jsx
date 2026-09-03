/* "Add photos" hands the work to a job and follows it.
 *
 * The route used to run the orientation pass and the cutouts inside the
 * request. A seller adding four photos waited on a spinner for the length of
 * all of that, and past the client's deadline the request was abandoned with
 * the photos and the tokens lost to work the server was still doing --
 * "adding photos is taking forever / not working". The request now returns a
 * job id the moment the files are saved; the editor polls it, shows which
 * photo it is on, and appends the result to the listing.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { grantAiConsent } from "@/lib/api";
import { useListingForm } from "./useListingForm";

// The browser-side downscale needs a real decoder; jsdom has none. The files
// go up as they are, which is all this test is about.
vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, downscaleAllForUpload: async (files) => files };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  const form = useListingForm();
  useEffect(() => { onValue({ app, form }); });
  return null;
}

let root;
let host;

async function mountEditor() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe onValue={(v) => { value = v; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing: { images: ["img_000.jpg"] } });
  });
  // Adding photos may run the AI cutout, and the client refuses AI endpoints
  // until the seller has agreed once -- which the real editor has by now.
  grantAiConsent();
  return () => value;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("addImages", () => {
  it("starts a job, follows it, and appends what it made", async () => {
    const calls = [];
    let polls = 0;
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      const path = String(url);
      calls.push({ path, method: opts.method || "GET" });
      if (path.startsWith("/api/upload-more/s1")) {
        return ok({ job_id: "job-9", running: true, total: 2 });
      }
      if (path.startsWith("/api/bulk/status/job-9")) {
        polls += 1;
        if (polls === 1) {
          return ok({ id: "job-9", done: false, phase: "optimizing",
                      current: 0, total_photos: 2, beat: 1 });
        }
        return ok({ id: "job-9", done: true, phase: "done", result: {
          added: ["img_001.jpg", "img_002.jpg"],
          optimized: ["img_000.jpg", "img_001.jpg", "img_002.jpg"],
          optimize_results: [],
        } });
      }
      if (path.startsWith("/api/save/s1")) return ok({ ok: true });
      return ok({});
    }));
    const get = await mountEditor();
    const files = [new File(["a"], "a.jpg", { type: "image/jpeg" }),
                   new File(["b"], "b.jpg", { type: "image/jpeg" })];

    await act(async () => { await get().form.addImages(files); });

    expect(get().form.form.images).toEqual(["img_000.jpg", "img_001.jpg", "img_002.jpg"]);
    // The request carried the files; the work was followed on the job.
    expect(calls.filter((c) => c.path.startsWith("/api/upload-more/")).length).toBe(1);
    expect(calls.filter((c) => c.path.startsWith("/api/bulk/status/job-9")).length).toBeGreaterThan(1);
    expect(calls.some((c) => c.path.startsWith("/api/save/s1"))).toBe(true);
    expect(get().form.addingPhotos).toBe(false);
    expect(get().form.addingStatus).toBe("");
  });

  it("says why when the job could not make anything", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      const path = String(url);
      if (path.startsWith("/api/upload-more/s1")) return ok({ job_id: "job-8", running: true, total: 1 });
      if (path.startsWith("/api/bulk/status/job-8")) {
        return ok({ id: "job-8", done: true, phase: "failed",
                    error: "Could not process the uploaded image(s)." });
      }
      return ok({});
    }));
    const get = await mountEditor();
    await act(async () => {
      await get().form.addImages([new File(["x"], "x.jpg", { type: "image/jpeg" })]);
    });
    expect(get().form.form.images).toEqual(["img_000.jpg"]);
    expect(document.body.textContent).toContain("Could not process the uploaded image(s).");
  });
});
