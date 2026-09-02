/**
 * A listing eBay refused has to say so on the card holding the fields it
 * refused over.
 *
 * The card was where the answer lived and the last place a seller could find
 * it. eBay names one aspect out of forty ("Missing required item specific:
 * Sleeve Length"), and that sentence appeared once, in the publish banner at
 * the bottom of a long page. The Item specifics card itself could be sitting
 * collapsed; the aspect in question could be one of the unfilled recommended
 * ones the card hides behind "Show 20 more"; and nothing on the field itself
 * said it was the problem. So the seller re-published the same listing and
 * got the same refusal.
 *
 * Now: the card opens itself on a refusal, stops hiding any of its fields,
 * repeats eBay's complaint where the inputs are, and rings the named ones.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { SpecificsCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const aspect = (name, over = {}) => ({
  name, required: false, mode: "FREE_TEXT", values: [],
  cardinality: "SINGLE", data_type: "STRING", ...over,
});

// One required aspect and twelve recommended ones — four more than the card
// shows before "Show all", which is what puts the last of them out of reach.
const ASPECTS = [
  aspect("Brand", { required: true }),
  ...["Colour", "Style", "Material", "Pattern", "Fit", "Season", "Theme",
      "Neckline", "Closure", "Occasion", "Accents", "Sleeve Length"]
    .map((n) => aspect(n)),
];

/** The slice of useListingForm this card actually reads. */
function stub(over = {}) {
  const specifics = over.item_specifics || [];
  return {
    categoryMeta: { aspects: ASPECTS },
    form: { item_specifics: specifics, brand: over.brand || "" },
    isLive: false,
    completion: { specifics: "todo" },
    fixTarget: null,
    publishResult: null,
    getSpecific: (name) => (specifics.find(
      (s) => s.name.trim().toLowerCase() === name.trim().toLowerCase()
        && (s.value || "").trim()) || {}).value || "",
    getSpecificValues: () => [],
    set: () => {},
    upsertSpecific: () => {},
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
  return () => host.textContent || "";
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

const REFUSAL = {
  error: true,
  issues: [{
    target: "specifics", level: "error",
    title: "Missing required item specific: Sleeve Length",
    fix: "Fill in “Sleeve Length” under Item specifics.",
    fields: ["Sleeve Length"],
  }],
};

describe("the Item specifics card after a refused publish", () => {
  it("repeats eBay's complaint where the fields are", async () => {
    const text = await mount(stub({ publishResult: REFUSAL }));
    expect(text()).toContain("Missing required item specific: Sleeve Length");
    expect(text()).toContain("Fill in “Sleeve Length” under Item specifics.");
  });

  it("stops hiding fields — including the one eBay named", async () => {
    // Sleeve Length is the twelfth recommended aspect and empty, so the card
    // would normally leave it behind "Show 4 more".
    const quiet = await mount(stub());
    expect(quiet()).not.toContain("Sleeve Length");
    await act(async () => { root.unmount(); });
    document.body.innerHTML = "";

    const text = await mount(stub({ publishResult: REFUSAL }));
    expect(text()).toContain("Sleeve Length");
    expect(text()).not.toContain("Show 4 more");
  });

  it("marks the field eBay named, not just the empty required ones", async () => {
    const text = await mount(stub({ publishResult: REFUSAL }));
    expect(text()).toContain("eBay refused this");
    // The base field class carries a `data-[fix=true]:ring-error/25` variant,
    // so the assertion is on the ring this card applies, not on the word.
    const inputs = [...host.querySelectorAll("input, select")];
    const ringed = inputs.filter((el) => el.className.includes("ring-error/70"));
    expect(ringed).toHaveLength(1);
  });

  it("says nothing of the sort when the publish was never refused", async () => {
    const text = await mount(stub({
      publishResult: { published: true, issues: [] },
    }));
    expect(text()).not.toContain("eBay refused this");
    expect(text()).toContain("Show 4 more");
  });

  it("ignores a refusal that belongs to another card", async () => {
    const text = await mount(stub({
      publishResult: {
        error: true,
        issues: [{ target: "weight", level: "error",
                   title: "eBay needs a valid shipping weight", fix: "" }],
      },
    }));
    expect(text()).not.toContain("eBay needs a valid shipping weight");
    expect(text()).toContain("Show 4 more");
  });

  it("doesn't put words in eBay's mouth about a check it never saw", async () => {
    // Same issues, same shape, from OUR pre-publish check. Worth showing the
    // same way; not worth claiming eBay answered.
    const text = await mount(stub({
      publishResult: { preflight: true, error: true, issues: REFUSAL.issues },
    }));
    expect(text()).toContain("Missing required item specific: Sleeve Length");
    expect(text()).toContain("Fix this to publish");
    expect(text()).not.toContain("eBay refused this");
  });

  it("finds the complaint in a multi-marketplace result too", async () => {
    // A publish to several marketplaces keeps each one's issues a level down.
    const text = await mount(stub({
      publishResult: { multi: true, published: false,
                       results: { ebay: { ok: false, issues: REFUSAL.issues } } },
    }));
    expect(text()).toContain("Missing required item specific: Sleeve Length");
  });
});
