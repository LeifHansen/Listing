/* A draft that finishes after the seller moved on does not take over.
 *
 * A single-item draft polls its job from the uploader, for a minute or more,
 * and nothing stops the seller going to Manage and opening another listing
 * meanwhile. When the draft landed it replaced whatever was open — the
 * listing being edited, unsaved edits and all — with the new draft. It is
 * saved either way; the seller is told where, and keeps what they were doing.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { UploadPhase } from "./UploadPhase";

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
  useEffect(() => { onValue(app); });
  return null;
}

let root;
let host;
let release;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("thryft-ai-consent", "yes");
  let n = 0;
  URL.createObjectURL = vi.fn(() => `blob:preview-${n++}`);
  URL.revokeObjectURL = vi.fn();
  globalThis.createImageBitmap = vi.fn(async () => ({ width: 10, height: 10, close() {} }));
  const held = new Promise((r) => { release = r; });
  vi.stubGlobal("fetch", vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/api/upload")) return ok({ session_id: "s1", job_id: "j1" });
    if (u.includes("/api/bulk/status/")) {
      await held;
      return ok({ id: "j1", done: true, phase: "done", items: [],
                  result: { listing: { title: "The new draft", images: [] },
                            confidence: "medium" } });
    }
    return ok({});
  }));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function startDrafting() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let app = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>
        <UploadPhase /><Probe onValue={(a) => { app = a; }} />
      </AppProvider></ToastProvider>);
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  const input = host.querySelector('input[type="file"]');
  Object.defineProperty(input, "files", { configurable: true, value: [
    new File([new Uint8Array([1, 2, 3])], "p.jpg", { type: "image/jpeg" })] });
  await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
  const go = [...host.querySelectorAll("button")]
    .find((b) => b.textContent.includes("Identify with AI"));
  await act(async () => { go.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return () => app;
}

async function land() {
  release();
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

describe("a draft that lands late", () => {
  it("leaves the listing the seller moved on to where it is", async () => {
    const app = await startDrafting();
    await act(async () => {
      app().setSession({ sessionId: "other", listing: { title: "Mid-edit" } });
    });
    await land();
    expect(app().session?.sessionId).toBe("other");
    expect(document.body.textContent).toContain("Your new draft is ready");
  });

  it("still opens in the editor when nothing else was opened", async () => {
    const app = await startDrafting();
    await land();
    expect(app().session?.sessionId).toBe("s1");
    expect(app().session?.listing?.title).toBe("The new draft");
  });
});
