/* The inbox icon and the marketplace toggle.
 *
 * The icon is the feature's front door, so what it does when there is nothing
 * behind it matters as much as the happy path: with messaging off, or nobody
 * signed in, it must not render at all. A dead icon in the header is worse
 * than no icon — and this is a limited-release integration most deployments
 * will run with switched off.
 *
 * Rendered with react-dom + act rather than a testing library, matching
 * store.logout.test.jsx; the repo has no testing-library dependency.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessagesInbox, inboxEmptyCopy, ConversationRow } from "@/components/MessagesInbox";
import { SourceTabs } from "@/views/messages/SourceTabs";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({ current: {} }));
vi.mock("@/store", () => ({ useApp: () => app.current }));

let root = null;
let host = null;

async function mount(element) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(element); });
  return host;
}

const byLabel = (l) => host.querySelector(`[aria-label="${l}"]`);
const texts = () => [...host.querySelectorAll("*")].map((n) => n.textContent);
const hasText = (t) => [...host.querySelectorAll("*")]
  .some((n) => n.children.length === 0 && n.textContent.trim() === t);
const nodeWithText = (t) => [...host.querySelectorAll("*")]
  .find((n) => n.children.length === 0 && n.textContent.trim() === t);

const BASE = {
  user: { id: 1 },
  ebay: { messaging_enabled: true },
  messages: {
    conversations: [], unread: 0, sources: [], available: true, reason: "",
    message: "", loaded: true,
  },
  loadMessages: vi.fn(),
  openMessages: vi.fn(),
  setView: vi.fn(),
};

async function setup(over = {}) {
  app.current = {
    ...BASE, ...over,
    messages: { ...BASE.messages, ...(over.messages || {}) },
  };
  return mount(<MessagesInbox />);
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

describe("MessagesInbox", () => {
  it("renders nothing when nobody is signed in", async () => {
    await setup({ user: null });
    expect(host.innerHTML).toBe("");
  });

  it("renders nothing when messaging isn't switched on", async () => {
    await setup({ ebay: { messaging_enabled: false } });
    expect(host.innerHTML).toBe("");
  });

  it("puts the unread count in the label, not just the badge", async () => {
    await setup({ messages: { unread: 3 } });
    expect(byLabel("Messages — 3 unread")).toBeTruthy();
  });

  it("caps a big count at 9+ so the badge can't grow the header", async () => {
    await setup({ messages: { unread: 42 } });
    expect(hasText("9+")).toBe(true);
    // The real number still reaches a screen reader.
    expect(byLabel("Messages — 42 unread")).toBeTruthy();
  });

  it("says Messages, without a count, when nothing is waiting", async () => {
    await setup();
    expect(byLabel("Messages")).toBeTruthy();
  });

  it("opens on click and closes on Escape", async () => {
    await setup();
    const btn = byLabel("Messages");
    await act(async () => { btn.click(); });
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    expect(hasText("See all messages")).toBe(true);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(btn.getAttribute("aria-expanded")).toBe("false");
  });

  it("routes a conversation click through the id that names its marketplace",
    async () => {
      const openMessages = vi.fn();
      await setup({
        openMessages,
        messages: {
          unread: 1,
          conversations: [{ id: "etsy:c9", counterparty: "dave", snippet: "hi",
            last_at: "2026-08-30T09:00:00Z", unread: 1 }],
        },
      });
      await act(async () => { byLabel("Messages — 1 unread").click(); });
      await act(async () => { nodeWithText("dave").closest("button").click(); });
      expect(openMessages).toHaveBeenCalledWith("etsy:c9");
    });
});

describe("empty copy", () => {
  it("offers the fix when a marketplace needs reconnecting", () => {
    expect(inboxEmptyCopy({ reason: "needs_reconnect", message: "", sources: [] })
      .toLowerCase()).toContain("reconnect");
  });

  it("says what the inbox is for when it's simply empty", () => {
    expect(inboxEmptyCopy({
      reason: "", message: "", sources: [{ key: "ebay", available: true }],
    })).toContain("No buyer messages yet");
  });

  it("quotes the marketplace's own reason on an error", () => {
    expect(inboxEmptyCopy({
      reason: "error", message: "eBay returned 503.", sources: [],
    })).toBe("eBay returned 503.");
  });
});

describe("ConversationRow", () => {
  it("shows the listing, so one buyer asking about two items isn't two identical rows",
    async () => {
      await mount(<ConversationRow conversation={{
        id: "ebay:1", counterparty: "sarah_m", snippet: "hi",
        title: "Nikon 50mm", last_at: "2026-08-30T09:00:00Z", unread: 0,
      }} />);
      expect(hasText("Nikon 50mm")).toBe(true);
    });

  it("announces unread state, since the dot is decorative", async () => {
    await mount(<ConversationRow conversation={{
      id: "ebay:1", counterparty: "sarah_m", snippet: "hi", unread: 1,
    }} />);
    expect(hasText("Unread.")).toBe(true);
  });
});

describe("SourceTabs", () => {
  const two = [
    { key: "ebay", label: "eBay", available: true, supported: true },
    { key: "etsy", label: "Etsy", available: true, supported: true },
  ];

  it("stays hidden when only one marketplace can deliver — a filter of one is furniture",
    async () => {
      await mount(<SourceTabs sources={[two[0]]} value="" onChange={() => {}} />);
      expect(host.innerHTML).toBe("");
    });

  it("offers All plus each live marketplace", async () => {
    await mount(<SourceTabs sources={two} value="" onChange={() => {}} />);
    expect(texts().join("|")).toContain("All");
    expect(hasText("eBay")).toBe(true);
    expect(hasText("Etsy")).toBe(true);
  });

  it("names a marketplace that can't do messages yet, rather than hiding it",
    async () => {
      await mount(<SourceTabs
        sources={[...two, { key: "depop", label: "Depop", available: false,
          supported: true, message: "Not connected" }]}
        value="" onChange={() => {}} />);
      const soon = nodeWithText("Depop · soon");
      expect(soon).toBeTruthy();
      expect(soon.closest("button").disabled).toBe(true);
    });

  it("reports the picked marketplace by key", async () => {
    const onChange = vi.fn();
    await mount(<SourceTabs sources={two} value="" onChange={onChange} />);
    await act(async () => { nodeWithText("Etsy").closest("button").click(); });
    expect(onChange).toHaveBeenCalledWith("etsy");
  });
});
