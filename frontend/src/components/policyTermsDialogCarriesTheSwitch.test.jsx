/* The terms dialog describes the policy the International shipping switch
 * would make -- and the create echoes exactly those terms back.
 *
 * "Create my policies" makes a real business policy on the seller's eBay
 * account. With the switch on, that policy ships worldwide through eBay
 * International Shipping, which is the term that changes who the seller
 * sells to. So the preview has to ask the server for THAT policy's terms,
 * and the confirm has to send back the options the server described rather
 * than whatever the screen holds a moment later.
 *
 * Rendered with react-dom + act rather than a testing library, matching the
 * other component tests here; the dialog primitive is stubbed because it
 * portals and animates, and the test wants its children.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PolicyTermsDialog } from "@/components/PolicyTermsDialog";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const served = vi.hoisted(() => ({ asked: [] }));
vi.mock("@/lib/api", () => ({
  api: (path) => {
    served.asked.push(path);
    const abroad = path.includes("international_shipping=true");
    return Promise.resolve({
      kinds: {
        fulfillment: {
          title: "Postage", name: "Ground",
          terms: [{
            label: "Where you post to",
            value: abroad
              ? "The United States, and worldwide through eBay International Shipping"
              : "The United States only",
            detail: "",
          }],
        },
        payment: { title: "Payment", name: "Pay", terms: [] },
        return: { title: "Returns", name: "Ret", terms: [] },
      },
      options: { service_code: "USPSGroundAdvantage", international_shipping: abroad },
    });
  },
}));
vi.mock("@/components/ui/Dialog", () => ({
  Dialog: ({ open, children }) => (open ? <div data-dialog>{children}</div> : null),
}));

let root = null;
let host = null;

async function mount(options, onConfirm = vi.fn()) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      <PolicyTermsDialog open options={options} onClose={() => {}} onConfirm={onConfirm} />,
    );
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return {
    text: () => host.textContent || "",
    create: () => [...host.querySelectorAll("button")]
      .find((b) => (b.textContent || "").includes("Create these policies")),
  };
}

afterEach(async () => {
  if (root) await act(async () => { root.unmount(); });
  document.body.innerHTML = "";
  served.asked = [];
});

describe("the policy terms dialog and the International shipping switch", () => {
  it("asks for the worldwide policy's terms when the switch is on", async () => {
    const d = await mount({ international_shipping: true });

    expect(served.asked.at(-1)).toContain("international_shipping=true");
    expect(d.text()).toContain("worldwide through eBay International Shipping");
  });

  it("asks for the domestic policy's terms when it is off", async () => {
    const d = await mount({ international_shipping: false });

    expect(served.asked.at(-1)).toContain("international_shipping=false");
    expect(d.text()).toContain("The United States only");
    expect(d.text()).not.toContain("worldwide");
  });

  it("creates the policy it described, not the switch as it stands later", async () => {
    const onConfirm = vi.fn();
    const d = await mount({ international_shipping: true }, onConfirm);

    await act(async () => { d.create().click(); });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0]).toMatchObject({ international_shipping: true });
  });
});
