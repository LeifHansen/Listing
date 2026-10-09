/* The category, as a line rather than three boxes.
 *
 * The old card asked for a free-text label eBay never reads and a numeric
 * id no seller can know, with a "Suggest categories" button underneath.
 * Both boxes existed only to carry what the picker chose. Now the section
 * shows the path with its leaf in bold and a pencil; the pencil swaps in the
 * picker -- the same list the draft cards use (CategorySuggestList) -- and
 * the number is read-only under More options (LISTING_REDESIGN.md,
 * "Category").
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { CategoryCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

const SUGGESTIONS = {
  items: [
    { category_id: "15709", path: "Clothing > Men > Shoes > Athletic Shoes" },
    { category_id: "93427", path: "Clothing > Men > Shoes > Casual Shoes" },
  ],
};

/** The slice of useListingForm the section reads, recording every call. */
function stub(calls, over = {}, catSuggestions = null) {
  return {
    fixLevel: () => undefined,
    fixTarget: null,
    form: {
      title: "Nike Air Max 90", category_id: "15709",
      category_suggestion: "Clothing > Men > Shoes > Athletic Shoes", ...over,
    },
    completion: { category: "todo" },
    catSuggestions,
    suggestCategories: (q) => calls.push(["suggest", q]),
    chooseCategory: (c) => calls.push(["choose", c.category_id]),
  };
}

function render(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => { root.render(<ToastProvider><CategoryCard w={w} /></ToastProvider>); });
  return host;
}

const line = () => host.querySelector('button[aria-label^="Category:"], button[aria-label="Pick a category"]');

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the category line", () => {
  it("shows the path with its leaf in bold, and nothing to type into", () => {
    render(stub([]));
    expect(line().textContent).toContain("Clothing");
    expect(line().querySelector("strong").textContent).toBe("Athletic Shoes");
    // No label box, no id box: the number is under More options, read-only.
    expect(host.querySelector("input")).toBeNull();
    expect(host.textContent).not.toContain("15709");
  });

  it("says plainly when there is no category yet", () => {
    render(stub([], { category_id: "", category_suggestion: "" }));
    expect(line().textContent).toContain("No category yet");
    expect(line().className).toContain("border-warning");
  });

  it("opens the picker from the pencil and runs the lookup", () => {
    const calls = [];
    render(stub(calls));
    act(() => line().click());
    expect(calls).toEqual([["suggest", undefined]]);
    expect(host.querySelector('[role="group"][aria-label="Pick the right category"]')).toBeTruthy();
  });

  it("lists the matches and marks the one the listing has", () => {
    const calls = [];
    render(stub(calls, {}, SUGGESTIONS));
    act(() => line().click());
    const rows = [...host.querySelectorAll('[role="group"] button[aria-pressed]')];
    expect(rows.map((r) => r.getAttribute("aria-pressed"))).toEqual(["true", "false"]);
    act(() => rows[1].click());
    expect(calls).toContainEqual(["choose", "93427"]);
    // Chosen: the picker swaps back out for the line.
    expect(host.querySelector('[role="group"]')).toBeNull();
    expect(line()).toBeTruthy();
  });

  it("searches on the seller's own words when the title's matches miss", () => {
    const calls = [];
    render(stub(calls, {}, SUGGESTIONS));
    act(() => line().click());
    const box = host.querySelector('input[aria-label="Search eBay categories"]');
    act(() => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")
        .set.call(box, "vintage camera");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => {
      box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(calls).toContainEqual(["suggest", "vintage camera"]);
  });

  it("can be closed without choosing", () => {
    render(stub([], {}, SUGGESTIONS));
    act(() => line().click());
    act(() => host.querySelector('button[aria-label="Close category picker"]').click());
    expect(host.querySelector('[role="group"]')).toBeNull();
    expect(line().textContent).toContain("Athletic Shoes");
  });
});
