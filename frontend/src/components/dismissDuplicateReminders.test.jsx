/* "I've looked at these and they're fine" — said once.
 *
 * The duplicate card is rebuilt from the server's scan on every Dashboard
 * load, and there is no press that CLEARS it the way Enrich all clears a
 * suggestion: often the honest answer is that nothing needs doing, because
 * two of the same thing can be genuine. Without a way to say so, a seller
 * with a pair they have already thought about gets the same question on
 * every visit, forever.
 *
 * So: one Dismiss all, and it sticks. What it must never become is a silent
 * "never show me this again" — so it goes through a confirm that says what
 * it means, it sends the fingerprints the seller was actually LOOKING at
 * (not whatever the scan turns up a moment later), and the server brings a
 * group back the moment the pair changes.
 *
 * Rendered with react-dom + act, matching MessagesInbox.test.jsx; the repo
 * has no testing-library dependency.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "@/components/ui/Toaster";
import { DuplicateListings } from "@/components/DuplicateListings";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const server = vi.hoisted(() => ({ groups: [], posts: [], fail: null, hidden: 0 }));

vi.mock("@/lib/api", () => ({
  api: vi.fn(async () => ({ groups: server.groups, total: server.groups.length,
                            dismissed: server.hidden })),
  postJson: vi.fn(async (path, body) => {
    server.posts.push({ path, body });
    if (server.fail) throw new Error(server.fail);
    return { ok: true, dismissed: (body.fingerprints || []).length };
  }),
}));

const group = (fingerprint, title, ids) => ({
  fingerprint,
  title,
  confidence: "low",
  reasons: ["Same price."],
  caveats: ["One is an auction and one is Buy It Now, which is often deliberate."],
  listings: ids.map((id, i) => ({
    listing_id: `row-${id}`, ebay_listing_id: id, price: i ? 145 : null,
    currency: "USD", view_url: `https://www.ebay.com/itm/${id}`,
    listed_at: "2026-09-13T09:00:00Z", created_here: i === 0, watch_count: 0,
  })),
});

const BUITRON = group("fp-buitron", "A. Buitron 2008 Signed Original Painting",
  ["158289510185", "158295185672"]);
const ANTEZANA = group("fp-antezana", "Tammy Antezana Original Abstract",
  ["158285193440", "158286306029"]);

let root = null;
let host = null;

async function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(<ToastProvider><DuplicateListings /></ToastProvider>);
  });
  return host;
}

// The confirm dialog and the toasts render into document.body through a
// portal, so everything here is looked up on the whole document.
const buttonSaying = (text) => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const press = async (el) => { await act(async () => { el.click(); }); };
const bodyHasText = (t) => document.body.textContent.includes(t);

beforeEach(() => {
  server.groups = [BUITRON, ANTEZANA];
  server.posts = [];
  server.fail = null;
  server.hidden = 0;
});

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

describe("dismissing the duplicate reminder", () => {
  it("offers Dismiss all beside the report", async () => {
    await mount();
    expect(buttonSaying("Dismiss all")).toBeTruthy();
  });

  it("names one as one rather than 'all 1'", async () => {
    server.groups = [BUITRON];
    await mount();
    expect(buttonSaying("Dismiss all")).toBeFalsy();
    expect(buttonSaying("Dismiss")).toBeTruthy();
  });

  it("does not offer it when there is nothing to report", async () => {
    server.groups = [];
    await mount();
    // The whole card hides itself on a healthy store; the button goes with it.
    expect(host.innerHTML).toBe("");
    expect(buttonSaying("Dismiss all")).toBeFalsy();
  });

  it("asks first, and says what dismissing does and does not do", async () => {
    await mount();
    await press(buttonSaying("Dismiss all"));
    // The two things a seller needs before pressing: nothing comes off eBay,
    // and this is not a one-way door.
    expect(bodyHasText("Every listing stays live on eBay")).toBe(true);
    expect(bodyHasText("They come back if you edit")).toBe(true);
    expect(server.posts).toEqual([]);
  });

  it("changes nothing when the confirm is refused", async () => {
    await mount();
    await press(buttonSaying("Dismiss all"));
    await press(buttonSaying("Cancel"));
    expect(server.posts).toEqual([]);
    expect(bodyHasText("A. Buitron")).toBe(true);
  });

  it("sends the fingerprints on screen, and takes the card away", async () => {
    await mount();
    await press(buttonSaying("Dismiss all"));
    await press([...document.querySelectorAll("button")]
      .filter((b) => b.textContent.trim() === "Dismiss all").pop());

    expect(server.posts).toEqual([{
      path: "/api/ebay/duplicates/dismiss",
      // What the seller was looking at — not "everything you can find".
      body: { fingerprints: ["fp-buitron", "fp-antezana"] },
    }]);
    expect(host.innerHTML).toBe("");
    expect(bodyHasText("unless they change")).toBe(true);
  });

  it("keeps the card when the dismissal did not land", async () => {
    server.fail = "the database is having a moment";
    await mount();
    await press(buttonSaying("Dismiss all"));
    await press([...document.querySelectorAll("button")]
      .filter((b) => b.textContent.trim() === "Dismiss all").pop());

    // Silently hiding a card whose dismissal never persisted is how a seller
    // finds out it is broken tomorrow instead of now.
    expect(bodyHasText("A. Buitron")).toBe(true);
    expect(bodyHasText("the database is having a moment")).toBe(true);
    expect(buttonSaying("Dismiss all")).toBeTruthy();
  });

  it("says how many it is holding back, and on what terms", async () => {
    server.hidden = 2;
    await mount();
    // A scan that quietly drops what you dismissed reads exactly like a scan
    // that stopped finding anything.
    expect(bodyHasText("2 more are dismissed; they come back here if you edit them"))
      .toBe(true);
  });

  it("says nothing about dismissals when there are none", async () => {
    await mount();
    expect(bodyHasText("dismissed")).toBe(false);
  });

  it("leaves End this one alone — a dismissal ends nothing", async () => {
    await mount();
    await press(buttonSaying("Dismiss all"));
    await press([...document.querySelectorAll("button")]
      .filter((b) => b.textContent.trim() === "Dismiss all").pop());
    expect(server.posts.map((p) => p.path))
      .toEqual(["/api/ebay/duplicates/dismiss"]);
  });
});
