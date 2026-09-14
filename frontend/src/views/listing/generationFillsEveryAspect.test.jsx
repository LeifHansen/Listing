/* Generation fills the whole aspect list, not just the fields eBay demands.
 *
 * The editor's fallback fill — the one that runs when the identify job's own
 * server-side pass was skipped or came back empty — used to start only if a
 * REQUIRED aspect was blank. That was the editor asking a narrower question
 * than the pass it triggers: the fill reads the photos against eBay's WHOLE
 * aspect list for the category, and recommended is where a listing's
 * searchability actually lives ("Subject", "Era", "Occasion" — the filters
 * buyers click).
 *
 * So a draft whose two required specifics came back filled and whose twenty
 * recommended ones were blank stood the fill down and left them blank. What
 * the seller was left with was a "Fill 20 with AI" button at the bottom of
 * the Item specifics card, offering to do by hand the work this listing had
 * already earned. That button is gone; this is what replaces it.
 *
 * The guards that keep it from spending twice are unchanged and tested here
 * too, because widening the trigger is only safe while they hold: once per
 * session, never on a reopened listing, and never when the server already
 * filled during identify.
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

const aspect = (name, required = false) => ({
  name, required, mode: "FREE_TEXT", values: [],
  cardinality: "SINGLE", data_type: "STRING",
});

// One required aspect and three recommended — the shape that used to fall
// through the required-only test.
const ASPECTS = [aspect("Brand", true), aspect("Colour"), aspect("Style"),
                 aspect("Material")];

function Probe({ onValue }) {
  const app = useApp();
  const form = useListingForm();
  useEffect(() => { onValue({ app, form }); });
  return null;
}

let root;
let host;

/** Mount the editor over a session, and hand back the autofill calls it made. */
async function open(session) {
  const calls = [];
  vi.stubGlobal("fetch", vi.fn((url, opts = {}) => {
    const path = String(url);
    if (path.startsWith("/api/autofill-specifics/")) {
      calls.push(path);
      return ok({ item_specifics: [], added: 0 });
    }
    if (path === "/api/item-aspects") return ok({ aspects: ASPECTS });
    if (path === "/api/item-conditions") return ok({ conditions: [], checked: true });
    if (path === "/api/health") {
      return ok({ taxonomy_configured: true, anthropic_configured: true });
    }
    return ok({});
  }));

  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  let value = null;
  await act(async () => {
    root.render(
      <ToastProvider><AppProvider><Probe onValue={(v) => { value = v; }} /></AppProvider></ToastProvider>,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { value.app.setSession(session); });
  // The aspect lookup, the effect it wakes, and the fill it starts each land
  // a turn apart.
  for (let i = 0; i < 4; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
  return calls;
}

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

/** A listing straight out of a fresh identify, holding `specifics`. */
const drafted = (specifics, over = {}) => ({
  sessionId: "s1",
  confidence: "high",          // a FRESH identify — not a reopened listing
  specificsAutofilled: false,  // ...whose server-side pass filled nothing
  listing: { title: "Vintage bowling trophy", category_id: "11450",
             images: ["1.jpg"], item_specifics: specifics },
  ...over,
});

describe("the fill that runs at generation", () => {
  it("runs for blank RECOMMENDED aspects, with every required one filled",
    async () => {
      // The reported case: nothing is blocking the publish, and the listing
      // is still absent from three filters buyers use.
      const calls = await open(drafted([
        { name: "Brand", value: "Acme", confidence: "high" },
      ]));
      expect(calls).toEqual(["/api/autofill-specifics/s1"]);
    });

  it("still runs when a required aspect is blank", async () => {
    const calls = await open(drafted([]));
    expect(calls).toEqual(["/api/autofill-specifics/s1"]);
  });

  it("does not run when the whole list is already answered", async () => {
    const calls = await open(drafted(
      ASPECTS.map((a) => ({ name: a.name, value: "x", confidence: "high" }))));
    expect(calls).toEqual([]);
  });
});

describe("what keeps it from spending twice", () => {
  it("stands down when the identify job already filled server-side", async () => {
    const calls = await open(drafted([], { specificsAutofilled: true }));
    expect(calls).toEqual([]);
  });

  it("stands down on a listing merely reopened", async () => {
    // No confidence score: a saved draft, a bulk item, an eBay import. Their
    // specifics were settled long ago and re-reading the photos would just
    // charge for it again.
    const calls = await open(drafted([], { confidence: null }));
    expect(calls).toEqual([]);
  });
});
