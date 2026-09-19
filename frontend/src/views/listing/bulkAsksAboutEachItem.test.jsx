/* A batch stops and asks about each item before it drafts any of them.
 *
 * aiNotesStep.test.jsx covers the one-item upload. This is the case the
 * feature is really for, and the case with a way to go quietly wrong: a pile
 * split into several items, several boxes on screen, and an answer that has to
 * arrive keyed to the item the seller was looking at when they typed it. A
 * line that reached the wrong item is worse than no box at all — the seller
 * described the blue polo and the AI put it on the green one — and nothing on
 * screen afterwards would show them that it had happened.
 *
 * So these mount the real batch screen against a paused job and read the
 * request that leaves the browser.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { BulkQueue } from "@/views/listing/BulkMode";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BASE = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/health": { anthropic_configured: true, ebay_configured: true },
  "/api/ebay/status": { connected: true },
  "/api/notifications": { notifications: [], unread: 0, checked: true },
  "/api/marketplaces": { marketplaces: [] },
  "/api/tokens": { enabled: false, total: 0, packs: [], costs: {} },
};

// A batch that has optimized and split the pile, and has drafted nothing.
const PAUSED = {
  id: "job1", done: false, phase: "awaiting_notes", current: 0,
  total_items: 2, total_photos: 4, items: [],
  pending_items: [
    { gi: 0, name: "a blue polo", photo_count: 2,
      photos: ["/media/stg/optimized/img_000.jpg",
               "/media/stg/optimized/img_001.jpg"] },
    { gi: 1, name: "a lacoste polo", photo_count: 2,
      photos: ["/media/stg/optimized/img_002.jpg",
               "/media/stg/optimized/img_003.jpg"] },
  ],
};

const BLUE = "men's L, small mark on the left cuff";
const LACOSTE = "size 5 which is a men's large, tiny hole by the hem";

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function server(sent, status) {
  return (url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/bulk/notes/")) {
      sent.push({ path, body: JSON.parse(opts.body) });
      return json({ ok: true, items: 2 });
    }
    if (path.startsWith("/api/bulk/cancel/")) {
      sent.push({ path, body: null });
      return json({ ok: true, stopped: true, already_finished: false });
    }
    if (path.startsWith("/api/bulk/status/")) return json(status);
    if (path.startsWith("/api/listings")) {
      return json({ authed: true, db: { configured: true, connected: true },
                    listings: [] });
    }
    const key = Object.keys(BASE).find((k) => path.startsWith(k));
    return key ? json(BASE[key]) : json({ detail: "Not found" });
  };
}

async function mount(sent = [], status = PAUSED) {
  vi.stubGlobal("fetch", vi.fn(server(sent, status)));
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AppProvider>
          <BulkQueue jobId="job1" onExit={() => {}} onSettled={() => {}} />
        </AppProvider>
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => document.body.textContent || "" };
}

function boxes() {
  return [...document.body.querySelectorAll("textarea")];
}

function button(label) {
  return [...document.body.querySelectorAll("button")]
    .find((b) => (b.textContent || "").includes(label));
}

async function type(box, text) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function press(label) {
  await act(async () => { button(label).click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

describe("a batch waiting on the seller", () => {
  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("asks once per item, with that item's own photos", async () => {
    const { root } = await mount();

    expect(boxes()).toHaveLength(2);
    expect(document.body.textContent).toContain("sorted into 2 items");
    expect(document.body.textContent).toContain("a blue polo");
    expect(document.body.textContent).toContain("a lacoste polo");
    const srcs = [...document.body.querySelectorAll("img")]
      .map((i) => i.getAttribute("src"));
    expect(srcs.some((s) => s.includes("img_000.jpg"))).toBe(true);
    expect(srcs.some((s) => s.includes("img_003.jpg"))).toBe(true);
    await act(() => root.unmount());
  });

  it("does not claim to be working while it waits for a person", async () => {
    // The progress bar is for work the machine is doing. A bar inching along
    // while nothing is running is how a seller decides the app is broken and
    // closes the tab — on a batch that would have finished the moment they
    // answered.
    const { root, text } = await mount();

    expect(text()).not.toContain("Sorting photos into items");
    expect(text()).not.toContain("Identifying items");
    // ...and stopping is still on offer, because nothing has been drafted yet.
    expect(button("Stop batch")).toBeTruthy();
    await act(() => root.unmount());
  });

  it("sends each line keyed to the item it was typed about", async () => {
    const sent = [];
    const { root } = await mount(sent);
    const [blue, lacoste] = boxes();

    await type(blue, BLUE);
    await type(lacoste, LACOSTE);
    await press("Write 2 listings");

    expect(sent).toHaveLength(1);
    expect(sent[0].path).toContain("/api/bulk/notes/job1");
    expect(sent[0].body).toEqual({ notes: { 0: BLUE, 1: LACOSTE } });
    await act(() => root.unmount());
  });

  it("leaves an item the seller skipped out of the request entirely", async () => {
    // Not "" for the blank one: an empty string and an absent key mean the
    // same thing to the server, and sending only what was typed keeps the
    // request honest about what the seller actually said.
    const sent = [];
    const { root } = await mount(sent);

    await type(boxes()[1], LACOSTE);
    await press("Write 2 listings");

    expect(sent[0].body).toEqual({ notes: { 1: LACOSTE } });
    await act(() => root.unmount());
  });

  it("presses on with every box blank", async () => {
    const sent = [];
    const { root } = await mount(sent);

    await press("Write 2 listings");

    expect(sent[0].body).toEqual({ notes: {} });
    await act(() => root.unmount());
  });

  it("stops asking the moment the answer is sent", async () => {
    // The status this screen is reading still says "awaiting_notes" until the
    // next poll lands, up to a second and a half later. Leaving the boxes up
    // for that long invites a second answer over a batch that is already
    // drafting — which the server refuses, so the seller would be told their
    // notes failed after they had just been accepted.
    const { root } = await mount([]);

    await press("Write 2 listings");

    expect(boxes()).toHaveLength(0);
    await act(() => root.unmount());
  });
});
