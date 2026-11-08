/* Background removal is switched on by the seller, once, and that switch is
 * where they agree to their photos going to Photoroom.
 *
 * Photoroom has no way for an app to create an account per seller, so the
 * app cuts on its own account and each seller opts in here instead. That
 * makes this card the consent: nothing may be switched on without a confirm
 * that names who receives the photos, a cancelled confirm must send nothing,
 * and switching it off has to be one press away for as long as it is on.
 *
 * Rendered with react-dom + act, matching dismissDuplicateReminders.test.jsx;
 * the repo has no testing-library dependency.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  bgRemoval: null, user: { id: "u1" }, posts: [], reloads: 0,
  tokens: { enabled: true, costs: { image_ai: 1 } },
}));

vi.mock("@/lib/api", () => ({
  postJson: vi.fn(async (path) => { state.posts.push(path); return { ok: true }; }),
}));

vi.mock("@/store", () => ({
  useApp: () => ({
    user: state.user,
    bgRemoval: state.bgRemoval,
    tokens: state.tokens,
    loadBgRemovalStatus: async () => { state.reloads += 1; },
  }),
}));

const { BackgroundRemovalCard } = await import("./BackgroundRemovalCard");

const OFF = { available: true, active: false, needs_activation: true,
              used: 0, limit: 1000, resets_on: "2026-12-01", sandbox: false };

let root = null;
let host = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<ToastProvider><BackgroundRemovalCard /></ToastProvider>);
  });
}

// The confirm dialog renders into document.body through a portal.
const buttonSaying = (text) => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const press = async (el) => {
  await act(async () => { el.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};
const bodyHasText = (t) => document.body.textContent.includes(t);

beforeEach(() => {
  state.bgRemoval = OFF;
  state.user = { id: "u1" };
  state.posts = [];
  state.reloads = 0;
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

describe("switching background removal on", () => {
  it("asks first, naming Photoroom as who receives the photos", async () => {
    await mount();
    await press(buttonSaying("Turn on background removal"));

    expect(bodyHasText("sent to Photoroom")).toBe(true);
    expect(bodyHasText("Only the photos you choose are sent")).toBe(true);
    expect(state.posts).toEqual([]);
  });

  it("switches it on only once the seller confirms", async () => {
    await mount();
    await press(buttonSaying("Turn on background removal"));
    await press(buttonSaying("Turn on"));

    expect(state.posts).toEqual(["/api/bg-removal/activate"]);
    expect(state.reloads).toBe(1);
  });

  it("sends nothing when the seller backs out", async () => {
    await mount();
    await press(buttonSaying("Turn on background removal"));
    await press(buttonSaying("Cancel"));

    expect(state.posts).toEqual([]);
  });

  it("says there is no separate account to create", async () => {
    await mount();
    expect(bodyHasText("no separate account to create")).toBe(true);
  });
});

describe("once it is on", () => {
  it("shows this month's usage against the allowance", async () => {
    state.bgRemoval = { ...OFF, active: true, used: 42 };
    await mount();
    expect(host.querySelector('[data-testid="bg-usage"]').textContent)
      .toContain("42 of 1000 photos this month");
  });

  it("can be switched off again, after a confirm", async () => {
    state.bgRemoval = { ...OFF, active: true };
    await mount();
    await press(buttonSaying("Turn off"));
    await press([...document.querySelectorAll("button")]
      .filter((b) => b.textContent.trim() === "Turn off").at(-1));

    expect(state.posts).toEqual(["/api/bg-removal/deactivate"]);
  });

  it("flags a server on Photoroom's watermarking test key", async () => {
    state.bgRemoval = { ...OFF, active: true, sandbox: true };
    await mount();
    expect(bodyHasText("Test mode")).toBe(true);
  });
});

describe("when there is nothing to switch on", () => {
  it("says so instead of offering a button that cannot work", async () => {
    state.bgRemoval = { ...OFF, available: false };
    await mount();
    expect(buttonSaying("Turn on background removal")).toBeFalsy();
    expect(bodyHasText("isn’t available")).toBe(true);
  });
});
