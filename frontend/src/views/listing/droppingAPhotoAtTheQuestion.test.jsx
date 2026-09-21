/**
 * Dropping a bad shot at the guidance step, from the seller's side.
 *
 * The question is asked WITH the photos — the optimized shots, already sorted
 * into items, that the AI is about to read. So it is also the last moment the
 * blurred one, the one of the floor and the receipt that got swept up with
 * the pile can be taken out for nothing: after it, removing a photo costs a
 * draft that had to look at it and an edit to undo.
 *
 * What is tested here is what makes that worth having rather than merely
 * present. The thumb goes at the tap, because a seller pruning forty items
 * cannot wait on a round trip forty times. It goes for the right item, by the
 * `gi` the server matches its own rows by — a delete sent against the wrong
 * one would take a photo off somebody else's polo. It comes BACK if the
 * server would not take it, because the alternative is a seller pressing on
 * believing a photo is out of their listing when it is still in it. And the
 * last photo cannot be dropped at all.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { writeLocal } from "@/lib/localPrefs";
import { UploadPhase } from "./UploadPhase";

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

function refused(status, detail) {
  return Promise.resolve({
    ok: false,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve({ detail }),
    text: () => Promise.resolve(JSON.stringify({ detail })),
  });
}

const PHOTOS = [
  "/media/s1/optimized/img_000.jpg",
  "/media/s1/optimized/img_001.jpg",
  "/media/s1/optimized/img_002.jpg",
  "/media/s1/optimized/img_003.jpg",
];

/** The paused job's rows: two items, so a delete has an item to get wrong,
 *  and the second of them is down to its last photo. */
function items() {
  return [
    { gi: 0, name: "a blue polo", photo_count: 3, photos: PHOTOS.slice(0, 3) },
    { gi: 1, name: "a lacoste polo", photo_count: 1, photos: PHOTOS.slice(3) },
  ];
}

/* A server paused at the question, which answers deletes as the real one
 * does: with the rows as they now are. `deletes` collects what went out;
 * `hold` gates the first one so the ordering claim can be read. */
function serverPausedAtTheQuestion({ answer } = {}) {
  const deletes = [];
  let rows = items();
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    const u = String(url);
    if (u.includes("/delete-photo")) {
      const body = JSON.parse(opts.body);
      deletes.push(body);
      if (answer) return answer(body, deletes.length);
      rows = rows.map((it) => (it.gi === body.gi
        ? { ...it, photos: it.photos.filter((p) => p !== body.photo),
            photo_count: it.photo_count - 1 }
        : it));
      return ok({ ok: true, pending_items: rows });
    }
    if (u.includes("/api/bulk/notes/")) return ok({ ok: true, items: 2 });
    if (u.includes("/api/bulk/status/")) {
      return ok({ id: "j1", done: false, phase: "awaiting_notes",
                  pending_items: rows });
    }
    return ok({});
  }));
  return deletes;
}

let root;
let host;

/* Straight to the question, without an upload: the uploader puts back a job
 * it left paused (see aiNotesStep.test.jsx), which is the shortest way to a
 * real screen with real rows on it. */
async function reachTheQuestion(opts) {
  const deletes = serverPausedAtTheQuestion(opts);
  writeLocal("ask", JSON.stringify({ sessionId: "s1", jobId: "j1" }));
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
  return deletes;
}

const bins = () => [...host.querySelectorAll("button")]
  .filter((b) => (b.getAttribute("aria-label") || "").includes("photo"));

const shown = () => [...host.querySelectorAll("img")]
  .map((i) => i.getAttribute("src"));

async function click(el) {
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem("thryft-ai-consent", "yes");
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("dropping a photo at the guidance step", () => {
  it("offers one on every photo the AI is about to read", async () => {
    await reachTheQuestion();

    expect(shown().length).toBe(4);
    // Always there rather than on hover: half these sellers are on a phone,
    // where an invisible control is no control at all.
    expect(bins().length).toBe(4);
  });

  it("names the photo and the item the server knows it by", async () => {
    const deletes = await reachTheQuestion();

    await click(bins()[1]);

    // `gi`, not the row's position: the server matches its own items by it,
    // and a delete keyed on anything else takes a photo off another polo.
    expect(deletes).toEqual([{ gi: 0, photo: PHOTOS[1] }]);
  });

  it("takes the thumb off screen at the tap, not at the answer", async () => {
    // A seller pruning forty items cannot wait on a round trip forty times,
    // and the server's answer only confirms what the tap already decided.
    let release;
    const held = new Promise((r) => { release = r; });
    await reachTheQuestion({ answer: () => held.then(() => ok({ ok: true })) });

    await click(bins()[0]);

    expect(shown().some((src) => src.includes("img_000"))).toBe(false);
    expect(shown().length).toBe(3);
    await act(async () => { release(); await held; });
  });

  it("puts it back when the server won't take it", async () => {
    // The photo is still in the batch. Leaving the gap on screen would have
    // the seller press on believing it is out of their listing.
    await reachTheQuestion({
      answer: () => refused(409, "That photo isn't part of this item any more."),
    });

    await click(bins()[0]);

    expect(shown().length).toBe(4);
    // The toast is portalled to the body, not into this screen's subtree.
    expect(document.body.textContent).toContain("Couldn't remove that photo");
  });

  it("will not let the last photo of an item go", async () => {
    // An item with none is drafted from an empty directory. The server
    // refuses it too; this is the half that says so before the tap.
    await reachTheQuestion();

    // The second item has exactly one photo — its button is the fourth.
    expect(bins()[3].disabled).toBe(true);
    expect(bins()[3].getAttribute("title")).toContain("at least one photo");
    // The first item has three, so none of its own is the last one.
    expect(bins().slice(0, 3).map((b) => b.disabled)).toEqual(
      [false, false, false]);
  });

  it("sends deletes one at a time", async () => {
    // Each one rewrites the item's whole photo list server-side, so two in
    // flight together are two rewrites of the same list — and the second is
    // refused, which would put a photo back under the hands of a seller who
    // is plainly pruning.
    let release;
    const held = new Promise((r) => { release = r; });
    const deletes = await reachTheQuestion({
      answer: (_body, n) => (n === 1 ? held.then(() => ok({ ok: true }))
        : ok({ ok: true })),
    });
    const [first, second] = bins();

    await click(first);
    await click(second);

    expect(deletes).toHaveLength(1);
    // ...and both thumbs are already gone, which is what makes the queue
    // free: the wait is behind the seller, not in front of them.
    expect(shown().length).toBe(2);
    await act(async () => { release(); await held; });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(deletes).toEqual([{ gi: 0, photo: PHOTOS[0] },
                             { gi: 0, photo: PHOTOS[1] }]);
  });

  it("leaves the question up and unanswered", async () => {
    // A delete says what the question is about. It does not answer it.
    const deletes = await reachTheQuestion();

    await click(bins()[0]);

    expect(deletes).toHaveLength(1);
    expect(host.textContent).toContain("anything the photos don't show?");
    expect(host.querySelectorAll("textarea").length).toBe(2);
  });
});
