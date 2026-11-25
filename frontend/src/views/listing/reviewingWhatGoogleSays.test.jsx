/**
 * The review step: what Google says about each item, and the seller's yes.
 *
 * Every card shows Google image search's answer — the name, the used and new
 * price, Google's own words and the pages it read — with the box underneath
 * and one button. The button is Approve while the box says nothing Google has
 * not already been asked with, and Submit Changes the moment it does, which
 * sends the seller's words back to Google with the photo. The listing is
 * written about the item Google named and priced from its range, so approving
 * the card is approving the listing; these pin that the button only ever
 * approves an answer the seller has actually seen.
 */
import { act, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiNotesStep, priceSpan } from "./AiNotesStep";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const BOWL = {
  gi: 0, key: 0, name: "a bowl", photo_count: 1,
  photos: ["/media/st/optimized/img_000.jpg"],
};
const JACKET = {
  gi: 1, key: 1, name: "a jacket", photo_count: 1,
  photos: ["/media/st/optimized/img_001.jpg"],
};

const ANSWERED = {
  status: "done", ticket: "t-bowl", hint: "",
  identified: "Pyrex Butterprint 403 Mixing Bowl",
  used_low: 35, used_high: 55, new_low: null, new_high: null,
  confidence: "high",
  summary: "This is a Pyrex Butterprint 403 mixing bowl, made 1957-1968.",
  sources: [{ title: "Pyrex 403", link: "https://www.ebay.com/itm/1", source: "eBay" },
            { title: "bad", link: "javascript:alert(1)", source: "x" }],
};

let root;
let host;

/** The step as its callers use it: notes and approvals held outside it. */
function Harness({ items, google, googleEnabled = true, onSubmit, onAskGoogle }) {
  const [notes, setNotes] = useState({});
  const [approved, setApproved] = useState({});
  return (
    <AiNotesStep
      items={items}
      values={notes}
      onChange={(k, v) => setNotes((cur) => ({ ...cur, [k]: v }))}
      onSubmit={onSubmit}
      google={google}
      googleEnabled={googleEnabled}
      onAskGoogle={onAskGoogle}
      approved={approved}
      onApprovedChange={setApproved}
    />
  );
}

async function mount(props) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<Harness {...props} />); });
  return (next) => act(async () => { root.render(<Harness {...props} {...next} />); });
}

function buttons(label) {
  return [...host.querySelectorAll("button")]
    .filter((b) => b.textContent.includes(label));
}

async function click(el) {
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

async function type(box, text) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    setter.call(box, text);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("what Google says, on the card", () => {
  it("shows the name, the price, Google's words and where they came from", async () => {
    await mount({ items: [BOWL], google: { 0: ANSWERED }, onSubmit: vi.fn() });

    expect(host.textContent).toContain("Google says");
    expect(host.textContent).toContain("Pyrex Butterprint 403 Mixing Bowl");
    expect(host.textContent).toContain("Used $35–$55");
    expect(host.textContent).toContain("made 1957-1968");
    const links = [...host.querySelectorAll("a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["https://www.ebay.com/itm/1"]);
    expect(links[0].getAttribute("rel")).toContain("noopener");
    // The box sits under the answer, asking whether it is right.
    expect(host.textContent).toContain("Is this right?");
  });

  it("says Google is still looking, and will not approve what isn't there yet", async () => {
    const onSubmit = vi.fn();
    await mount({ items: [BOWL], google: { 0: { status: "pending", ticket: "t" } },
                  onSubmit });

    expect(host.textContent).toContain("Asking Google what this is");
    const [wait] = buttons("Waiting for Google");
    expect(wait.disabled).toBe(true);
    await click(wait);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("lets a seller approve past a lookup that failed, and says why it did", async () => {
    const onSubmit = vi.fn();
    await mount({ items: [BOWL],
                  google: { 0: { status: "failed", ticket: "t",
                                 reason: "Couldn't reach Google — try again in a moment." } },
                  onSubmit });

    expect(host.textContent).toContain("Couldn't reach Google");
    expect(buttons("Ask Google again")).toHaveLength(1);
    await click(buttons("Approve")[0]);
    expect(onSubmit).toHaveBeenCalledWith({});
  });
});

describe("Approve, or Submit Changes", () => {
  it("writes one item the moment its answer is approved", async () => {
    const onSubmit = vi.fn();
    await mount({ items: [BOWL], google: { 0: ANSWERED }, onSubmit });

    expect(host.textContent).toContain("Approving writes your listing.");
    await click(buttons("Approve")[0]);

    expect(onSubmit).toHaveBeenCalledWith({});
  });

  it("turns Approve into Submit Changes once the seller types, and asks Google again", async () => {
    const onSubmit = vi.fn();
    const onAskGoogle = vi.fn(async () => ({
      google: { 0: { status: "pending", ticket: "t-2", hint: "it's the 403" } } }));
    await mount({ items: [BOWL], google: { 0: ANSWERED }, onSubmit, onAskGoogle });

    await type(host.querySelector("textarea"), "it's the 403");
    expect(buttons("Approve")).toHaveLength(0);
    await click(buttons("Submit Changes")[0]);

    expect(onAskGoogle).toHaveBeenCalledWith(0, BOWL.photos[0], "it's the 403");
    expect(onSubmit).not.toHaveBeenCalled();
    // The card waits on the new answer rather than showing the old one, even
    // though the status it was handed still carries the old one.
    expect(host.textContent).toContain("Asking Google again with your changes");
  });

  it("approves the new answer once Google has been asked with the seller's words", async () => {
    const onSubmit = vi.fn();
    const onAskGoogle = vi.fn(async () => ({
      google: { 0: { status: "pending", ticket: "t-2", hint: "it's the 403" } } }));
    const rerender = await mount({ items: [BOWL], google: { 0: ANSWERED },
                                   onSubmit, onAskGoogle });
    await type(host.querySelector("textarea"), "it's the 403");
    await click(buttons("Submit Changes")[0]);

    await rerender({ google: { 0: { ...ANSWERED, ticket: "t-2", hint: "it's the 403",
                                    identified: "Pyrex 403 (2.5 qt)" } } });

    expect(host.textContent).toContain("Pyrex 403 (2.5 qt)");
    await click(buttons("Approve")[0]);
    expect(onSubmit).toHaveBeenCalledWith({ 0: "it's the 403" });
  });

  it("without Google, Submit Changes keeps the note for the AI", async () => {
    const onSubmit = vi.fn();
    await mount({ items: [BOWL], googleEnabled: false, onSubmit });

    expect(host.textContent).not.toContain("Google says");
    await type(host.querySelector("textarea"), "men's L");
    await click(buttons("Submit Changes")[0]);

    expect(onSubmit).toHaveBeenCalledWith({ 0: "men's L" });
  });
});

describe("a batch", () => {
  it("is written only once every item is approved", async () => {
    const onSubmit = vi.fn();
    await mount({ items: [BOWL, JACKET],
                  google: { 0: ANSWERED, 1: { ...ANSWERED, ticket: "t-jacket",
                                              identified: "Carhartt Detroit Jacket" } },
                  onSubmit });
    const [write] = buttons("Write 2 listings");
    expect(write.disabled).toBe(true);
    expect(host.textContent).toContain("0 of 2 approved");

    await click(buttons("Approve")[0]);
    expect(host.textContent).toContain("1 of 2 approved");
    expect(buttons("Write 2 listings")[0].disabled).toBe(true);
    expect(onSubmit).not.toHaveBeenCalled();

    await click(buttons("Approve")[0]);
    expect(host.textContent).toContain("2 of 2 approved");
    await click(buttons("Write 2 listings")[0]);
    expect(onSubmit).toHaveBeenCalledWith({});
  });

  it("approves everything ready at once, and leaves a card with changes to send", async () => {
    await mount({ items: [BOWL, JACKET],
                  google: { 0: ANSWERED, 1: { ...ANSWERED, ticket: "t-jacket" } },
                  onSubmit: vi.fn(), onAskGoogle: vi.fn(async () => ({})) });
    await type(host.querySelectorAll("textarea")[1], "it's the J97");

    await click(buttons("Approve all")[0]);

    expect(host.textContent).toContain("1 of 2 approved");
    expect(host.textContent).toContain("1 with changes to submit");
    expect(buttons("Write 2 listings")[0].disabled).toBe(true);
  });

  it("takes an approval back when Google's answer changes under it", async () => {
    const rerender = await mount({ items: [BOWL, JACKET],
                                   google: { 0: ANSWERED, 1: { ...ANSWERED, ticket: "t-j" } },
                                   onSubmit: vi.fn() });
    await click(buttons("Approve all")[0]);
    expect(host.textContent).toContain("2 of 2 approved");

    // A photo removed, so the bowl was looked up again.
    await rerender({ google: { 0: { ...ANSWERED, ticket: "t-bowl-2" },
                               1: { ...ANSWERED, ticket: "t-j" } } });

    expect(host.textContent).toContain("1 of 2 approved");
  });
});

describe("a price range, said the way a seller says it", () => {
  it.each([
    [35, 55, "$35–$55"],
    [1200, 1500, "$1,200–$1,500"],
    [19.99, 19.99, "$19.99"],
    [12, null, "$12"],
    [null, null, ""],
  ])("%s to %s is %s", (low, high, said) => {
    expect(priceSpan(low, high)).toBe(said);
  });
});
