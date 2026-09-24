/* "Add photos" finishes against the listing as it is when it finishes.
 *
 * An add runs for minutes — the upload, then a polled job doing the
 * orientation pass — and the editor stays live the whole time. It finished
 * against the photo list and the fields it had captured when it STARTED, so
 * the save at the end wrote that snapshot back over everything the seller did
 * meanwhile: a photo they deleted came back (its file already gone), a title
 * they retyped reverted. And if they opened another listing while it ran, the
 * first listing's photos were written into the second listing's editor.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { grantAiConsent } from "@/lib/api";
import { useListingForm } from "./useListingForm";

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

/** A server whose job poll is held until the test lets it answer. */
function server({ onSave, stored = {} }) {
  let release;
  const held = new Promise((r) => { release = r; });
  const calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url, opts = {}) => {
    const path = String(url);
    calls.push({ path, method: opts.method || "GET" });
    if (path.startsWith("/api/upload-more/s1")) return ok({ job_id: "job-1", running: true });
    if (path.startsWith("/api/bulk/status/job-1")) {
      await held;
      return ok({ id: "job-1", done: true, phase: "done", result: {
        added: ["img_009.jpg"], optimize_results: [] } });
    }
    if (path.startsWith("/api/save/s1")) {
      onSave(JSON.parse(opts.body));
      return ok({ ok: true });
    }
    if (path === "/api/listings/s1") return ok(stored);
    return ok({});
  }));
  return { release: () => release(), calls };
}

async function mountEditor(listing) {
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
  await act(async () => { value.app.setSession({ sessionId: "s1", listing }); });
  grantAiConsent();
  return () => value;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("adding photos keeps what was done meanwhile", () => {
  it("saves onto the photos and fields as they are when it finishes", async () => {
    const saves = [];
    const srv = server({ onSave: (b) => saves.push(b) });
    const get = await mountEditor(
      { title: "Old title", images: ["img_000.jpg", "img_001.jpg"] });

    let adding;
    await act(async () => {
      adding = get().form.addImages([new File(["a"], "a.jpg", { type: "image/jpeg" })]);
      await new Promise((r) => setTimeout(r, 0));
    });
    // While the job runs: a photo deleted, the title retyped.
    await act(async () => {
      get().form.set("images", ["img_001.jpg"]);
      get().form.set("title", "New title");
    });
    srv.release();
    await act(async () => { await adding; });

    expect(saves).toHaveLength(1);
    expect(saves[0].images).toEqual(["img_001.jpg", "img_009.jpg"]);
    expect(saves[0].title).toBe("New title");
    expect(get().form.form.images).toEqual(["img_001.jpg", "img_009.jpg"]);
  });

  it("never writes into another listing opened while it ran", async () => {
    const saves = [];
    const srv = server({
      onSave: (b) => saves.push(b),
      stored: { id: "s1", listing: { title: "Saved title",
                                     images: ["img_000.jpg"] } },
    });
    const get = await mountEditor({ title: "Old title", images: ["img_000.jpg"] });

    let adding;
    await act(async () => {
      adding = get().form.addImages([new File(["a"], "a.jpg", { type: "image/jpeg" })]);
      await new Promise((r) => setTimeout(r, 0));
    });
    await act(async () => {
      get().app.setSession({ sessionId: "s2",
                             listing: { title: "Other listing", images: ["b.jpg"] } });
    });
    srv.release();
    await act(async () => { await adding; });

    // The open editor is still the other listing's, untouched.
    expect(get().form.form.images).toEqual(["b.jpg"]);
    expect(get().form.form.title).toBe("Other listing");
    // The photos went onto the first listing, as the server held it.
    expect(srv.calls.some((c) => c.path === "/api/listings/s1")).toBe(true);
    expect(saves).toHaveLength(1);
    expect(saves[0].images).toEqual(["img_000.jpg", "img_009.jpg"]);
    expect(saves[0].title).toBe("Saved title");
  });
});
