/* A toast is an announcement. It must not take a tap meant for the app.
 *
 * Toasts are drawn above everything (z-60) and anchored to an edge of the
 * screen, so what they cover is decided by wherever the app happens to have
 * put its buttons — and the app's most important button, the editor's
 * "Publish Live", is pinned to the bottom of the screen. Every toast used to
 * be one big click target that dismissed itself, which turned an eBay
 * rejection into an 8-second dead zone over that button: the seller tapped
 * Publish, the tap dismissed the toast, nothing was published, and the message
 * explaining why went with it. Publishing the same draft from its card in the
 * drafts grid worked, because that button is in the page flow.
 *
 * These pin the behaviour; scripts/reach.mjs proves the consequence in a real
 * browser, where pointer-events and layout actually exist.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ToastProvider, useToast } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
let api;

// The live toast API, handed back to the test rather than assigned from
// inside the component (see store.logout.test.jsx for the same idiom).
function Probe({ onValue }) {
  const value = useToast();
  useEffect(() => { onValue(value); });
  return null;
}

beforeEach(async () => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><Probe onValue={(v) => { api = v; }} /></ToastProvider>,
    );
  });
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  document.body.innerHTML = "";
});

const panel = () => document.querySelector('[role="alert"], [role="status"]');

describe("a toast and the buttons underneath it", () => {
  it("shows what happened", async () => {
    await act(async () => { api.toast("That didn't go live.", { kind: "error" }); });
    expect(panel()).toBeTruthy();
    expect(panel().textContent).toContain("That didn't go live.");
  });

  it("does not swallow the tap: the panel itself is not interactive", async () => {
    await act(async () => { api.toast("That didn't go live.", { kind: "error" }); });
    // The class is the mechanism: the stack is pointer-events-none, so only a
    // child that opts back in can take a tap. If the panel opts in, every
    // pixel of it is a control again and the button behind it is unreachable.
    expect(panel().className).not.toContain("pointer-events-auto");
  });

  it("keeps one control, and only one, for getting rid of it", async () => {
    await act(async () => { api.toast("That didn't go live.", { kind: "error" }); });
    const controls = [...panel().querySelectorAll("button")];
    expect(controls).toHaveLength(1);
    const [close] = controls;
    expect(close.getAttribute("aria-label")).toBe("Dismiss");
    // The one thing in the stack that opts back into taking a tap. That it
    // actually dismisses — and that a tap anywhere else reaches the page
    // behind it — is asserted in a real browser by scripts/reach.mjs, because
    // jsdom has neither pointer-events nor a paint loop to animate the exit.
    expect(close.className).toContain("pointer-events-auto");
  });
});
