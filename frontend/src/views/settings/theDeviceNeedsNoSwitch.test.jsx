/* What this device can do needs no switch, and says so above the one that
 * does.
 *
 * Removing backgrounds on the seller's own phone or browser sends nothing
 * anywhere and costs nothing, so there is no consent to ask for: the card
 * just says what the device can do -- ready, downloading, or why not. The
 * Photoroom switch below it, and its consent, are exactly as they were.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  bgRemoval: null, user: { id: "u1" },
  local: { status: "off", usable: false, ready: false, provider: null, reason: "", progress: null },
}));

vi.mock("@/lib/api", () => ({ postJson: vi.fn(async () => ({ ok: true })) }));
vi.mock("@/lib/bgRemoval/useLocalCutout", () => ({ useLocalCutout: () => state.local }));
vi.mock("@/store", () => ({
  useApp: () => ({
    user: state.user, bgRemoval: state.bgRemoval,
    tokens: { enabled: false, costs: {} }, loadBgRemovalStatus: async () => {},
  }),
}));

const { BackgroundRemovalCard } = await import("./BackgroundRemovalCard");

const CLOUD_OFF = { available: true, active: false, needs_activation: true,
                    used: 0, limit: 1000, resets_on: "", sandbox: false };

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

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  host?.remove();
  root = null;
});

describe("the device needs no switch", () => {
  it("says it is ready, free and private, above the cloud's unchanged switch", async () => {
    state.bgRemoval = CLOUD_OFF;
    state.local = { status: "ready", usable: true, ready: true, provider: "apple-vision" };
    await mount();
    const text = host.textContent;
    expect(text).toContain("On this device");
    expect(text).toContain("free");
    expect(text).toContain("aren’t sent anywhere");
    expect(text).toContain("In the cloud");
    expect(text).toContain("Turn on background removal");
    expect(text.indexOf("On this device")).toBeLessThan(text.indexOf("In the cloud"));
  });

  it("works where the server has no cloud at all", async () => {
    state.bgRemoval = { ...CLOUD_OFF, available: false };
    state.local = { status: "ready", usable: true, ready: true, provider: "mlkit" };
    await mount();
    expect(host.textContent).toContain("On this device");
    expect(host.textContent).not.toContain("isn’t available on this server");
  });

  it("says why a device can't, and that the cloud still can", async () => {
    state.bgRemoval = CLOUD_OFF;
    state.local = { status: "unsupported", usable: false, ready: false,
                    provider: "apple-vision", reason: "os_version" };
    await mount();
    expect(host.textContent).toContain("needs a newer system version");
    expect(host.textContent).toContain("The cloud option below still works");
  });

  it("offers the one-time download where there is a model to fetch", async () => {
    state.bgRemoval = CLOUD_OFF;
    state.local = { status: "needs-download", usable: true, ready: false, provider: "mlkit" };
    await mount();
    expect([...host.querySelectorAll("button")].some(
      (b) => b.textContent.includes("Download it now"))).toBe(true);
  });

  it("reads exactly as before on a device with no engine switched on", async () => {
    state.bgRemoval = CLOUD_OFF;
    state.local = { status: "off", usable: false, ready: false, provider: null };
    await mount();
    expect(host.textContent).not.toContain("On this device");
    expect(host.textContent).not.toContain("In the cloud");
  });
});
