/* The upload pipeline's wait state is the mark, breathing, with the stage in
 * words -- not a small status card over a stack of grey skeleton bars, which
 * the seller read as a page that had failed to load. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { BrandPulse } from "@/components/ui/AIStatus";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
afterEach(() => { act(() => root.unmount()); host.remove(); });

describe("BrandPulse", () => {
  it("shows the pulsing mark, the stage, and the reassurance", async () => {
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root.render(<BrandPulse message="Cleaning up photo 6 of 12…"
                              detail="Your photos are safe here." />);
    });
    const status = host.querySelector("[role=status]");
    expect(status).toBeTruthy();
    expect(status.getAttribute("aria-live")).toBe("polite");
    expect(host.querySelector(".brand-pulse")).toBeTruthy();
    expect(host.querySelector(".brand-pulse-halo")).toBeTruthy();
    expect(host.textContent).toContain("Cleaning up photo 6 of 12…");
    expect(host.textContent).toContain("Your photos are safe here.");
    // Nothing that reads as a half-loaded page.
    expect(host.querySelector(".ai-shimmer")).toBeNull();
  });
});
