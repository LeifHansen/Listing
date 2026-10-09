/* The description: three lines and "Edit", until the seller wants it all.
 *
 * The AI drafts several hundred words that most sellers never change; an
 * 18-row box made it the tallest thing on the page (LISTING_REDESIGN.md,
 * "Description"). It opens itself when a refusal or a fix-it target names
 * the description, because then the seller has to be in it.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ToastProvider } from "@/components/ui/Toaster";
import { DescriptionCard } from "./cards";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function stub(over = {}, w = {}) {
  return {
    fixTarget: null,
    publishResult: null,
    form: { description: "A long story about a jacket.\\nSecond line.\\nThird.\\nFourth.", ...over },
    completion: { description: "complete" },
    set: () => {},
    ...w,
  };
}

function render(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => { root.render(<ToastProvider><DescriptionCard w={w} /></ToastProvider>); });
  return host;
}

beforeEach(() => {
  // A flagged section scrolls itself into view; jsdom has no scrolling.
  Element.prototype.scrollIntoView = () => {};
});

afterEach(() => {
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the description", () => {
  it("shows a clamped preview and an Edit button, not the box", () => {
    render(stub());
    const preview = host.querySelector("[data-description-preview]");
    expect(preview).toBeTruthy();
    expect(preview.className).toContain("line-clamp-3");
    expect(host.querySelector("textarea")).toBeNull();
    expect(host.querySelector('button[aria-label="Edit the description"]')).toBeTruthy();
  });

  it("opens the box on Edit, and folds it again on Collapse", () => {
    render(stub());
    act(() => host.querySelector('button[aria-label="Edit the description"]').click());
    expect(host.querySelector("textarea")).toBeTruthy();
    const collapse = [...host.querySelectorAll("button")]
      .find((b) => b.textContent.includes("Collapse"));
    act(() => collapse.click());
    expect(host.querySelector("textarea")).toBeNull();
  });

  it("offers to write one when there is none", () => {
    render(stub({ description: "" }));
    expect(host.textContent).toContain("No description yet");
    expect(host.querySelector('button[aria-label="Write a description"]')).toBeTruthy();
  });

  it("opens itself when the description is what eBay named", () => {
    render(stub({}, { fixTarget: "description" }));
    expect(host.querySelector("textarea")).toBeTruthy();
  });

  it("opens itself on a refusal that names it", () => {
    render(stub({}, { publishResult: { error: true, issues: [
      { target: "description", title: "Description contains a phone number" },
    ] } }));
    expect(host.querySelector("textarea")).toBeTruthy();
  });
});
