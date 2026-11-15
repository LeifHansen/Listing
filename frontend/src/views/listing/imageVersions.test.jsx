/**
 * A photo's cache-buster is never one this page has used for it before.
 *
 * The versions were a per-open counter — 0, then 1, then 2 — and a browser
 * hands back an image it already loaded in this page for an identical URL
 * without asking. So "?v=1" showed an earlier edit: the studio saved, the
 * tile asked for the new version, and got the old picture back.
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

describe("bumpImageVersion", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("fetch", vi.fn(() => ok({})));
  });

  it("never reuses a version, and moves only the photo it names", async () => {
    const get = await mountEditor();
    const base = get().form.imageBase;
    expect(base).toBeGreaterThan(1_700_000_000_000);

    await act(async () => { get().form.bumpImageVersion("img_000.jpg"); });
    const first = get().form.imageVersions["img_000.jpg"];
    await act(async () => { get().form.bumpImageVersion("img_000.jpg"); });
    const second = get().form.imageVersions["img_000.jpg"];

    expect(first).toBeGreaterThan(base);
    expect(second).toBeGreaterThan(first);
    expect(get().form.imageVersions["img_001.jpg"]).toBeUndefined();
  });
});
