/**
 * What the rotate button is allowed to show while the server catches up.
 *
 * One tap turns the photo on screen straight away and holds that turn until
 * the re-encoded file has actually loaded — the optimized photo is a perfect
 * square, so a CSS quarter-turn in a square frame is exactly what the saved
 * file will look like.
 *
 * The glitch: the turn was also cleared by an effect on `version`. That prop
 * bumps the moment the server answers, which only points the <img> at a new
 * URL — the browser goes on painting the OLD bytes until that fetch comes
 * back. So on every single rotate the tile turned, snapped back to the
 * un-turned photo for as long as the download took, then turned again when
 * the new file arrived. The effect was written as a fallback for a missed
 * load event and instead won the race with it every time.
 *
 * The other half: a rotate that FAILED left the turn on screen for good. The
 * tile takes it back on a rejection, but the caller swallowed the error, so
 * the seller was left looking at an orientation no file had.
 */
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PhotoTile } from "./PhotoTile";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const img = () => container.querySelector("img");
const turn = () => img().style.transform;
const rotateButton = () => container.querySelector('button[title="Rotate 90°"]');

function render(props) {
  act(() => {
    root.render(
      <PhotoTile
        sessionId="s1" name="img_000.jpg" index={0} total={2}
        onDelete={() => {}} onEdit={() => {}} onMakeMain={() => {}}
        onMove={() => {}}
        {...props}
      />);
  });
}

/** The browser finishing a fetch and painting those bytes. */
function paint() {
  act(() => { img().dispatchEvent(new Event("load")); });
}

async function click() {
  await act(async () => { rotateButton().click(); });
}

describe("the optimistic turn", () => {
  it("shows on the tap, before the server has answered", async () => {
    let release;
    render({ version: 0, onRotate: () => new Promise((r) => { release = r; }) });

    expect(turn()).toBe("");
    await click();
    expect(turn()).toBe("rotate(90deg)");

    await act(async () => { release(); });
  });

  it("holds while the re-encoded file is still downloading", async () => {
    render({ version: 0, onRotate: () => Promise.resolve() });
    await click();

    // The server answered, so the version bumped and the <img> is pointed at
    // the new file. The pixels on screen are still the old ones.
    render({ version: 1, onRotate: () => Promise.resolve() });

    expect(turn()).toBe("rotate(90deg)");
  });

  it("comes off when the new file is the one on screen", async () => {
    render({ version: 0, onRotate: () => Promise.resolve() });
    await click();
    render({ version: 1, onRotate: () => Promise.resolve() });

    paint();

    expect(turn()).toBe("");
  });

  it("survives a reload of the photo it was applied to", async () => {
    let release;
    render({ version: 0, onRotate: () => new Promise((r) => { release = r; }) });
    await click();

    // Same version, so these are the un-turned bytes: clearing here is the
    // flash the whole mechanism exists to avoid.
    paint();

    expect(turn()).toBe("rotate(90deg)");
    await act(async () => { release(); });
  });

  it("is taken back when the rotate failed", async () => {
    render({
      version: 0,
      onRotate: () => Promise.reject(new Error("Couldn't rotate that photo")),
    });

    await click();

    expect(turn()).toBe("");
  });

  it("does not strand a turn on a photo that failed to load", async () => {
    render({ version: 0, onRotate: () => Promise.resolve() });
    await click();
    render({ version: 1, onRotate: () => Promise.resolve() });

    act(() => { img().dispatchEvent(new Event("error")); });

    expect(turn()).toBe("");
  });
});
