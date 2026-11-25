/* SplitListingDialog — taking a two-item draft apart, from the seller's side.
 *
 * The dialog decides nothing itself; what matters is what it sends. The
 * photos it names are the ones that were tapped, in the listing's own order.
 * It refuses to send a split that would leave either draft without a photo,
 * since neither could be written. The rewrite of this listing is on by
 * default — its text was about both items — and the note about what this
 * listing keeps goes only when that rewrite is going to read it. And a
 * refusal from the server keeps the dialog up, ticks and all. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MotionGlobalConfig } from "framer-motion";

import { SplitListingDialog } from "./SplitListingDialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;
let sent;

async function mount(onSplit) {
  sent = [];
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <SplitListingDialog
        open
        onClose={() => {}}
        sessionId="s1"
        images={["a.jpg", "b.jpg", "c.jpg"]}
        title="Blue polo and a champagne bottle"
        onSplit={onSplit || (async (body) => { sent.push(body); })}
      />,
    );
  });
  await settle();
}

async function settle() {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

const tiles = () => [...document.querySelectorAll('button[aria-pressed]')];
const splitButton = () => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.includes("Split into two listings"));
const textareas = () => [...document.querySelectorAll("textarea")];

async function click(el) {
  expect(el, "tried to click something that isn't on screen").toBeTruthy();
  await act(async () => { el.click(); });
  await settle();
}

async function type(box, text) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

let skipping;
beforeEach(() => {
  skipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
});

afterEach(async () => {
  MotionGlobalConfig.skipAnimations = skipping;
  await act(async () => { root.unmount(); });
  host.remove();
});

describe("the split dialog", () => {
  it("sends the tapped photos, in the listing's order, with the rewrite on", async () => {
    await mount();
    expect(tiles()).toHaveLength(3);
    // Nothing tapped: nothing to split.
    expect(splitButton().disabled).toBe(true);

    await click(tiles()[2]);
    await click(tiles()[0]);
    await type(textareas()[0], "a 1970s champagne bottle");
    await type(textareas()[1], "a blue polo, men's L");
    expect(document.body.textContent).toContain("2 photos move to the new draft · 1 stays here");
    await click(splitButton());

    expect(sent).toEqual([{
      photos: ["a.jpg", "c.jpg"], redraft: true,
      notes: "a 1970s champagne bottle", keep_notes: "a blue polo, men's L",
    }]);
  });

  it("will not leave this listing without a photo", async () => {
    await mount();
    for (const t of tiles()) await click(t);
    expect(document.body.textContent).toContain("leave at least one on this listing");
    expect(splitButton().disabled).toBe(true);
    // Untap one and it can go.
    await click(tiles()[1]);
    expect(splitButton().disabled).toBe(false);
  });

  it("sends no note about this listing when its text is being kept", async () => {
    await mount();
    await click(tiles()[2]);
    await type(textareas()[1], "would be lost");
    const toggle = document.querySelector('input[type="checkbox"]');
    await click(toggle);
    // The box for it goes with the rewrite it was for.
    expect(textareas()).toHaveLength(1);
    await click(splitButton());

    expect(sent).toEqual([{ photos: ["c.jpg"], redraft: false, notes: "" }]);
  });

  it("stays up, ticks in place, when the server says no", async () => {
    await mount(async () => { throw new Error("Only a draft can be split."); });
    await click(tiles()[1]);
    await click(splitButton());

    expect(document.body.textContent).toContain("Only a draft can be split.");
    expect(tiles()[1].getAttribute("aria-pressed")).toBe("true");
    expect(splitButton().disabled).toBe(false);
  });
});
