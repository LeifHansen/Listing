/* A deep link into Settings names the section it wants.
 *
 * Every "fix this in Settings" link used to land at the top of a long page.
 * openSettings("shipping") switches to the Settings view AND records the
 * section, which the Settings screen scrolls to once and then clears -- so a
 * plain tap on the nav still lands at the top. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400, status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

describe("openSettings", () => {
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("switches to Settings and names the section, which can be cleared", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json({ detail: "Not found" }, 404)));
    const seen = {};
    function Probe() {
      const app = useApp();
      Object.assign(seen, app);
      return null;
    }
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => {
      root.render(<ToastProvider><AppProvider><Probe /></AppProvider></ToastProvider>);
    });

    expect(seen.view).toBe("dashboard");
    expect(seen.settingsSection).toBe(null);
    await act(async () => { seen.openSettings("shipping"); });
    expect(seen.view).toBe("settings");
    expect(seen.settingsSection).toBe("shipping");
    await act(async () => { seen.clearSettingsSection(); });
    expect(seen.settingsSection).toBe(null);
    // A bare open lands at the top: no section.
    await act(async () => { seen.openSettings(); });
    expect(seen.view).toBe("settings");
    expect(seen.settingsSection).toBe(null);
    await act(async () => { root.unmount(); });
  });
});
