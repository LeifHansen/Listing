/* The two answers to a conflict have to be tellable apart.
 *
 * The banner asks the most consequential question in the sync flow: the
 * seller and eBay both changed a field, the merge sent NEITHER value, and
 * whichever they pick is what eventually reaches their live listing. Both
 * buttons read "Keep this".
 *
 * On screen that works, because each sits beside the value it keeps. To
 * anything that reads the page by its controls -- a screen reader, voice
 * control, a keyboard user tabbing without the surrounding text -- it is
 * "Keep this, Keep this", on a choice where picking the wrong one overwrites
 * a fix the seller made in Seller Hub with a stale copy from here.
 *
 * The visible label stays short. The accessible name is what changes.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ToastProvider } from "@/components/ui/Toaster";
import { ConflictBanner } from "@/views/listing/ConflictBanner";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const CONFLICTS = [{
  field: "title",
  label: "Title",
  mine: "Vintage denim jacket",
  ebay: "Vintage Levis denim jacket 1980s",
}];

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(conflicts = CONFLICTS) {
  act(() => {
    root.render(
      <ToastProvider>
        <ConflictBanner conflicts={conflicts} sessionId="s1" />
      </ToastProvider>,
    );
  });
  return [...host.querySelectorAll("button")];
}

function accessibleName(el) {
  return (el.getAttribute("aria-label") || el.textContent || "").trim();
}

describe("answering a conflict", () => {
  it("names which version each button keeps", () => {
    const names = render().map(accessibleName);
    expect(names.length).toBe(2);
    expect(new Set(names).size).toBe(2);
    expect(names.join(" | ").toLowerCase()).toMatch(/your/);
    expect(names.join(" | ").toLowerCase()).toMatch(/ebay/);
  });

  it("keeps the label short on screen", () => {
    // The distinguishing part is the accessible name, not the rendered text:
    // two of these sit inside a card and a sentence each would crowd out the
    // values they are asking about.
    for (const b of render()) {
      expect(b.textContent.trim().length).toBeLessThan(24);
    }
  });

  it("says nothing at all when there is nothing held back", () => {
    expect(render([]).length).toBe(0);
  });

  it("distinguishes them per field, not just per side", () => {
    // Two conflicts means four buttons, and "keep yours" twice is the same
    // ambiguity one level up.
    const names = render([
      CONFLICTS[0],
      { field: "price", label: "Price", mine: "48.00", ebay: "52.00" },
    ]).map(accessibleName);
    expect(names.length).toBe(4);
    expect(new Set(names).size).toBe(4);
  });
});
