/**
 * The category's fields are the ones for the category the listing HAS.
 *
 * Picking a category asks eBay for its conditions and item specifics, and
 * those answers took whatever order the network gave them. Pick one
 * category, think better of it and pick another, and a slow answer for the
 * first landed last: the specifics (and what blocks a publish) were the
 * abandoned category's, and the condition was re-fitted to its list -- on
 * the listing, where the next save took it.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { useListingForm } from "./useListingForm";

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

// What eBay says about each category. "slow" answers only when released.
const META = {
  slow: {
    conditions: [{ enum: "NEW", id: "1000", label: "New" }],
    aspects: [{ name: "Inseam", required: true, values: [] }],
  },
  fast: {
    conditions: [{ enum: "USED_GOOD", id: "3000", label: "Used" }],
    aspects: [{ name: "Shoe Size", required: true, values: [] }],
  },
};

let root;
let host;
let release;

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function settle() {
  for (let i = 0; i < 3; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

describe("choosing a category twice", () => {
  it("keeps the later choice's fields when the earlier answer lands last", async () => {
    const held = [];
    release = () => held.splice(0).forEach((go) => go());
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      const path = String(url);
      if (path.includes("/api/health")) return ok({ taxonomy_configured: true });
      const kind = path.includes("/api/item-conditions") ? "conditions"
        : path.includes("/api/item-aspects") ? "aspects" : null;
      if (!kind) return ok({});
      const cid = JSON.parse(opts.body).category_id;
      const answer = () => ok({ [kind]: META[cid][kind], checked: true });
      if (cid === "slow") return new Promise((resolve) => { held.push(() => resolve(answer())); });
      return answer();
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
    await settle();
    await act(async () => {
      value.app.setSession({ sessionId: "s1", status: "draft",
        listing: { title: "Sneakers", condition: "USED_GOOD", images: ["img_000.jpg"] } });
    });
    await settle();

    await act(async () => { value.form.chooseCategory({ category_id: "slow", path: "Jeans" }); });
    await act(async () => { value.form.chooseCategory({ category_id: "fast", path: "Sneakers" }); });
    await settle();
    expect(value.form.categoryMeta.aspects.map((a) => a.name)).toEqual(["Shoe Size"]);

    await act(async () => { release(); });
    await settle();
    expect(value.form.form.category_id).toBe("fast");
    expect(value.form.categoryMeta.aspects.map((a) => a.name)).toEqual(["Shoe Size"]);
    expect(value.form.form.condition).toBe("USED_GOOD");
  });
});
