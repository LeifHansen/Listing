/* The console's numbers follow the dashboard's rule: a number nobody could
 * measure is a dash, never a zero.
 *
 * "0 accounts, $0 sold, 0 deletion notices owed" during an outage is a false
 * report about the whole platform — and the backlog tiles are the worst
 * place for it, because a zero there says a promised erasure is done. The
 * other half matters equally: a genuinely empty platform shows real zeros,
 * because a dash where a zero belongs would be its own lie.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toaster";
import { AdminOverview } from "@/views/admin/AdminOverview";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function json(body, status = 200) {
  return Promise.resolve({
    ok: status < 400,
    status,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const EMPTY_PLATFORM = {
  available: true, days: 30,
  users: {
    total: 0, signups: 0, active: 0,
    signup_series: [{ day: "2026-08-01", count: 0 }, { day: "2026-08-02", count: 0 }],
  },
  listings: { by_status: {}, total: 0 },
  sales: { count: 0, value: 0, approx: 0, undated: 0,
           mixed_currency: false, currency: null },
  tokens: { by_kind: {}, features: [] },
  deletion_backlog: { media_purges: 0, deletion_notices: 0 },
  owed_refunds: 0,
};

async function mount() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(
      <ToastProvider>
        <AdminOverview />
      </ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { root, text: () => host.textContent || "" };
}

describe("the overview tiles when the platform cannot be read", () => {
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ""; });

  it("says it could not check, instead of counting zero", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      json({ detail: "Couldn't load the platform numbers just now." }, 503)));
    const { root, text } = await mount();

    expect(text()).toContain("we couldn’t check");
    expect(text()).toContain("—");
    // The sub-lines are claims the failed read cannot support.
    expect(text()).not.toContain("joined in the last");
    expect(text()).not.toContain("records in every state");
    await act(async () => { root.unmount(); });
  });

  it("still counts a platform that really is empty", async () => {
    vi.stubGlobal("fetch", vi.fn(() => json(EMPTY_PLATFORM)));
    const { root, text } = await mount();

    expect(text()).toContain("0 joined in the last");
    expect(text()).not.toContain("we couldn’t check");
    await act(async () => { root.unmount(); });
  });
});
