/**
 * Merging items at the guidance step.
 *
 * The grouping's usual mistake is one thing shot from enough angles to look
 * like two or three. The seller sees that on the sorted pile, before anything
 * is drafted, so they tick the cards that are the same thing and merge them
 * into one. What makes that safe: the merge waits for the server's answer
 * (only it can say what the merged item is), what was typed under each card
 * goes with it into the one they became, and a merge queued behind a removal
 * names the items' new slots, not the ones they were ticked in.
 */
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiNotesStep, joinItemNotes } from "./AiNotesStep";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const P = (n) => `/media/s1/optimized/img_00${n}.jpg`;
const pileIndex = (url) => Number(url.match(/img_(\d+)/)[1]);

/** What the server hands back: rows with a slot (`gi`) and an identity. */
function rowsOf(list) {
  return list.map((it, gi) => ({ ...it, gi, photo_count: it.photos.length }));
}

const START = [
  { key: 0, name: "cloisonne bell #1", photos: [P(0), P(1)] },
  { key: 1, name: "cloisonne bell #2", photos: [P(2), P(3)] },
  { key: 2, name: "a doll head cup", photos: [P(4), P(5)] },
];

let root;
let host;

/** The step, in a parent that holds the notes the way BulkMode does, over a
 *  fake server that merges and renumbers exactly as the real one does. */
async function mount({ refuse = false, hold, withMerge = true } = {}) {
  let server = START.map((it) => ({ ...it }));
  const sent = [];
  const submitted = [];
  const onMergeItems = vi.fn(async (items) => {
    sent.push({ merge: items });
    if (hold) await hold;
    if (refuse) throw new Error("409");
    if (items.some(({ gi, photo }) => !server[gi]?.photos.includes(photo))) {
      throw new Error("stale slot");
    }
    const slots = [...new Set(items.map((it) => it.gi))].sort((a, b) => a - b);
    const into = slots[0];
    const photos = [...new Set(slots.flatMap((gi) => server[gi].photos))]
      .sort((a, b) => pileIndex(a) - pileIndex(b));
    server = server
      .map((it, gi) => (gi === into
        ? { ...it, name: it.name.replace(/\s*#\d+$/, ""), photos } : it))
      .filter((_, gi) => gi === into || !slots.includes(gi));
    return { ok: true, pending_items: rowsOf(server), merged: into };
  });
  const onDeleteItem = vi.fn(async (gi, photo) => {
    sent.push({ remove: gi, photo });
    if (!server[gi]?.photos.includes(photo)) throw new Error("stale slot");
    server = server.filter((_, i) => i !== gi);
    return { ok: true, pending_items: rowsOf(server) };
  });
  function Parent() {
    const [notes, setNotes] = useState({});
    return (
      <AiNotesStep
        items={rowsOf(START)}
        values={notes}
        onChange={(k, t) => setNotes((cur) => ({ ...cur, [k]: t }))}
        onSubmit={(out) => submitted.push(out)}
        onDeletePhoto={vi.fn()}
        onDeleteItem={onDeleteItem}
        onMergeItems={withMerge ? onMergeItems : undefined}
      />
    );
  }
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<Parent />); });
  return { sent, submitted, onMergeItems };
}

const ticks = () => [...host.querySelectorAll("input[type=checkbox]")];
const buttons = () => [...host.querySelectorAll("button")];
const button = (text) => buttons().find((b) => b.textContent.includes(text));
const names = () => [...host.querySelectorAll("p")]
  .map((p) => p.textContent).filter((t) => t.startsWith("the AI thinks"));
const thumbs = () => [...host.querySelectorAll("img")].length;
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

async function click(el) {
  await act(async () => { el.click(); });
  await settle();
}

async function type(textarea, value) {
  const set = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    set.call(textarea, value);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("merging items at the guidance step", () => {
  it("offers a tick on every card, and the merge once two are ticked",
    async () => {
      await mount();
      expect(ticks()).toHaveLength(3);
      expect(button("Merge")).toBeUndefined();

      await click(ticks()[0]);
      expect(host.textContent).toContain("Now tick the other card");
      expect(button("Merge")).toBeUndefined();

      await click(ticks()[1]);
      expect(button("Merge 2 items into one")).toBeTruthy();
    });

  it("folds the ticked cards into one when the server says so", async () => {
    const { sent } = await mount();
    await click(ticks()[0]);
    await click(ticks()[1]);

    await click(button("Merge 2 items into one"));

    // Each item sent with one of its own photos, so the server can tell the
    // slots still hold the items that were ticked.
    expect(sent).toEqual([{ merge: [{ gi: 0, photo: P(0) },
                                    { gi: 1, photo: P(2) }] }]);
    expect(names()).toEqual(["the AI thinks: cloisonne bell",
                             "the AI thinks: a doll head cup"]);
    expect(thumbs()).toBe(6);
    expect(host.textContent).toContain("sorted into 2 items");
    expect(button("Write 2 listings")).toBeTruthy();
    // Done with: nothing is left ticked.
    expect(ticks().every((t) => !t.checked)).toBe(true);
  });

  it("leaves the cards alone, and holds the write, until the answer lands",
    async () => {
      let release;
      const hold = new Promise((r) => { release = r; });
      const { submitted } = await mount({ hold });
      await click(ticks()[0]);
      await click(ticks()[1]);

      await click(button("Merge 2 items into one"));

      expect(names()).toHaveLength(3);
      const write = button("Write 3 listings");
      expect(write.disabled).toBe(true);
      await click(write);
      expect(submitted).toEqual([]);

      await act(async () => { release(); await hold; });
      await settle();
      expect(names()).toHaveLength(2);
    });

  it("carries what was typed under each card into the one they became",
    async () => {
      const { submitted } = await mount();
      const boxes = host.querySelectorAll("textarea");
      await type(boxes[0], "Cloisonne, Japanese");
      await type(boxes[1], "small chip on the rim");
      await type(boxes[2], "Avon, 1970s");
      await click(ticks()[0]);
      await click(ticks()[1]);

      await click(button("Merge 2 items into one"));

      const after = host.querySelectorAll("textarea");
      expect(after).toHaveLength(2);
      expect(after[0].value).toBe("Cloisonne, Japanese. small chip on the rim");
      // The one that wasn't merged keeps its own.
      expect(after[1].value).toBe("Avon, 1970s");

      await click(button("Write 2 listings"));
      expect(submitted).toEqual([{
        0: "Cloisonne, Japanese. small chip on the rim",
        1: "Avon, 1970s",
      }]);
    });

  it("leaves everything as it was, still ticked, when the server won't",
    async () => {
      await mount({ refuse: true });
      await click(ticks()[0]);
      await click(ticks()[2]);

      await click(button("Merge 2 items into one"));

      expect(names()).toHaveLength(3);
      expect(ticks().map((t) => t.checked)).toEqual([true, false, true]);
      expect(button("Merge 2 items into one").disabled).toBe(false);
    });

  it("aims a merge queued behind a removal at the items' new slots",
    async () => {
      const { sent } = await mount();
      // Tick the second bell and the cup, then remove the first bell — which
      // moves both of them up a slot before the merge goes out.
      await click(ticks()[1]);
      await click(ticks()[2]);
      const remove = buttons().find((b) =>
        (b.getAttribute("aria-label") || "") === "Remove item 1");
      await act(async () => { remove.click(); });

      await click(button("Merge 2 items into one"));

      expect(sent).toEqual([
        { remove: 0, photo: P(0) },
        { merge: [{ gi: 0, photo: P(2) }, { gi: 1, photo: P(4) }] },
      ]);
      expect(names()).toEqual(["the AI thinks: cloisonne bell"]);
    });

  it("is not offered once one item is left, or where merging isn't wired",
    async () => {
      await mount();
      ticks().forEach((t) => { act(() => { t.click(); }); });
      await settle();
      await click(button("Merge 3 items into one"));
      expect(names()).toEqual(["the AI thinks: cloisonne bell"]);
      expect(ticks()).toHaveLength(0);

      act(() => root.unmount());
      host.remove();
      await mount({ withMerge: false });
      expect(ticks()).toHaveLength(0);
    });
});

describe("joinItemNotes", () => {
  it("joins lines as sentences, once each", () => {
    expect(joinItemNotes(["Lacoste", "size L"])).toBe("Lacoste. size L");
    expect(joinItemNotes(["Lacoste.", "size L"])).toBe("Lacoste. size L");
    expect(joinItemNotes(["", "  ", "size L"])).toBe("size L");
    expect(joinItemNotes(["Size L", "size l"])).toBe("Size L");
    expect(joinItemNotes([])).toBe("");
  });

  it("stays inside the box's limit", () => {
    const long = "x".repeat(500);
    expect(joinItemNotes([long, "y".repeat(500)]).length).toBe(600);
  });
});
