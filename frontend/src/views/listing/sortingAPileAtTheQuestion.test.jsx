/**
 * "It's more than one item", at the question of a single upload.
 *
 * The uploader has a bulk toggle, and a seller who forgot it reaches the
 * question with both items' photos under ONE card. That is the last moment
 * the mistake costs nothing — nothing has been drafted — so the card has to
 * offer the way back: sort these photos into items, the way a batch would
 * have. What is tested here is the hand-off that makes it work rather than
 * merely exist. The request carries what was typed, because that is the
 * sort's best hint. A pile that comes back sorted becomes a running batch —
 * the store's record of one, which is what puts the batch screen (with its
 * merge and remove tools, and its answer that drafts them all) in front of
 * the seller — and the uploader's own record of the question goes with it,
 * or a reload would put the single-item question back. And a sort that saw
 * one item leaves the question exactly where it was, and says why.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { readLocal, writeLocal } from "@/lib/localPrefs";
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

const PHOTOS = [
  "/media/s1/optimized/img_000.jpg",
  "/media/s1/optimized/img_001.jpg",
  "/media/s1/optimized/img_002.jpg",
];

const ONE = [{ gi: 0, key: 0, name: "", photo_count: 3, photos: PHOTOS }];
const TWO = [
  { gi: 0, key: 0, name: "a blue polo", photo_count: 2, photos: PHOTOS.slice(0, 2) },
  { gi: 1, key: 1, name: "a champagne bottle", photo_count: 1, photos: PHOTOS.slice(2) },
];

/* A server paused at the question with one item. A split request puts it
 * into "grouping" for one poll, then it answers with `after`: the sorted
 * pile, or the same one item with the server's reason beside it. */
function serverPausedOnOneItem({ after }) {
  const splits = [];
  let sorting = false;
  let polls = 0;
  vi.stubGlobal("fetch", vi.fn((url, opts) => {
    const u = String(url);
    if (u.endsWith("/split")) {
      splits.push(JSON.parse(opts.body));
      sorting = true;
      polls = 0;
      return ok({ ok: true, job_id: "j1" });
    }
    if (u.includes("/api/bulk/status/")) {
      if (sorting && polls++ < 1) {
        return ok({ id: "j1", done: false, phase: "grouping", total_photos: 3 });
      }
      if (sorting) {
        return ok({ id: "j1", done: false, phase: "awaiting_notes", ...after });
      }
      return ok({ id: "j1", done: false, phase: "awaiting_notes",
                  pending_items: ONE, split_error: null });
    }
    return ok({});
  }));
  return splits;
}

let root;
let host;

/* Straight to the question, without an upload: the uploader puts back a job
 * it left paused (see aiNotesStep.test.jsx). */
async function reachTheQuestion(opts) {
  const splits = serverPausedOnOneItem(opts);
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
  await settle();
  return splits;
}

async function settle(turns = 1) {
  for (let i = 0; i < turns; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

function button(label) {
  return [...host.querySelectorAll("button")]
    .find((b) => b.textContent.includes(label));
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

/** Wait, in real time, for `pred`: the poll between the sort and the answer
 *  sleeps 1.5s on the real timer. */
async function until(pred, { ms = 5000 } = {}) {
  const deadline = Date.now() + ms;
  while (!pred() && Date.now() < deadline) {
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
  }
  expect(pred()).toBe(true);
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

describe("sorting a single upload into items at the question", () => {
  it("offers the way back from the bulk toggle, beside Write my listing", async () => {
    await reachTheQuestion({ after: { pending_items: TWO } });
    expect(button("Write my listing")).toBeTruthy();
    expect(button("It's more than one item")).toBeTruthy();
  });

  it("sends what was typed as the sort's hint, and hands a sorted pile to the batch", async () => {
    const splits = await reachTheQuestion({ after: { pending_items: TWO, split_error: null } });
    await type(host.querySelector("textarea"), "a blue polo, a champagne bottle");

    await act(async () => { button("It's more than one item").click(); });
    await settle(2);
    // Sorting, out loud, while the server works.
    expect(host.textContent).toContain("Sorting your photos into items");
    await until(() => readLocal("bulk") !== null);

    expect(splits).toEqual([{ notes: "a blue polo, a champagne bottle" }]);
    // The batch screen takes over: the store holds the job as a running
    // batch, and the uploader's own record of the question is gone, so a
    // reload lands on the batch rather than on the single-item question.
    expect(JSON.parse(readLocal("bulk"))).toEqual({ jobId: "j1" });
    expect(readLocal("ask")).toBeNull();
  });

  it("keeps the question, and says why, when the sort still sees one item", async () => {
    const splits = await reachTheQuestion({
      after: { pending_items: ONE, split_error: "The AI still sees one item in these photos." },
    });
    await type(host.querySelector("textarea"), "a polo");

    await act(async () => { button("It's more than one item").click(); });
    await until(() => !!button("Write my listing"));

    expect(splits).toHaveLength(1);
    // Said as a toast, which the provider draws outside the uploader.
    expect(document.body.textContent).toContain("The AI still sees one item in these photos.");
    // Nothing lost: the question is back, the typing with it, and the job is
    // still the uploader's own.
    expect(host.querySelector("textarea").value).toBe("a polo");
    expect(readLocal("ask")).not.toBeNull();
    expect(readLocal("bulk")).toBeNull();
    expect(button("It's more than one item")).toBeTruthy();
  });
});
