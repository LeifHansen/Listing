/**
 * A field that is keeping the listing off eBay says so, in colour, whenever
 * the seller is looking at it.
 *
 * The editor already knew: `blockers` is recomputed on every render and is
 * the app's single definition of "eBay will refuse this". But the colour on
 * the inputs was wired to `fixTarget` instead — state a publish attempt sets
 * inside this component, and which dies with it. So a seller whose publish
 * failed in the bulk queue, or who came back to the draft later, opened a
 * form with nothing marked and no way to tell which field was missing. Their
 * words: "I can NOT see what the missing field is when going to edit."
 *
 * Two levels, the pair the item-specifics grid has drawn all along. Amber is
 * our reading of eBay's rules — required and still empty — and reads as the
 * prediction it is. Red is the marketplace or the server naming the field in
 * a refusal, and outranks it. Only fields that actually block get either:
 * a list that mixes "eBay will refuse this" with "this would sell better" is
 * what made the old UI unreadable, and the same is true of colour.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";
import { PricingCard, TitleCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

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

/** The editor, holding `listing`. Returns a getter for its live hook value.
 *  `status` is the session's, which is what decides whether this listing is
 *  already up on eBay (and so which publish contract it is held to). */
async function mountEditor(listing, status = "draft") {
  vi.stubGlobal("fetch", vi.fn(() => ok({})));
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
    value.app.setSession({ sessionId: "s1", listing, status });
  });
  return () => value.form;
}

/** A draft that would publish: nothing in it blocks eBay. */
const READY = {
  images: ["img_000.jpg"],
  title: "Nike Air Max 90 Men's 10.5 White Leather Sneakers",
  category_id: "15709",
  condition: "USED_EXCELLENT",
  price: 48,
  quantity: 1,
  package_weight_lb: 2,
  package_weight_oz: 0,
};

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("colour on the fields that block a publish", () => {
  it("marks a missing field with no publish attempt at all", async () => {
    // The reported case: the seller arrives at the editor, having failed to
    // publish somewhere else entirely. Nothing has set fixTarget here.
    const get = await mountEditor({ ...READY, price: null });
    expect(get().fixTarget).toBe(null);
    expect(get().fixLevel("price")).toBe("warn");
  });

  it("leaves fields that are not blocking alone", async () => {
    const get = await mountEditor({ ...READY, price: null });
    // Only the missing one. A form where everything is amber says nothing.
    expect(get().fixLevel("title")).toBeUndefined();
    expect(get().fixLevel("category")).toBeUndefined();
    expect(get().fixLevel("condition")).toBeUndefined();
    expect(get().fixLevel("weight")).toBeUndefined();
  });

  it("says nothing at all about a draft that would publish", async () => {
    const get = await mountEditor(READY);
    for (const t of ["title", "category", "condition", "price", "weight"]) {
      expect(get().fixLevel(t), t).toBeUndefined();
    }
  });

  it("marks every blocking field, not just the first", async () => {
    // fixTarget could only ever name one. A listing missing three things sent
    // the seller round the publish loop three times to discover them.
    const get = await mountEditor({
      ...READY, price: null, title: "", package_weight_lb: 0, package_weight_oz: 0,
    });
    expect(get().fixLevel("price")).toBe("warn");
    expect(get().fixLevel("title")).toBe("warn");
    expect(get().fixLevel("weight")).toBe("warn");
  });

  it("lets what eBay actually said outrank what we predicted", async () => {
    const get = await mountEditor({ ...READY, price: null });
    await act(async () => { get().setFixTarget("title"); });
    // The refusal names the title: red there, and the price stays amber.
    expect(get().fixLevel("title")).toBe("true");
    expect(get().fixLevel("price")).toBe("warn");
  });

  it("clears as the seller fills the field in", async () => {
    const get = await mountEditor({ ...READY, price: null });
    expect(get().fixLevel("price")).toBe("warn");
    await act(async () => { get().set("price", "48"); });
    expect(get().fixLevel("price")).toBeUndefined();
  });

  it("holds a live listing to the revise contract, not the create one", async () => {
    // eBay never asks for a package weight on a revise, so marking one on a
    // listing that is already up would be inventing a blocker.
    const get = await mountEditor({
      ...READY, ebay_listing_id: "1234567890",
      package_weight_lb: 0, package_weight_oz: 0,
    }, "published");
    expect(get().fixLevel("weight")).toBeUndefined();
  });
});


/* ---------------------------------------------------------------- the DOM

   The hook returning the right level is half of it; the other half is the
   input actually wearing the colour. These mount the real cards with the
   slice of the form they read, and check the attribute the stylesheet hangs
   the amber and the red on (see controlClasses in components/ui/fields).   */

/** The slice of useListingForm these cards read. */
function stub(fixLevel, over = {}) {
  return {
    fixLevel,
    fixTarget: null,
    form: {
      title: "", price: "", quantity: 1, condition: "USED_EXCELLENT",
      listing_format: "FIXED_PRICE", currency: "USD", auction_start_price: "",
      condition_description: "", purchase_price: "", item_specifics: [],
      accept_offers: false, ...over,
    },
    completion: { title: "todo", pricing: "todo" },
    categoryMeta: { aspects: [], conditions: [], conditionsChecked: true },
    comps: null, compsBusy: false, isLive: false, publishResult: null,
    set: () => {}, loadComps: () => {}, suggestTitle: () => {}, aiBusy: false,
    ...(over.w || {}),
  };
}

function render(el) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => { root.render(<ToastProvider>{el}</ToastProvider>); });
  return host;
}

/** data-fix on every control that carries one, in document order. */
function marks(el) {
  return [...el.querySelectorAll("[data-fix]")]
    .map((n) => n.getAttribute("data-fix"));
}

describe("the colour reaches the input", () => {
  it("draws amber on a field our own rules say is required and empty", () => {
    const el = render(<PricingCard w={stub((t) => (t === "price" ? "warn" : undefined))} />);
    expect(marks(el)).toContain("warn");
  });

  it("draws red on a field eBay named", () => {
    const el = render(<TitleCard w={stub((t) => (t === "title" ? "true" : undefined))} />);
    expect(marks(el)).toContain("true");
  });

  it("draws nothing on a card with nothing blocking", () => {
    const el = render(<PricingCard w={stub(() => undefined)} />);
    expect(marks(el)).toEqual([]);
  });

  it("reaches the condition dropdown, which could not be marked at all before", () => {
    const el = render(<PricingCard w={stub((t) => (t === "condition" ? "warn" : undefined))} />);
    const select = el.querySelector("select[data-fix]");
    expect(select).toBeTruthy();
    expect(select.getAttribute("data-fix")).toBe("warn");
  });
});
