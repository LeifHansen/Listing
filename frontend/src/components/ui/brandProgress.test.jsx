/* The bulk progress bar's fill is the number, drawn. It used to floor itself
 * at 6% "so the bar reads alive from the first paint", and the seller read a
 * bar that plainly was not empty under a label that said 0% as progress the
 * batch had not made. */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { BrandProgress } from "@/components/ui/Progress";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;
afterEach(() => { act(() => root.unmount()); host.remove(); });

async function render(value) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root.render(<BrandProgress value={value} />); });
  const bar = host.querySelector("[role=progressbar]");
  return { bar, fill: bar.querySelector(".brand-progress-fill") };
}

describe("BrandProgress", () => {
  it("draws nothing at 0%: the fill is as empty as the label says", async () => {
    const { bar, fill } = await render(0);
    expect(bar.getAttribute("aria-valuenow")).toBe("0");
    expect(fill.style.width).toBe("0%");
    expect(fill.classList.contains("min-w-6")).toBe(false);
    expect(host.textContent).toContain("0%");
  });

  it("draws exactly the number once there is progress, never less than a bead", async () => {
    const { fill } = await render(3);
    expect(fill.style.width).toBe("3%");
    // A 3% fill on a 24px-high track would be a squashed sliver; the fill
    // keeps its own height as a floor, so it is a round bead instead.
    expect(fill.classList.contains("min-w-6")).toBe(true);
    // The label rides just past the fill's real edge, not a computed one.
    const label = fill.querySelector("span");
    expect(label.className).toContain("left-full");
    expect(label.textContent).toBe("3%");
  });

  it("moves the label inside the fill once there is room, and caps at 100", async () => {
    const half = await render(50);
    const inside = half.fill.querySelector("span");
    expect(inside.className).toContain("right-2.5");
    expect(inside.textContent).toBe("50%");
    act(() => root.unmount()); host.remove();

    const over = await render(140);
    expect(over.bar.getAttribute("aria-valuenow")).toBe("100");
    expect(over.fill.style.width).toBe("100%");
  });
});
