import { useEffect } from "react";

/* The editor's keyboard shortcuts (LISTING_REDESIGN.md, "Keyboard").
 *
 * One, for now: Ctrl/⌘+Enter publishes -- the same call the bar's primary
 * button makes (Publish Live on a draft, Update Live Listing on a live
 * one), so a seller who has just finished typing the last chip can send
 * the listing without reaching for the mouse. Tab order is the page order
 * and needs no help; Escape belongs to whatever is open (the chip editor,
 * the menu) and is handled there.
 *
 * It stands down while the AI is busy -- a publish is already running, or
 * a refine is -- and while a dialog is up, so Enter in a confirm never
 * doubles as a publish. */
export const PUBLISH_SHORTCUT = typeof navigator !== "undefined"
  && /Mac|iPhone|iPad/.test(navigator.platform || "") ? "⌘↩" : "Ctrl+↩";

export function useEditorShortcuts(w) {
  const { publish, aiBusy } = w;
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey)) return;
      if (aiBusy) return;
      if (document.querySelector('[role="dialog"]')) return;
      e.preventDefault();
      publish("live");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [publish, aiBusy]);
}
