/* Opening a notification takes it off the bell's count only if it was unread.
 *
 * markNotificationsRead lowers the badge optimistically, before the server
 * answers. It used to subtract one per id it was handed, so opening a
 * notification already read lowered the count anyway, and the bell showed
 * one fewer unread than there were until the next minute's poll.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

const ANSWERS = {
  "/api/auth/me": { user: { id: 7, email: "seller@example.com" } },
  "/api/notifications": {
    notifications: [{ id: 1, read: true, title: "Sold" }, { id: 2, read: false, title: "Sold" }],
    unread: 1, checked: true,
  },
};

let app;
function Probe({ onValue }) {
  const value = useApp();
  useEffect(() => { onValue(value); });
  return null;
}

let root;
afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); });

async function settle() {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

it("an already-read notification does not lower the unread count", async () => {
  vi.stubGlobal("fetch", vi.fn((url) => {
    const key = Object.keys(ANSWERS).find((k) => String(url).startsWith(k));
    return key ? json(ANSWERS[key]) : json({});
  }));
  root = createRoot(document.createElement("div"));
  await act(async () => {
    root.render(<ToastProvider><AppProvider><Probe onValue={(v) => { app = v; }} /></AppProvider></ToastProvider>);
  });
  await settle();
  await act(async () => { await app.loadNotifications(); });
  expect(app.notifications.unread).toBe(1);

  await act(async () => { await app.markNotificationsRead([1]); });
  expect(app.notifications.unread).toBe(1);

  await act(async () => { await app.markNotificationsRead([2]); });
  expect(app.notifications.unread).toBe(0);
});
