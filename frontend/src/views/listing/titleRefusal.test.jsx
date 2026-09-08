/* What the seller sees when eBay refuses a listing over its title.
 *
 * The reported screen, all at once: the Title card ringed red, its badge
 * reading "Complete", the publish bar reading "Ready to publish", a title of
 * 56 characters out of 80 — and not one word anywhere about what eBay
 * objected to. Editing the title changed none of it. Three things disagreeing
 * about the same field and nothing to act on.
 *
 * Three fixes, one per describe below.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TitleCard } from "./cards";

let host, root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  root = null;
  host = null;
});

// The slice of the form hook TitleCard reads. Everything else on the card is
// inert here — this is about what it says, not what it does.
function stub(over = {}) {
  return {
    form: { title: "", subtitle: "", brand: "", ...(over.form || {}) },
    completion: { title: "complete" },
    fixTarget: null,
    fixLevel: () => undefined,
    publishResult: null,
    set: vi.fn(),
    ...over,
  };
}

const render = (w) => act(() => root.render(<TitleCard w={w} />));
const text = () => host.textContent;

const REFUSED = {
  issues: [{
    target: "title", level: "error",
    title: "eBay is refusing this listing's title",
    fix: "eBay accepted this same listing when we checked it with a plain "
       + "title, and refused it with this one.",
  }],
};

describe("eBay's words reach the card that holds the field", () => {
  it("says what eBay said, where the title is", () => {
    // Before: the card turned red and said nothing. eBay's sentence existed —
    // in the publish banner, at the bottom of a long page.
    render(stub({ form: { title: "Vintage camera" }, publishResult: REFUSED }));
    expect(text()).toContain("eBay is refusing this listing's title");
  });

  it("stays quiet when eBay refused something else", () => {
    render(stub({
      form: { title: "Vintage camera" },
      publishResult: { issues: [{ target: "price", level: "error", title: "The price is missing or invalid" }] },
    }));
    expect(text()).not.toContain("refusing this listing's title");
  });

  it("names the word eBay would not name", () => {
    // The whole of the reported case. eBay refuses over "spy" and says only
    // that it dislikes the title; error 240 never names a word. This does.
    render(stub({
      form: { title: "Miniature Subminiature Spy Camera Made in Japan with Box" },
      publishResult: REFUSED,
    }));
    expect(text()).toContain("Most likely");
    expect(text()).toContain("“Spy Camera”");
  });

  it("invents no culprit when the title holds no known word", () => {
    // eBay's filter is wider than our list. A wrong guess sends the seller
    // deleting an accurate word, which is the bug with extra steps.
    render(stub({ form: { title: "Nike Air Max 90 Men's 10.5 White" },
                  publishResult: REFUSED }));
    expect(text()).toContain("eBay is refusing this listing's title");
    expect(text()).not.toContain("Most likely");
  });
});

describe("risky words are flagged before the publish, not after", () => {
  it("flags the word as it is typed, with a reason and a replacement", () => {
    render(stub({ form: { title: "Miniature Subminiature Spy Camera" } }));
    expect(text()).toContain("eBay is likely to refuse “Spy Camera”");
    expect(text()).toContain("electronic equipment policy");
    expect(text()).toContain("subminiature");           // what to write instead
  });

  it("does not stop the seller publishing anyway", () => {
    // "Spy camera" is what collectors call these. The flag is a warning, and
    // it has to say so — eBay does not publish its list and neither can we.
    render(stub({ form: { title: "Spy camera" } }));
    expect(text()).toContain("You can publish anyway");
  });

  it("says nothing about an ordinary title", () => {
    render(stub({ form: { title: "Nike Air Max 90 Men's 10.5 White Leather" } }));
    expect(text()).not.toContain("likely to refuse");
    expect(text()).not.toContain("can trip eBay's filters");
  });
});
