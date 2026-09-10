/**
 * The editor holds a trading card's second answer, and keeps it honest.
 *
 * useListingForm is where the category's condition list arrives and where
 * the listing's condition is fitted to it. With descriptors that job grows:
 * the second answer has to follow the first (a card fitted onto "Ungraded"
 * cannot keep a PSA grade), an imported card's bare ids pick up eBay's
 * wording, and a graded card with no grade has to ring amber on the
 * condition — from the same blocker list every other surface reads.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// eBay's answer for Sports Trading Card Singles, as /api/item-conditions
// hands it over.
const CARDS = [
  { enum: "LIKE_NEW", id: "2750", label: "Graded", descriptors: [
    { id: "27501", name: "Professional Grader", required: true, free_text: false,
      cardinality: "SINGLE",
      values: [{ id: "275010", name: "Professional Sports Authenticator (PSA)" }] },
    { id: "27502", name: "Grade", required: true, free_text: false, cardinality: "SINGLE",
      values: [{ id: "275020", name: "10" }] },
    { id: "27503", name: "Certification Number", required: false, free_text: true,
      max_length: 30, values: [] },
  ] },
  { enum: "USED_VERY_GOOD", id: "4000", label: "Ungraded", descriptors: [
    { id: "40001", name: "Card Condition", required: true, free_text: false,
      cardinality: "SINGLE",
      values: [{ id: "400010", name: "Near Mint or Better" }, { id: "400013", name: "Poor" }] },
  ] },
];

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

function Probe({ onValue }) {
  const app = useApp();
  const form = useListingForm();
  useEffect(() => { onValue({ app, form }); });
  return null;
}

let root;
let host;

/** The editor holding `listing`, with eBay answering the category's
 *  condition lookup with the trading-card list. */
async function mountEditor(listing) {
  vi.stubGlobal("fetch", vi.fn((url) => {
    if (String(url).includes("/api/item-conditions")) {
      return ok({ conditions: CARDS, checked: true });
    }
    if (String(url).includes("/api/health")) {
      return ok({ taxonomy_configured: true });
    }
    return ok({});
  }));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider>
        <Probe onValue={(v) => { value = v; }} />
      </AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => {
    value.app.setSession({ sessionId: "s1", listing, status: "draft" });
  });
  // The condition lookup lands after a round trip.
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return () => value.form;
}

const CARD = {
  images: ["img_000.jpg"],
  title: "2003 Topps Chrome LeBron James Rookie #111",
  category_id: "261328",
  price: 1499,
  quantity: 1,
  package_weight_lb: 1,
  package_weight_oz: 0,
};

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("a trading card in the editor", () => {
  it("carries the second answer through to what is saved, in eBay's words", async () => {
    // An imported card holds ids only; the editor gives them eBay's wording.
    const get = await mountEditor({ ...CARD, condition: "LIKE_NEW",
      condition_descriptors: [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["275020"] }] });
    expect(get().categoryMeta.conditions.map((c) => c.label)).toEqual(["Graded", "Ungraded"]);
    const saved = get().collect();
    expect(saved.condition).toBe("LIKE_NEW");
    expect(saved.condition_descriptors.map((d) => [d.id, d.values[0], d.value_labels[0]]))
      .toEqual([["27501", "275010", "Professional Sports Authenticator (PSA)"],
        ["27502", "275020", "10"]]);
  });

  it("drops a grade the fitted condition does not take", async () => {
    // A draft graded "Used" by the AI lands on Ungraded (the nearest grade
    // eBay offers) — and cannot keep a PSA 10 left on it.
    const get = await mountEditor({ ...CARD, condition: "USED_EXCELLENT",
      condition_descriptors: [{ id: "27501", values: ["275010"] }, { id: "27502", values: ["275020"] }] });
    expect(get().form.condition).toBe("USED_VERY_GOOD");
    expect(get().form.condition_descriptors).toEqual([]);
  });

  it("rings the condition on a graded card with no grade, and clears it once answered", async () => {
    const get = await mountEditor({ ...CARD, condition: "LIKE_NEW",
      condition_descriptors: [{ id: "27501", values: ["275010"] }] });
    expect(get().fixLevel("condition")).toBe("warn");
    expect(get().blockers.map((b) => b.label)).toEqual(["Grade"]);
    await act(async () => {
      get().set("condition_descriptors", [
        { id: "27501", values: ["275010"] }, { id: "27502", values: ["275020"] }]);
    });
    expect(get().fixLevel("condition")).toBeUndefined();
    expect(get().blockers).toEqual([]);
  });

  it("leaves a card that is not in a card category alone", async () => {
    const get = await mountEditor({ ...CARD, category_id: "", condition: "USED_GOOD" });
    expect(get().form.condition).toBe("USED_GOOD");
    expect(get().form.condition_descriptors).toEqual([]);
  });
});
