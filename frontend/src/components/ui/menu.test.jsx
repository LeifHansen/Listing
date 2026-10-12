/* The ⋯ menu: the one popover the app has, and the three ways it closes.
 *
 * The editor's header carried five buttons and its bar four more, most of
 * them pressed once a month; they are one menu now (LISTING_REDESIGN.md,
 * "Header"). A menu that stays open over the form is a modal with no scrim,
 * so what these pin is the closing: a click anywhere outside, Escape, and
 * choosing an item all shut it -- and a screen reader hears a menu.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { MotionGlobalConfig } from "framer-motion";
import { Menu } from "./Menu";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;
let picked;

function render(items) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => {
    root.render(
      <div>
        <button type="button" id="elsewhere">elsewhere</button>
        <Menu label="More actions" items={items} />
      </div>,
    );
  });
  return host;
}

const ITEMS = () => [
  { label: "New listing", onSelect: () => picked.push("new") },
  { label: "Check with eBay", onSelect: () => picked.push("check") },
  null,
  { label: "Delete listing", danger: true, divider: true, onSelect: () => picked.push("delete") },
];

const trigger = () => host.querySelector('button[aria-haspopup="menu"]');
const menu = () => host.querySelector('[role="menu"]');
const items = () => [...host.querySelectorAll('[role="menuitem"]')];
const open = () => act(() => trigger().click());

let skipping;
beforeEach(() => {
  skipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
  picked = [];
});

afterEach(() => {
  MotionGlobalConfig.skipAnimations = skipping;
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
});

describe("the ⋯ menu", () => {
  it("is shut until pressed, and says so to a screen reader", () => {
    render(ITEMS());
    expect(menu()).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    open();
    expect(menu()).toBeTruthy();
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
    // Every entry is a menu item; a null entry (an action this listing
    // does not have) draws nothing.
    expect(items().map((b) => b.textContent)).toEqual(
      ["New listing", "Check with eBay", "Delete listing"]);
  });

  it("runs the item and shuts on a choice", () => {
    render(ITEMS());
    open();
    act(() => items()[1].click());
    expect(picked).toEqual(["check"]);
    expect(menu()).toBeNull();
  });

  it("shuts on a click anywhere outside it", () => {
    render(ITEMS());
    open();
    act(() => {
      document.getElementById("elsewhere")
        .dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(menu()).toBeNull();
    expect(picked).toEqual([]);
  });

  it("shuts on Escape", () => {
    render(ITEMS());
    open();
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(menu()).toBeNull();
  });

  it("lands focus on the first item and walks the list with the arrow keys", () => {
    render(ITEMS());
    open();
    expect(document.activeElement).toBe(items()[0]);
    act(() => {
      menu().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    expect(document.activeElement).toBe(items()[1]);
    act(() => {
      menu().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    });
    expect(document.activeElement).toBe(items()[0]);
    // Wraps: up from the first lands on the last.
    act(() => {
      menu().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true }));
    });
    expect(document.activeElement).toBe(items()[2]);
  });

  it("marks a dangerous action and leaves a disabled one unpressable", () => {
    render([
      { label: "Delete listing", danger: true, onSelect: () => picked.push("delete") },
      { label: "Duplicate", disabled: true, onSelect: () => picked.push("dup") },
    ]);
    open();
    expect(items()[0].className).toContain("text-error");
    expect(items()[1].disabled).toBe(true);
    act(() => items()[1].click());
    expect(picked).toEqual([]);
  });
});
