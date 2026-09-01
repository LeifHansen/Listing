/**
 * Deleting a photo has to leave the editor holding the list the SERVER holds.
 *
 * The delete was optimistic in the form and nowhere else: the session kept the
 * photo, and the server kept it too (the route unlinked the file and never
 * touched the saved listing). So the next drag sent a list the server could
 * not match, and the reorder came back 409 — "this listing's photos changed
 * somewhere else" — about a listing nobody else had touched. The photo order
 * then snapped back on screen, and stayed unfixable until the editor was
 * reopened.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PHOTOS = ["img_1.jpg", "img_2.jpg", "img_3.jpg"];

function ok(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function refuse(status, detail) {
  return Promise.resolve({
    ok: false,
    status,
    statusText: "Conflict",
    headers: { get: () => "application/json" },
    json: () => Promise.resolve({ detail }),
    text: () => Promise.resolve(detail),
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
      <ToastProvider>
        <AppProvider>
          <Probe onValue={(v) => { value = v; }} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing: { images: [...PHOTOS] } });
  });
  return () => value;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("deleteImage", () => {
  beforeEach(() => { localStorage.clear(); });

  it("takes the photo out of the session as well as the form", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => (
      String(url).includes("/api/delete-image")
        ? ok({ ok: true, images: ["img_1.jpg", "img_3.jpg"] })
        : ok({}))));
    const get = await mountEditor();

    await act(async () => { await get().form.deleteImage("img_2.jpg"); });

    expect(get().form.form.images).toEqual(["img_1.jpg", "img_3.jpg"]);
    expect(get().app.session.listing.images).toEqual(["img_1.jpg", "img_3.jpg"]);
  });

  it("takes the server's remaining list over its own", async () => {
    // The server is the one that knows: another tab may have removed a photo
    // too, and its answer is what the next reorder is checked against.
    vi.stubGlobal("fetch", vi.fn((url) => (
      String(url).includes("/api/delete-image")
        ? ok({ ok: true, images: ["img_3.jpg"] })
        : ok({}))));
    const get = await mountEditor();

    await act(async () => { await get().form.deleteImage("img_2.jpg"); });

    expect(get().form.form.images).toEqual(["img_3.jpg"]);
    expect(get().app.session.listing.images).toEqual(["img_3.jpg"]);
  });

  it("puts the photo back everywhere when the delete failed", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => (
      String(url).includes("/api/delete-image")
        ? refuse(503, "Couldn't update this listing's photos just now.")
        : ok({}))));
    const get = await mountEditor();

    await act(async () => { await get().form.deleteImage("img_2.jpg"); });

    expect(get().form.form.images).toEqual(PHOTOS);
    expect(get().app.session.listing.images).toEqual(PHOTOS);
  });

  it("leaves the next drag sending the photos that are left", async () => {
    // The regression, end to end: delete, then reorder what remains.
    const sent = [];
    vi.stubGlobal("fetch", vi.fn((url, opts) => {
      const u = String(url);
      if (u.includes("/api/delete-image")) {
        return ok({ ok: true, images: ["img_1.jpg", "img_3.jpg"] });
      }
      if (u.includes("/images/order")) {
        const body = JSON.parse(opts.body);
        sent.push(body.images);
        return ok({ images: body.images });
      }
      return ok({});
    }));
    const get = await mountEditor();

    await act(async () => { await get().form.deleteImage("img_2.jpg"); });
    await act(async () => {
      await get().form.reorderImages(["img_3.jpg", "img_1.jpg"]);
    });

    expect(sent).toEqual([["img_3.jpg", "img_1.jpg"]]);
    expect(get().form.form.images).toEqual(["img_3.jpg", "img_1.jpg"]);
  });
});
