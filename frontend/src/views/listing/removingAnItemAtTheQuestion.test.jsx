/**
 * Removing a whole item at the guidance step.
 *
 * The batch has sorted the pile into items and nothing has been drafted, so
 * an item the seller doesn't want listed is free to drop right here — one tap
 * on its card, no confirm. What makes that safe rather than merely quick is
 * that a removal renumbers every item after it on the server: the notes typed
 * under the next item have to stay with THAT item, and a delete queued behind
 * the removal has to name the item's new slot, not the one it was tapped in.
 */
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiNotesStep } from "./AiNotesStep";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const P = (n) => `/media/s1/optimized/img_00${n}.jpg`;

/** What the server hands back: rows with a slot (`gi`) and an identity. */
function rowsOf(list) {
  return list.map((it, gi) => ({ ...it, gi, photo_count: it.photos.length }));
}

const START = [
  { key: 0, name: "coasters", photos: [P(0), P(1)] },
  { key: 1, name: "a blue decanter", photos: [P(2), P(3)] },
  { key: 2, name: "a doll head cup", photos: [P(4), P(5)] },
];

let root;
let host;

/** The step, in a parent that holds the notes the way BulkMode does, over a
 *  fake server that renumbers exactly as the real one does. */
async function mount({ refuse = false, hold } = {}) {
  let server = START.map((it) => ({ ...it }));
  const sent = [];
  const submitted = [];
  const onDeleteItem = vi.fn(async (gi, photo) => {
    sent.push({ item: gi, photo });
    if (hold) await hold;
    if (refuse) throw new Error("409");
    if (!server[gi]?.photos.includes(photo)) throw new Error("stale slot");
    server = server.filter((_, i) => i !== gi);
    return { ok: true, pending_items: rowsOf(server) };
  });
  const onDeletePhoto = vi.fn(async (gi, photo) => {
    sent.push({ photoOf: gi, photo });
    server = server.map((it, i) => (i === gi
      ? { ...it, photos: it.photos.filter((p) => p !== photo) } : it));
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
        onDeletePhoto={onDeletePhoto}
        onDeleteItem={onDeleteItem}
      />
    );
  }
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<Parent />); });
  return { sent, submitted };
}

const removers = () => [...host.querySelectorAll("button")]
  .filter((b) => (b.getAttribute("aria-label") || "").startsWith("Remove item"));
const names = () => [...host.querySelectorAll("p")]
  .map((p) => p.textContent).filter((t) => t.startsWith("the AI thinks"));
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

describe("removing an item at the guidance step", () => {
  it("offers it on every card, and takes the card at the tap", async () => {
    let release;
    const hold = new Promise((r) => { release = r; });
    const { sent } = await mount({ hold });
    expect(removers()).toHaveLength(3);

    await click(removers()[0]);

    expect(names()).toEqual(["the AI thinks: a blue decanter",
                             "the AI thinks: a doll head cup"]);
    expect(host.textContent).toContain("sorted into 2 items");
    // Sent with one of its own photos, so the server can tell the slot still
    // holds the item that was tapped.
    expect(sent).toEqual([{ item: 0, photo: P(0) }]);
    await act(async () => { release(); await hold; });
  });

  it("puts the card back when the server won't take it", async () => {
    await mount({ refuse: true });

    await click(removers()[1]);

    expect(names()).toHaveLength(3);
  });

  it("keeps each item's notes with that item, across the renumbering",
    async () => {
      const { submitted } = await mount();
      await type(host.querySelectorAll("textarea")[2], "Avon, 1970s");

      await click(removers()[0]);
      // The doll head cup is now the second card — and still has its note.
      expect(host.querySelectorAll("textarea")[1].value).toBe("Avon, 1970s");

      const write = [...host.querySelectorAll("button")]
        .find((b) => b.textContent.includes("Write 2 listings"));
      await click(write);

      // Sent under the slot the server now keeps it in.
      expect(submitted).toEqual([{ 1: "Avon, 1970s" }]);
    });

  it("aims a delete queued behind a removal at the item's new slot",
    async () => {
      let release;
      const hold = new Promise((r) => { release = r; });
      const { sent } = await mount({ hold });

      await click(removers()[0]);
      const bin = [...host.querySelectorAll("button")].find((b) =>
        (b.getAttribute("aria-label") || "") === "Remove photo 1");
      // First photo bin still on screen belongs to the decanter (was slot 1).
      await click(bin);
      await act(async () => { release(); await hold; });
      await settle();

      expect(sent).toEqual([{ item: 0, photo: P(0) },
                            { photoOf: 0, photo: P(2) }]);
    });

  it("is not offered on the last item left", async () => {
    await mount();
    await click(removers()[0]);
    await click(removers()[0]);

    expect(names()).toEqual(["the AI thinks: a doll head cup"]);
    expect(removers()).toHaveLength(0);
  });
});
