/* Manage has two halves: the listings, and the seller's eBay sales.
 *
 * The Sales half groups sales the way a seller reads them — running, coming
 * up, ended (folded) — marks one set up in Seller Hub that this app can't
 * edit with the reason and a way there, and "View listings" lands on the
 * Listings half filtered to that sale. */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { live, mountManage } from "@/test/manageHarness";

const day = 86400000;
const ev = (id, name, over = {}) => ({
  id, name, percent_off: 20, status: "RUNNING",
  start_at: new Date(Date.now() - day).toISOString(),
  end_at: new Date(Date.now() + 5 * day).toISOString(),
  item_ids: ["1a"], editable: true, source: "app", count: 1,
  seller_hub_url: "https://www.ebay.com/sh/mkt/promotions", ...over,
});

const SALES = [
  ev("se_run", "Spring clear-out"),
  ev("se_next", "Black Friday", { status: "SCHEDULED", item_ids: [], count: 0,
    start_at: new Date(Date.now() + 2 * day).toISOString(),
    end_at: new Date(Date.now() + 6 * day).toISOString() }),
  ev("se_hub", "Tiered hub sale", { editable: false, source: "ebay", item_ids: [], count: 4,
    read_only_reason: "Different discounts for different listings" }),
  ev("se_old", "Summer", { end_at: new Date(Date.now() - 2 * day).toISOString(), item_ids: [] }),
];

describe("the Sales half of Manage", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  const open = async () => {
    m = await mountManage([live("a", "Jacket"), live("b", "Scarf")], {},
      { base: { "/api/sale-events": { events: SALES, connected: true, checked: true } }, whole: true });
    const sales = [...document.body.querySelectorAll('[aria-label="Manage"] button')]
      .find((b) => b.textContent.startsWith("Sales"));
    expect(sales.textContent).toBe("Sales3");   // running + coming up + the hub one
    await m.click(sales);
    await m.settle();
  };

  it("groups the sales the way a seller reads them, with ended ones folded away", async () => {
    await open();
    const headings = [...m.host.querySelectorAll("h2, section > button")].map((h) => h.textContent);
    expect(headings).toEqual(["Running · 2", "Coming up · 1", "Ended · 1"]);
    expect(m.text()).not.toContain("Summer");
    await m.click(m.button("Ended · 1"));
    expect(m.text()).toContain("Summer");
  });

  it("marks a Seller Hub sale it can't edit, says why, and links there", async () => {
    await open();
    const card = [...m.host.querySelectorAll("article")].find((a) => a.textContent.includes("Tiered hub sale"));
    expect(card.textContent).toContain("Managed in Seller Hub · Different discounts for different listings");
    expect(card.textContent).not.toContain("Edit");
    expect(card.querySelector("a").getAttribute("href")).toContain("ebay.com");
  });

  it("opens the Listings half filtered to a sale from View listings", async () => {
    await open();
    const card = [...m.host.querySelectorAll("article")].find((a) => a.textContent.includes("Spring clear-out"));
    await m.click([...card.querySelectorAll("button")].find((b) => b.textContent.includes("View listings")));
    await m.settle();
    expect(m.text()).toContain("In: Spring clear-out");
    expect(m.text()).toContain("Jacket");
    expect(m.text()).not.toContain("Scarf");
  });

  it("asks before ending a running sale, and sends nothing on Cancel", async () => {
    await open();
    const card = [...m.host.querySelectorAll("article")].find((a) => a.textContent.includes("Spring clear-out"));
    await m.click([...card.querySelectorAll("button")].find((b) => b.textContent.includes("End sale")));
    await m.settle();
    expect(m.text()).toContain("back to full price on eBay straight away");
    await m.click([...document.body.querySelectorAll('[role="dialog"] button')].find((b) => b.textContent.trim() === "Cancel"));
    expect(m.state.asked["/api/sale-events/se_run/end"]).toBeUndefined();
  });
});

describe("a new sale from the Sales half", () => {
  let m;
  beforeEach(() => { localStorage.clear(); });
  afterEach(async () => { await m.unmount(); });

  it("can cover every active listing, and says that is a snapshot", async () => {
    const made = ev("se_new", "Everything 15%", { percent_off: 15 });
    m = await mountManage([live("a", "Jacket"), live("b", "Scarf")], {
      "/api/sale-events": (body) => (body ? { events: [made], event: made, connected: true,
        report: { changed: 2, skipped: 0, failed: 0, results: { changed: [], skipped: [], failed: [] } } }
        : { events: [], connected: true, checked: true }),
    }, { whole: true });
    const sales = [...document.body.querySelectorAll('[aria-label="Manage"] button')]
      .find((b) => b.textContent.startsWith("Sales"));
    await m.click(sales);
    await m.settle();
    await m.click(m.button("New sale"));
    await m.settle();
    const dialog = () => document.body.querySelector('[role="dialog"]');
    expect(dialog().textContent).toContain("All active listings (2)");
    expect(dialog().textContent).toContain("ones you list later aren’t added");
    await m.click([...dialog().querySelectorAll("button")].find((b) => b.textContent === "15%"));
    await m.click([...dialog().querySelectorAll("button")].find((b) => b.textContent === "Start sale"));
    await m.settle(20);
    const sent = m.state.asked["/api/sale-events"];
    expect(sent).toMatchObject({ percent: 15, days: 7, all_active: true });
    expect(sent.listing_ids).toBeUndefined();
  });
});
