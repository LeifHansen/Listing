/* Two switches flipped in quick succession both stay where they were put.
 *
 * Each switch saves its own key and the server answers with the whole row
 * as saved. That row replaced every pref on screen, so when the two answers
 * came back in the other order the first switch's row -- written before the
 * second switch was -- put the second one back on screen, though the server
 * held it. A seller who then flipped it again turned it off.
 */
import { act, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSettingsData } from "./useSettingsData";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// One object, as the store's context is between renders: a fresh `user`
// per render would re-read the prefs on every one.
const app = vi.hoisted(() => ({
  user: { id: 1 }, ebay: { connected: false },
  policiesData: null, setPoliciesData: () => {},
}));
vi.mock("@/store", () => ({ useApp: () => app }));

function json(body) {
  return Promise.resolve({
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

let root;
let host;

afterEach(async () => {
  await act(async () => { root.unmount(); });
  host.remove();
  vi.unstubAllGlobals();
});

function Probe({ onValue }) {
  const data = useSettingsData();
  useEffect(() => { onValue(data); });
  return null;
}

describe("savePref", () => {
  it("keeps a later switch when an earlier switch's answer lands last", async () => {
    const held = [];
    vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
      if (opts.method === "PATCH") {
        const sent = JSON.parse(opts.body);
        // The server merges; each answer is the row as it stood after it.
        if ("promote" in sent) {
          return new Promise((resolve) => {
            held.push(() => resolve(json({ prefs: { promote: 1, best_offer: 0 } })));
          });
        }
        return json({ prefs: { promote: 1, best_offer: 1 } });
      }
      return json({ prefs: { promote: 0, best_offer: 0 } });
    }));
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    let data = null;
    await act(async () => { root.render(<Probe onValue={(d) => { data = d; }} />); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(data.prefs).toEqual({ promote: 0, best_offer: 0 });

    let first;
    await act(async () => {
      first = data.savePref("promote", 1);
      await data.savePref("best_offer", 1);
    });
    await act(async () => { held.forEach((go) => go()); await first; });

    expect(data.prefs).toEqual({ promote: 1, best_offer: 1 });
  });
});
