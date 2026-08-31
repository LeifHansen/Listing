/**
 * A rotate that failed has to be a rotate the tile can take back.
 *
 * PhotoTile turns the photo on the tap and undoes that turn if the rotate is
 * rejected — that is the whole reason the optimistic turn is safe. The undo
 * was unreachable: rotateImage caught the failure, toasted it and resolved,
 * so the tile's catch never ran. Nor did anything else come along to correct
 * it, since a failed rotate bumps no version either. The photo stayed turned
 * on screen while the saved file was untouched, and stayed that way until the
 * editor was reopened.
 *
 * The toast is not the fix. "Couldn't rotate" over a photo that is visibly
 * rotated reads as a glitch in the message, not in the picture.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";

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

function refuse(status, detail) {
  return Promise.resolve({
    ok: false,
    status,
    statusText: "Bad Request",
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
    value.app.setSession({ sessionId: "s1", listing: { images: ["img_000.jpg"] } });
  });
  return () => value;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("rotateImage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("rejects when the server refused, so the tile can undo its turn",
    async () => {
      vi.stubGlobal("fetch", vi.fn((url) => (
        String(url).includes("/api/rotate-image")
          ? refuse(400, "Couldn't rotate that photo.")
          : ok({}))));
      const get = await mountEditor();

      let thrown = null;
      await act(async () => {
        thrown = await get().form.rotateImage("img_000.jpg").then(() => null,
                                                                 (e) => e);
      });

      expect(thrown).toBeTruthy();
      expect(thrown.message).toContain("Couldn't rotate that photo.");
    });

  it("bumps only the rotated photo's version on success", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok({ ok: true })));
    const get = await mountEditor();

    await act(async () => { await get().form.rotateImage("img_000.jpg"); });

    expect(get().form.imageVersions["img_000.jpg"]).toBe(1);
    expect(get().form.imageVersions["img_001.jpg"]).toBeUndefined();
  });

  it("leaves the version alone when the rotate did not happen", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => (
      String(url).includes("/api/rotate-image")
        ? refuse(502, "The photo was rotated here but the copy we publish "
                      + "from didn't update.")
        : ok({}))));
    const get = await mountEditor();

    await act(async () => {
      await get().form.rotateImage("img_000.jpg").catch(() => {});
    });

    expect(get().form.imageVersions["img_000.jpg"]).toBeUndefined();
  });
});
