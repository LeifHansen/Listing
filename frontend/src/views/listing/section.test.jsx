/* A section keeps the two things the editor depends on: a flag opens and
 * scrolls it, and "attention" is said in words. The card chrome is gone. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Section } from "./Section";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;
function render(el) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(el));
}
afterEach(() => { act(() => root.unmount()); host.remove(); });

describe("Section", () => {
  it("keeps the anchor the publish bar jumps to, and no chip but the one that matters", () => {
    render(<Section id="title" title="Title" state="complete"><input /></Section>);
    expect(host.querySelector("#card-title")).not.toBeNull();
    expect(host.textContent).not.toContain("Complete");
    expect(host.textContent).not.toContain("Optional");
    render(<Section id="price" title="Price" state="attention"><input /></Section>);
    expect(host.textContent).toContain("Blocks publish");
  });

  it("scrolls into view when flagged", () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    render(<Section id="title" title="Title" flagged><input /></Section>);
    expect(scroll).toHaveBeenCalled();
  });

  it("a collapsible section opens itself when expanded, and keeps its fields otherwise", () => {
    render(<Section id="description" title="Description" collapsible defaultOpen={false}>
      <textarea data-body />
    </Section>);
    expect(host.querySelector("[data-body]")).toBeNull();
    const button = host.querySelector("button[aria-expanded]");
    expect(button.getAttribute("aria-expanded")).toBe("false");
    act(() => button.click());
    expect(host.querySelector("[data-body]")).not.toBeNull();
    // Not collapsible: always open, no chevron button.
    render(<Section id="t" title="T"><input data-body /></Section>);
    expect(host.querySelector("[data-body]")).not.toBeNull();
    expect(host.querySelector("button[aria-expanded]")).toBeNull();
  });
});
