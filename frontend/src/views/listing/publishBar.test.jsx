/* The sticky bar, after the Publish card moved into it.
 *
 * The Publish card was the last of ten, at the bottom of the form: the one
 * place a refusal was explained and the one place a seller had to scroll
 * to. The bar is pinned, so the answer to "why isn't this on eBay?" is
 * pinned with it now, inside the same [data-publish-bar] wrapper the
 * reachability check measures (LISTING_REDESIGN.md, "Sticky bar"). The
 * once-a-month actions (Delete, Check, Save to eBay drafts) left for the
 * header's ⋯ menu; the refine prompt arrived as "Ask AI".
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MotionGlobalConfig } from "framer-motion";

import { AppProvider } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { PublishBar } from "./PublishBar";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let host;

function ok(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

/** The slice of useListingForm the bar reads, recording every call. */
function stub(calls, over = {}) {
  return {
    sessionId: "s1",
    isLive: false,
    blockers: [],
    chipTargets: null,
    publishResult: null,
    saveStatus: "saved",
    ebayListingId: "",
    setFixTarget: (t) => calls.push(["fix", t]),
    dismissPublishResult: () => calls.push(["dismiss"]),
    flushSave: async () => calls.push(["flush"]),
    publish: (mode) => calls.push(["publish", mode]),
    refine: async (prompt) => { calls.push(["refine", prompt]); return true; },
    runPreflight: () => calls.push(["preflight"]),
    endListing: () => calls.push(["end"]),
    collect: () => ({}),
    ...over,
  };
}

async function render(w) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><PublishBar w={w} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return host;
}

const bar = () => host.querySelector("[data-publish-bar]");
const buttons = () => [...bar().querySelectorAll("button")]
  .map((b) => (b.textContent || "").trim() || b.getAttribute("aria-label"));

let skipping;
beforeEach(() => {
  skipping = MotionGlobalConfig.skipAnimations;
  MotionGlobalConfig.skipAnimations = true;
  vi.stubGlobal("fetch", vi.fn(() => ok({})));
  vi.stubGlobal("requestAnimationFrame", (fn) => { fn(); return 1; });
});

afterEach(() => {
  MotionGlobalConfig.skipAnimations = skipping;
  if (root) act(() => root.unmount());
  host?.remove();
  root = null;
  host = null;
  vi.unstubAllGlobals();
});

describe("the bar", () => {
  it("holds Done, Publish and Ask AI — and none of the once-a-month actions", async () => {
    await render(stub([]));
    const names = buttons();
    expect(names).toContain("Done");
    expect(names.some((n) => /^Publish/.test(n))).toBe(true);
    expect(names).toContain("Ask AI");
    for (const gone of ["Delete", "Check", "Save Draft", "Cancel"]) {
      expect(names.some((n) => n && n.includes(gone)), gone).toBe(false);
    }
  });

  it("flips to Update / End / Cancel on a live listing", async () => {
    await render(stub([], { isLive: true, ebayListingId: "123" }));
    const names = buttons();
    expect(names).toContain("Update Live Listing");
    expect(names).toContain("End");
    expect(names).toContain("Cancel");
    expect(names).not.toContain("Done");
  });
});

describe("Ask AI", () => {
  it("keeps the refine prompt mounted while shut, and opens it on the button", async () => {
    const calls = [];
    await render(stub(calls));
    const input = () => bar().querySelector('[aria-label="Refine listing with AI"]');
    // Mounted, hidden: the app's own tests find the editor by this input.
    expect(input()).toBeTruthy();
    expect(input().closest("[data-ask-ai]").className).toContain("hidden");

    await act(async () => {
      bar().querySelector('[aria-label="Ask AI to change the listing"]').click();
    });
    expect(input().closest("[data-ask-ai]").className).not.toContain("hidden");

    await act(async () => {
      Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")
        .set.call(input(), "make the title punchier");
      input().dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      input().dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(calls).toContainEqual(["refine", "make the title punchier"]);
    // Applied: the prompt clears and the row folds away again.
    expect(input().value).toBe("");
    expect(input().closest("[data-ask-ai]").className).toContain("hidden");
  });
});

describe("the result panel", () => {
  const REFUSED = {
    error: true, published: false,
    message: "eBay couldn't publish this yet",
    issues: [
      { target: "specifics", title: "Sleeve Length is required",
        fix: "Fill in “Sleeve Length” under Item specifics." },
      { target: "generic", title: "Something account-wide" },
    ],
    detail: "Error 21916586",
  };

  it("rides inside the bar, with a Fix button that jumps to the section", async () => {
    const calls = [];
    await render(stub(calls, { publishResult: REFUSED }));
    const panel = bar().querySelector("[data-publish-result]");
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain("Sleeve Length is required");
    expect(panel.textContent).toContain("eBay's exact message");
    // One Fix button: the generic issue has no section to jump to.
    const fixes = [...panel.querySelectorAll("button")]
      .filter((b) => b.textContent.includes("Fix this"));
    expect(fixes).toHaveLength(1);
    await act(async () => { fixes[0].click(); });
    // Cleared then re-set, so a section already flagged scrolls again.
    expect(calls).toEqual([["fix", null], ["fix", "specifics"]]);
  });

  it("can be closed once read", async () => {
    const calls = [];
    await render(stub(calls, { publishResult: REFUSED }));
    await act(async () => {
      bar().querySelector('button[aria-label="Close the publish result"]').click();
    });
    expect(calls).toContainEqual(["dismiss"]);
  });

  it("shows a dry run's payload and a passed check", async () => {
    await render(stub([], { publishResult: {
      dry_run: true, message: "Dry run complete", payload: { Item: { Title: "x" } },
    } }));
    const panel = bar().querySelector("[data-publish-result]");
    expect(panel.textContent).toContain("Dry run complete");
    expect(panel.textContent).toContain("View the exact eBay API payload");
    expect(panel.className).toContain("border-success");
  });

  it("draws nothing with nothing to report", async () => {
    await render(stub([]));
    expect(bar().querySelector("[data-publish-result]")).toBeNull();
  });
});
