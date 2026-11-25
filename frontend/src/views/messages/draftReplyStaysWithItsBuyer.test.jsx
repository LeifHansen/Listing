/* A reply typed to one buyer is not sitting in the box for the next.
 *
 * On a wide screen the conversation list and the thread are side by side,
 * and the composer under the thread was one component whatever thread was
 * open. So a reply half-written to buyer A was still in the box after the
 * seller clicked buyer B, and Enter sent it to B.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { MessagesView } from "@/views/MessagesView";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({ current: {} }));
vi.mock("@/store", () => ({ useApp: () => app.current }));

const A = { id: "a", counterparty: "buyer_a", title: "Vase", marketplace: "ebay" };
const B = { id: "b", counterparty: "buyer_b", title: "Lamp", marketplace: "ebay" };

function state(activeConversationId) {
  return {
    user: { id: 1 },
    ebay: { messaging_enabled: true },
    messages: {
      conversations: [A, B], unread: 0, sources: [], available: true,
      reason: "", message: "", loaded: true,
    },
    threads: {},
    activeConversationId,
    openConversation: vi.fn(),
    sendMessage: vi.fn(async () => true),
    messageSource: "",
    setMessageSource: vi.fn(),
    openAuth: vi.fn(),
    openListing: vi.fn(),
    openSettings: vi.fn(),
  };
}

let root = null;
let host = null;

async function render(activeConversationId) {
  app.current = state(activeConversationId);
  if (!root) {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  }
  await act(async () => { root.render(<MessagesView />); });
}

// A controlled textarea only hears a change through the native setter.
async function type(el, text) {
  const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
  await act(async () => {
    set.call(el, text);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

// jsdom draws nothing, so it has no scrolling; the thread jumps to its end.
beforeAll(() => { Element.prototype.scrollIntoView ??= () => {}; });

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  root = null;
});

describe("the reply box", () => {
  it("starts empty when the seller opens a different conversation", async () => {
    await render("a");
    await type(host.querySelector("#composer"), "Yes, I can do $40");
    expect(host.querySelector("#composer").value).toBe("Yes, I can do $40");

    await render("b");
    expect(host.querySelector("#composer").value).toBe("");
  });
});
