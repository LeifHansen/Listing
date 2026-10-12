/* The item specifics as chips, with an inline editor.
 *
 * eBay's form draws every aspect of a category as a box -- forty for a pair
 * of jeans, most answered by the AI before the seller arrives. Chips read in
 * one glance: required first (empty ones amber and named as required), the
 * filled recommended ones with their trust mark, the next few empty ones as
 * ghosts, and "Show N more". Tapping a chip swaps the aspect's control in
 * under the chips; Enter commits and moves to the next empty required chip;
 * Escape closes (LISTING_REDESIGN.md, "Details").
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DetailsCard } from "./Details";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const aspect = (name, over = {}) => ({
  name, required: false, mode: "FREE_TEXT", values: [],
  cardinality: "SINGLE", data_type: "STRING", ...over,
});

// Two required, eight recommended: more empty recommended than the section
// shows as ghosts.
const ASPECTS = [
  aspect("Brand", { required: true }),
  aspect("Size", { required: true }),
  ...["Colour", "Style", "Material", "Pattern", "Fit", "Season", "Theme", "Closure"]
    .map((n) => aspect(n)),
];

function stub(over = {}) {
  const specifics = over.item_specifics || [];
  const value = (name) => (specifics.find(
    (s) => s.name.trim().toLowerCase() === name.trim().toLowerCase()
      && (s.value || "").trim()) || {}).value || "";
  return {
    categoryMeta: { aspects: ASPECTS },
    form: { item_specifics: specifics, brand: over.brand || "" },
    isLive: false,
    completion: { specifics: "todo" },
    fixTarget: null,
    publishResult: null,
    getSpecific: value,
    getSpecificValues: () => [],
    set: vi.fn(),
    upsertSpecific: vi.fn(),
    toggleSpecificValue: vi.fn(),
    ...over,
  };
}

let root;
let host;

async function mount(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<DetailsCard w={w} />); });
  return host;
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

const chips = () => [...host.querySelectorAll("[data-chip]")];
const chip = (name) => host.querySelector(`[data-chip="${name}"]`);
const editor = () => host.querySelector("[data-chip-editor]");
const press = (el, key) => act(async () => {
  el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
});

const FILLED = [
  { name: "Brand", value: "Levi's", confidence: "high" },
  { name: "Colour", value: "Blue", confidence: "medium" },
  { name: "Fit", value: "Straight", confidence: "" },
];

describe("the chips", () => {
  it("come in reading order: required, filled, then a few ghosts", async () => {
    await mount(stub({ item_specifics: FILLED }));
    expect(chips().map((c) => [c.getAttribute("data-chip"), c.getAttribute("data-chip-state")]))
      .toEqual([
        ["Brand", "filled"], ["Size", "missing"],
        ["Colour", "filled"], ["Fit", "filled"],
        // The first six empty recommended, as ghosts; Theme and Closure wait
        // behind "Show 2 more".
        ["Style", "ghost"], ["Material", "ghost"], ["Pattern", "ghost"],
        ["Season", "ghost"], ["Theme", "ghost"], ["Closure", "ghost"],
      ]);
    expect(host.textContent).not.toContain("Show");
  });

  it("say what is in them, and what is missing", async () => {
    await mount(stub({ item_specifics: FILLED }));
    expect(chip("Brand").textContent).toContain("Brand:");
    expect(chip("Brand").textContent).toContain("Levi's");
    expect(chip("Size").textContent).toBe("Size — required");
    expect(chip("Style").textContent).toBe("+ Style");
    // The trust mark rides on the chip.
    expect(chip("Colour").querySelector('[aria-label="Inferred by the AI — worth a glance"]'))
      .toBeTruthy();
    expect(chip("Brand").querySelector('[aria-label="Read from your photos by the AI"]'))
      .toBeTruthy();
  });

  it("hide the empty recommended past the first six behind Show N more", async () => {
    await mount(stub({ item_specifics: [] }));
    expect(chips().map((c) => c.getAttribute("data-chip")))
      .toEqual(["Brand", "Size", "Colour", "Style", "Material", "Pattern", "Fit", "Season"]);
    const more = [...host.querySelectorAll("button")]
      .find((b) => b.textContent.includes("Show 2 more"));
    await act(async () => { more.click(); });
    expect(chip("Closure")).toBeTruthy();
  });

  it("show the brand off the listing when there is no Brand row", async () => {
    await mount(stub({ item_specifics: [], brand: "Acme" }));
    expect(chip("Brand").getAttribute("data-chip-state")).toBe("filled");
    expect(chip("Brand").textContent).toContain("Acme");
  });
});

describe("the chip editor", () => {
  it("opens under the chips with the aspect's own control", async () => {
    await mount(stub({ item_specifics: FILLED }));
    expect(editor()).toBeNull();
    await act(async () => { chip("Size").click(); });
    expect(editor().getAttribute("data-chip-editor")).toBe("Size");
    expect(chip("Size").getAttribute("aria-pressed")).toBe("true");
    const input = editor().querySelector("input");
    expect(input.getAttribute("placeholder")).toBe("Size");
    // Focus lands on it.
    expect(document.activeElement).toBe(input);
  });

  it("writes through to the listing as the seller types", async () => {
    const w = stub({ item_specifics: FILLED });
    await mount(w);
    await act(async () => { chip("Size").click(); });
    const input = editor().querySelector("input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")
        .set.call(input, "34");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(w.upsertSpecific).toHaveBeenCalledWith("Size", "34");
  });

  it("moves to the next empty required chip on Enter, and closes when there is none", async () => {
    await mount(stub({ item_specifics: [] }));
    await act(async () => { chip("Brand").click(); });
    expect(editor().getAttribute("data-chip-editor")).toBe("Brand");
    await press(editor().querySelector("input"), "Enter");
    expect(editor().getAttribute("data-chip-editor")).toBe("Size");
    await press(editor().querySelector("input"), "Enter");
    expect(editor()).toBeNull();
  });

  it("closes on Escape", async () => {
    await mount(stub({ item_specifics: FILLED }));
    await act(async () => { chip("Colour").click(); });
    await press(editor().querySelector("input"), "Escape");
    expect(editor()).toBeNull();
    expect(chip("Colour").getAttribute("aria-pressed")).toBe("false");
  });

  it("closes on a second tap of its chip", async () => {
    await mount(stub({ item_specifics: FILLED }));
    await act(async () => { chip("Colour").click(); });
    await act(async () => { chip("Colour").click(); });
    expect(editor()).toBeNull();
  });

  it("mirrors a Brand edit onto the listing's own brand", async () => {
    const w = stub({ item_specifics: [], brand: "Acme" });
    await mount(w);
    await act(async () => { chip("Brand").click(); });
    const input = editor().querySelector("input");
    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")
        .set.call(input, "Levi's");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(w.set).toHaveBeenCalledWith("brand", "Levi's");
    expect(w.upsertSpecific).toHaveBeenCalledWith("Brand", "Levi's");
  });
});
