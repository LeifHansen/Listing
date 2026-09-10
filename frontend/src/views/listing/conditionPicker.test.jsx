/**
 * A trading card's condition, asked in two steps.
 *
 * In the single-card categories eBay's condition is "Graded" or "Ungraded",
 * and each REQUIRES a second answer: the grading service + grade (+ an
 * optional certification number) for a graded card, the card condition on
 * eBay's ladder for an ungraded one. A listing that stops at the first answer
 * is refused outright — which is what every card listed through the old
 * one-dropdown Condition field came back as.
 *
 * These pin the picker: one dropdown where eBay asks one question, the second
 * step where eBay asks two, drawn from eBay's own answer for the category
 * (names, ladders and ids), and the second answer dropped the moment the
 * first changes so a card switched to Ungraded never goes out carrying a PSA
 * grade eBay would refuse.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConditionPicker } from "./ConditionPicker";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// eBay's answer for Sports Trading Card Singles (261328), as
// /api/item-conditions hands it to the browser.
const GRADED = {
  enum: "LIKE_NEW", id: "2750", label: "Graded",
  descriptors: [
    { id: "27501", name: "Professional Grader", required: true, free_text: false,
      cardinality: "SINGLE",
      values: [{ id: "275010", name: "Professional Sports Authenticator (PSA)" },
        { id: "275013", name: "Beckett Grading Services (BGS)" }] },
    { id: "27502", name: "Grade", required: true, free_text: false, cardinality: "SINGLE",
      values: [{ id: "275020", name: "10" }, { id: "275021", name: "9.5" }] },
    { id: "27503", name: "Certification Number", required: false, free_text: true,
      max_length: 30, values: [] },
  ],
};
const UNGRADED = {
  enum: "USED_VERY_GOOD", id: "4000", label: "Ungraded",
  descriptors: [
    { id: "40001", name: "Card Condition", required: true, free_text: false,
      cardinality: "SINGLE",
      values: [{ id: "400010", name: "Near Mint or Better" }, { id: "400011", name: "Excellent" },
        { id: "400012", name: "Very Good" }, { id: "400013", name: "Poor" }] },
  ],
};
const CARDS = [GRADED, UNGRADED];

// ...and for a t-shirt: one question.
const APPAREL = [
  { enum: "NEW", id: "1000", label: "New with tags", descriptors: [] },
  { enum: "USED_EXCELLENT", id: "3000", label: "Pre-owned - Good", descriptors: [] },
];

let root;
let host;

async function mount(props) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  const render = async (p) => {
    await act(async () => {
      root.render(<div className="grid">
        <ConditionPicker labels {...p} />
      </div>);
    });
  };
  await render(props);
  return { host, render };
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

const selects = () => [...host.querySelectorAll("select")];
const selectLabelled = (label) => selects().find(
  (s) => s.closest("label")?.textContent.startsWith(label));
const choose = (el, value) => {
  Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set.call(el, value);
  el.dispatchEvent(new Event("change", { bubbles: true }));
};
const typeInto = (el, text) => {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set.call(el, text);
  el.dispatchEvent(new Event("input", { bubbles: true }));
};

describe("where eBay asks one question", () => {
  it("is the one dropdown it always was", async () => {
    await mount({ conditions: APPAREL, condition: "USED_EXCELLENT", descriptors: [],
      onChange: () => {} });
    expect(selects()).toHaveLength(1);
    expect([...selects()[0].options].map((o) => o.textContent))
      .toEqual(["New with tags", "Pre-owned - Good"]);
    expect(host.querySelector("input")).toBeNull();
  });

  it("falls back to the generic list, and says so, when eBay could not be asked", async () => {
    await mount({ conditions: null, checked: false, condition: "USED_GOOD", descriptors: [],
      onChange: () => {} });
    expect(selects()[0].value).toBe("USED_GOOD");
    // The help hides behind the ⓘ beside the label (fields.InfoTip).
    expect(host.querySelector("[title]").getAttribute("title")).toContain("couldn’t check");
  });
});

describe("where eBay asks two", () => {
  it("asks Graded or Ungraded first, in eBay's words", async () => {
    await mount({ conditions: CARDS, condition: "USED_VERY_GOOD", descriptors: [],
      onChange: () => {} });
    const step1 = selectLabelled("Condition");
    expect([...step1.options].map((o) => o.textContent)).toEqual(["Graded", "Ungraded"]);
    expect(host.textContent).toContain("Graded or Ungraded?");
  });

  it("offers eBay's card-condition ladder for an ungraded card", async () => {
    await mount({ conditions: CARDS, condition: "USED_VERY_GOOD", descriptors: [],
      onChange: () => {} });
    const ladder = selectLabelled("Card Condition");
    expect(ladder).toBeTruthy();
    expect([...ladder.options].map((o) => o.textContent)).toEqual([
      "Choose…", "Near Mint or Better", "Excellent", "Very Good", "Poor"]);
    expect(ladder.value).toBe("");
  });

  it("offers grading service, grade and certification number for a graded card", async () => {
    await mount({ conditions: CARDS, condition: "LIKE_NEW", descriptors: [],
      onChange: () => {} });
    expect(selectLabelled("Professional Grader")).toBeTruthy();
    expect(selectLabelled("Grade")).toBeTruthy();
    const cert = host.querySelector("input");
    expect(cert.closest("label").textContent).toContain("Certification Number");
    expect(cert.closest("label").textContent).toContain("optional");
    expect(cert.maxLength).toBe(30);
  });

  it("answers the second step with eBay's ids and wording", async () => {
    const onChange = vi.fn();
    await mount({ conditions: CARDS, condition: "LIKE_NEW", descriptors: [], onChange });
    await act(async () => { choose(selectLabelled("Professional Grader"), "275010"); });
    expect(onChange).toHaveBeenLastCalledWith({
      condition: "LIKE_NEW",
      condition_descriptors: [{ id: "27501", values: ["275010"], text: "",
        label: "Professional Grader",
        value_labels: ["Professional Sports Authenticator (PSA)"] }],
    });
  });

  it("keeps the answers in eBay's order whatever order they were given in", async () => {
    const onChange = vi.fn();
    await mount({ conditions: CARDS, condition: "LIKE_NEW",
      descriptors: [{ id: "27502", values: ["275020"] }], onChange });
    await act(async () => { choose(selectLabelled("Professional Grader"), "275013"); });
    expect(onChange.mock.calls[0][0].condition_descriptors.map((d) => d.id))
      .toEqual(["27501", "27502"]);
  });

  it("takes the certification number as free text", async () => {
    const onChange = vi.fn();
    await mount({ conditions: CARDS, condition: "LIKE_NEW",
      descriptors: [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["275020"] }],
      onChange });
    await act(async () => { typeInto(host.querySelector("input"), " 12345678 "); });
    const sent = onChange.mock.calls[0][0].condition_descriptors;
    expect(sent.find((d) => d.id === "27503")).toEqual({
      id: "27503", values: [], text: "12345678", label: "Certification Number",
      value_labels: [] });
  });

  it("shows the answers a saved listing already has", async () => {
    await mount({ conditions: CARDS, condition: "LIKE_NEW",
      descriptors: [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["275021"] },
        { id: "27503", text: "998877" }],
      onChange: () => {} });
    expect(selectLabelled("Professional Grader").value).toBe("275010");
    expect(selectLabelled("Grade").value).toBe("275021");
    expect(host.querySelector("input").value).toBe("998877");
  });
});

describe("changing the first answer", () => {
  it("drops a grade that no longer applies — Ungraded never carries PSA 10", async () => {
    const onChange = vi.fn();
    await mount({ conditions: CARDS, condition: "LIKE_NEW",
      descriptors: [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["275020"] }],
      onChange });
    await act(async () => { choose(selectLabelled("Condition"), "USED_VERY_GOOD"); });
    expect(onChange).toHaveBeenLastCalledWith({
      condition: "USED_VERY_GOOD", condition_descriptors: [] });
  });

  it("drops the second step entirely on a category with one question", async () => {
    const onChange = vi.fn();
    await mount({ conditions: APPAREL, condition: "USED_EXCELLENT",
      descriptors: [{ id: "27501", values: ["275010"] }], onChange });
    await act(async () => { choose(selectLabelled("Condition"), "NEW"); });
    expect(onChange).toHaveBeenLastCalledWith({ condition: "NEW", condition_descriptors: [] });
    expect(selects()).toHaveLength(1);
  });
});

describe("the ring on what is actually wrong", () => {
  it("rings the unanswered second step, not the first", async () => {
    await mount({ conditions: CARDS, condition: "LIKE_NEW",
      descriptors: [{ id: "27501", values: ["275010"] }], fixLevel: "warn",
      onChange: () => {} });
    expect(selectLabelled("Condition").dataset.fix).toBeUndefined();
    expect(selectLabelled("Professional Grader").dataset.fix).toBeUndefined();
    expect(selectLabelled("Grade").dataset.fix).toBe("warn");
    // Optional, so never amber.
    expect(host.querySelector("input").dataset.fix).toBeUndefined();
  });

  it("rings the first step when the condition itself is refused", async () => {
    await mount({ conditions: CARDS, condition: "USED_GOOD", descriptors: [],
      fixLevel: "warn", onChange: () => {} });
    expect(selectLabelled("Condition").dataset.fix).toBe("warn");
  });

  it("rings nothing when nothing blocks", async () => {
    await mount({ conditions: CARDS, condition: "USED_VERY_GOOD",
      descriptors: [{ id: "40001", values: ["400010"] }], onChange: () => {} });
    expect(selects().every((s) => s.dataset.fix === undefined)).toBe(true);
  });
});

describe("on a bulk card", () => {
  it("names each control for a screen reader and in its placeholder", async () => {
    await mount({ labels: false, conditions: CARDS, condition: "USED_VERY_GOOD",
      descriptors: [], onChange: () => {} });
    expect(host.querySelector("label")).toBeNull();
    const [step1, ladder] = selects();
    expect(step1.getAttribute("aria-label")).toBe("Condition — Graded or Ungraded?");
    expect(ladder.getAttribute("aria-label")).toBe("Card Condition");
    expect(ladder.options[0].textContent).toBe("Card Condition…");
  });
});
