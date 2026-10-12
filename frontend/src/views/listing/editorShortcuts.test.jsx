/* Ctrl/⌘+Enter publishes.
 *
 * The one editor shortcut (LISTING_REDESIGN.md, "Keyboard"): the same call
 * the bar's primary button makes, so a seller who has just typed the last
 * chip can send the listing without reaching for the mouse. It stands down
 * while the AI is busy and while a dialog is up, so Enter in a confirm never
 * doubles as a publish.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useEditorShortcuts } from "./useEditorShortcuts";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function Host({ w }) {
  useEditorShortcuts(w);
  return <input data-box />;
}

function mount(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => { root.render(<Host w={w} />); });
}

afterEach(() => {
  if (root) act(() => root.unmount());
  document.body.innerHTML = "";
  root = null;
});

const key = (init) => act(() => {
  window.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }));
});

describe("Ctrl/⌘+Enter", () => {
  it("publishes live, with either modifier", async () => {
    const publish = vi.fn();
    mount({ publish, aiBusy: null });
    await key({ key: "Enter", metaKey: true });
    await key({ key: "Enter", ctrlKey: true });
    expect(publish).toHaveBeenCalledTimes(2);
    expect(publish).toHaveBeenCalledWith("live");
  });

  it("does nothing on a bare Enter", async () => {
    const publish = vi.fn();
    mount({ publish, aiBusy: null });
    await key({ key: "Enter" });
    expect(publish).not.toHaveBeenCalled();
  });

  it("stands down while the AI is busy", async () => {
    const publish = vi.fn();
    mount({ publish, aiBusy: ["Publishing to eBay…"] });
    await key({ key: "Enter", metaKey: true });
    expect(publish).not.toHaveBeenCalled();
  });

  it("stands down while a dialog is up", async () => {
    const publish = vi.fn();
    mount({ publish, aiBusy: null });
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    document.body.appendChild(dialog);
    await key({ key: "Enter", metaKey: true });
    expect(publish).not.toHaveBeenCalled();
  });

  it("stops listening once the editor is gone", async () => {
    const publish = vi.fn();
    mount({ publish, aiBusy: null });
    act(() => root.unmount());
    root = null;
    await key({ key: "Enter", metaKey: true });
    expect(publish).not.toHaveBeenCalled();
  });
});
