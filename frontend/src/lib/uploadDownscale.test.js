/**
 * The browser-side downscale must not turn a transparent photo black.
 *
 * A canvas starts fully transparent and toBlob("image/jpeg") composites what
 * it cannot store onto SOLID BLACK — that is what the HTML spec requires. So
 * every uploaded photo carrying an alpha channel (a PNG cut-out, an iPhone
 * "lift subject" shot, anything another photo tool exported) reached the
 * server already black behind the item: services/images._flatten exists to
 * composite alpha onto the pipeline's canvas colour, and there was no alpha
 * left for it to composite by the time the file arrived.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { downscaleForUpload } from "./api";

const CANVAS_COLOR = "#f8f8f8";  // services/images.CANVAS_COLOR

function bigPhoto(type = "image/png") {
  // Over MAX_UPLOAD_SIDE, so the re-encode path runs rather than the
  // pass-through for photos small enough to send as they are.
  return new File([new Uint8Array(64)], "cutout.png", { type });
}

function stubCanvas(width = 3000, height = 3000) {
  vi.stubGlobal("createImageBitmap", vi.fn(async () => ({
    width, height, close() {},
  })));
  const seen = {};
  vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(
    function toBlob(cb) {
      seen.canvas = this;
      cb(new Blob([new Uint8Array(8)], { type: "image/jpeg" }));
    });
  return seen;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("downscaleForUpload", () => {
  it("paints an opaque backdrop before drawing the photo", async () => {
    const seen = stubCanvas();

    const out = await downscaleForUpload(bigPhoto());

    expect(out.type).toBe("image/jpeg");
    const calls = seen.canvas.getContext("2d").calls;
    const fill = calls.findIndex((c) => c.op === "fillRect");
    const draw = calls.findIndex((c) => c.op === "drawImage");
    expect(fill).toBeGreaterThanOrEqual(0);
    expect(fill).toBeLessThan(draw);
    // The whole frame, in the colour the server would have composited onto —
    // so a photo that is downscaled here and one small enough to skip this
    // land on the same backdrop instead of one of them landing on black.
    expect(calls[fill].args).toEqual([0, 0, 2000, 2000]);
    expect(calls[fill].fillStyle).toBe(CANVAS_COLOR);
  });

  it("still uploads the original when the photo can't be decoded", async () => {
    // Neither decode path works — the guarantee is that the upload goes ahead
    // with the file as it arrived rather than failing on an optimization.
    vi.stubGlobal("createImageBitmap", vi.fn(async () => {
      throw new Error("no options bag");
    }));
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL() {} });
    vi.stubGlobal("Image", class FailingImage {
      set src(_value) { setTimeout(() => this.onerror?.(new Error("decode")), 0); }
    });
    const file = bigPhoto();

    await expect(downscaleForUpload(file)).resolves.toBe(file);
  });
});
