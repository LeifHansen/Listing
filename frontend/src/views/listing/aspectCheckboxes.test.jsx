/**
 * eBay's tick-box item specifics, and the values eBay suggests for them.
 *
 * Two things about an eBay aspect vary independently, and this card ran them
 * together. CARDINALITY says how many answers fit — MULTI is what eBay draws
 * as checkboxes. MODE says what the value list MEANS — SELECTION_ONLY makes it
 * law, FREE_TEXT makes it eBay's own SUGGESTIONS.
 *
 * The checklist was drawn only for SELECTION_ONLY + MULTI, so the tick-box
 * aspects eBay reports as FREE_TEXT + MULTI (Features, Occasion, Style and
 * Material, in a lot of categories) got a single text input: one answer where
 * eBay offers twenty boxes, with the extra values the AI found showing as
 * unexplained chips. And on every free-text aspect eBay's suggested values —
 * fetched on each lookup — reached nothing at all.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SpecificsCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const aspect = (name, over = {}) => ({
  name, required: false, mode: "FREE_TEXT", values: [],
  cardinality: "SINGLE", data_type: "STRING", ...over,
});

// The shape at the heart of this: eBay draws boxes AND takes a value of the
// seller's own.
const OPEN_FEATURES = aspect("Features", {
  cardinality: "MULTI", values: ["Breathable", "Pockets", "Lined"] });
const CLOSED_STYLE = aspect("Style", {
  cardinality: "MULTI", mode: "SELECTION_ONLY", values: ["Bomber", "Parka"] });
const OPEN_MATERIAL = aspect("Material", { values: ["Cotton", "Wool"] });

function stub(aspects, over = {}) {
  const specifics = over.item_specifics || [];
  const value = (name) => (specifics.find(
    (s) => s.name.trim().toLowerCase() === name.trim().toLowerCase()
      && (s.value || "").trim()) || {}).value || "";
  return {
    categoryMeta: { aspects },
    form: { item_specifics: specifics, brand: "" },
    isLive: false,
    completion: { specifics: "todo" },
    fixTarget: null,
    publishResult: null,
    getSpecific: value,
    getSpecificValues: (name) => specifics
      .filter((s) => s.name.trim().toLowerCase() === name.trim().toLowerCase())
      .map((s) => (s.value || "").trim()).filter(Boolean),
    set: () => {},
    upsertSpecific: () => {},
    toggleSpecificValue: () => {},
    confirmSpecific: () => {},
    confirmAllSpecifics: () => {},
    autofillSpecifics: () => {},
    ...over,
  };
}

let root;
let host;

async function mount(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<SpecificsCard w={w} />); });
  return host;
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

// Type into a controlled React input. Setting .value directly is invisible to
// React — it tracks the last value it wrote and skips the change — so the
// native setter has to do it.
const typeInto = (el, text) => {
  Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value").set.call(el, text);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};

const boxes = (h) => [...h.querySelectorAll('input[type="checkbox"]')];
const boxFor = (h, label) => boxes(h)
  .find((b) => b.closest("label")?.textContent.trim() === label);

describe("a multi-select aspect eBay reports as free text", () => {
  it("gets tick boxes, not one text input", async () => {
    // The defect, stated: cardinality alone decides the shape of the answer.
    const h = await mount(stub([OPEN_FEATURES]));
    expect(boxes(h).map((b) => b.closest("label").textContent.trim()))
      .toEqual(["Breathable", "Pockets", "Lined"]);
  });

  it("ticks the values the listing already holds", async () => {
    const h = await mount(stub([OPEN_FEATURES], {
      item_specifics: [
        { name: "Features", value: "Pockets", confidence: "medium" },
        { name: "Features", value: "Lined", confidence: "medium" },
      ],
    }));
    expect(boxFor(h, "Pockets").checked).toBe(true);
    expect(boxFor(h, "Lined").checked).toBe(true);
    expect(boxFor(h, "Breathable").checked).toBe(false);
    expect(h.textContent).toContain("2 selected");
  });

  it("keeps a box for a value eBay never suggested", async () => {
    // Off-list is legal here, so an off-list tick must stay visible — and
    // removable — instead of vanishing from the card.
    const h = await mount(stub([OPEN_FEATURES], {
      item_specifics: [{ name: "Features", value: "Reflective" }],
    }));
    expect(boxFor(h, "Reflective")?.checked).toBe(true);
  });

  it("offers a box of the seller's own, because the list is only advice", async () => {
    const toggle = vi.fn();
    const h = await mount(stub([OPEN_FEATURES], { toggleSpecificValue: toggle }));
    const own = h.querySelector('input[aria-label="Add a Features value of your own"]');
    expect(own).toBeTruthy();
    expect(h.textContent).toContain("or add your own");

    await act(async () => { typeInto(own, "Reflective"); });
    const add = [...h.querySelectorAll("button")]
      .find((b) => b.textContent.trim() === "Add");
    await act(async () => { add.click(); });
    expect(toggle).toHaveBeenCalledWith("Features", "Reflective", true);
  });

  it("ticks a box through the same toggle a closed list uses", async () => {
    const toggle = vi.fn();
    const h = await mount(stub([OPEN_FEATURES], { toggleSpecificValue: toggle }));
    await act(async () => { boxFor(h, "Lined").click(); });
    expect(toggle).toHaveBeenCalledWith("Features", "Lined", true);
  });
});

describe("a multi-select aspect eBay closes", () => {
  it("still gets tick boxes and no add-your-own", async () => {
    // There, an off-list value is one eBay refuses — offering a box for it
    // would invite a publish failure.
    const h = await mount(stub([CLOSED_STYLE]));
    expect(boxes(h).length).toBe(2);
    expect(h.querySelector('input[aria-label="Add a Style value of your own"]'))
      .toBeNull();
    expect(h.textContent).toContain("tick all that apply");
    expect(h.textContent).not.toContain("or add your own");
  });
});

describe("a single-value aspect eBay suggests values for", () => {
  it("keeps its one box and offers the suggestions beside it", async () => {
    // One answer, so no checkboxes — but eBay's wording is still worth
    // offering: a publish can be refused over "Cotton Blend" vs "Cotton blend".
    const h = await mount(stub([OPEN_MATERIAL]));
    expect(boxes(h).length).toBe(0);
    const list = h.querySelector("datalist");
    expect([...list.querySelectorAll("option")].map((o) => o.value))
      .toEqual(["Cotton", "Wool"]);
    expect(h.querySelector(`input[list="${list.id}"]`)).toBeTruthy();
  });

  it("gives an aspect with no suggestions no empty list to point at", async () => {
    const h = await mount(stub([aspect("Care Instructions")]));
    expect(h.querySelector("datalist")).toBeNull();
    expect(h.querySelector("input[list]")).toBeNull();
  });

  it("survives a name a DOM id cannot hold", async () => {
    // "Country/Region of Manufacture" — spaces and a slash. An id built from
    // it verbatim is not one a list= reference resolves.
    const h = await mount(stub([
      aspect("Country/Region of Manufacture", { values: ["Japan", "Italy"] })]));
    const list = h.querySelector("datalist");
    expect(list.id).toBe("sugg-country-region-of-manufacture");
    expect(h.querySelector(`input[list="${list.id}"]`)).toBeTruthy();
  });
});
