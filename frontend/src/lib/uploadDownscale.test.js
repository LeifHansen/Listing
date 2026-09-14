/**
 * The browser-side downscale: what it must never do to a photo.
 *
 * It must not turn a transparent photo black. A canvas starts fully
 * transparent and toBlob("image/jpeg") composites what it cannot store onto
 * SOLID BLACK — that is what the HTML spec requires. So every uploaded photo
 * carrying an alpha channel (a PNG cut-out, an iPhone "lift subject" shot,
 * anything another photo tool exported) reached the server already black
 * behind the item: services/images._flatten exists to composite alpha onto
 * the pipeline's canvas colour, and there was no alpha left for it to
 * composite by the time the file arrived.
 *
 * And it must not send a phone photo lying on its side. A canvas keeps
 * pixels, not the camera's Orientation tag, so the only turn the server
 * ever sees is the one the browser applied when it decoded the photo — and
 * browsers have not agreed on whether to apply it. Reported as a batch of
 * cutouts where every item lay sideways. So the uploader asks the browser
 * what it does, with a two-pixel probe, and when the answer is "nothing" it
 * reads the tag off the JPEG itself and turns the photo by hand; when there
 * is no answer at all, the photo goes exactly as it is and the server, which
 * reads the tag, turns it. See lib/photoOrientation.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { downscaleForUpload } from "./api";

const CANVAS_COLOR = "#f8f8f8";  // services/images.CANVAS_COLOR

function bigPhoto(type = "image/png") {
  // Over MAX_UPLOAD_SIDE, so the re-encode path runs rather than the
  // pass-through for photos small enough to send as they are.
  return new File([new Uint8Array(64)], "cutout.png", { type });
}

// A phone JPEG tagged Orientation 6: the sensor lay on its side, so the
// stored frame is landscape and the photo is portrait. Just the head — the
// decode is stubbed, and the tag reader stops at the image data.
function phoneJpeg() {
  const exif = [
    0x45, 0x78, 0x69, 0x66, 0, 0,             // "Exif\0\0"
    0x4d, 0x4d, 0, 42, 0, 0, 0, 8,            // TIFF, big-endian, IFD0 at 8
    0, 1,                                     // one entry:
    0x01, 0x12, 0, 3, 0, 0, 0, 1, 0, 6, 0, 0, // Orientation, SHORT, 1, = 6
    0, 0, 0, 0,
  ];
  const bytes = [0xff, 0xd8, 0xff, 0xe1, 0, exif.length + 2, ...exif,
                 0xff, 0xda, 0, 2];
  return new File([new Uint8Array(bytes)], "IMG_0001.jpg", { type: "image/jpeg" });
}

// The decoder, stubbed. The probe is a bare Blob and answers as a browser
// that applies the tag (1x2) or one that does not (2x1); a photo is a File,
// and decodes at the size it is STORED at.
function stubDecoder({ appliesTag = true, width = 3000, height = 3000 } = {}) {
  const probe = appliesTag ? { width: 1, height: 2 } : { width: 2, height: 1 };
  vi.stubGlobal("createImageBitmap", vi.fn(async (blob) => (
    blob instanceof File ? { width, height, close() {} } : { ...probe, close() {} }
  )));
}

function stubCanvas() {
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
    stubDecoder();
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
    // A browser that applies the tag decodes an upright photo: no turn.
    expect(calls.some((c) => c.op === "transform")).toBe(false);
  });

  it("turns a phone photo by hand when the browser leaves the camera's tag alone", async () => {
    stubDecoder({ appliesTag: false, width: 4000, height: 3000 });
    const seen = stubCanvas();

    const out = await downscaleForUpload(phoneJpeg());

    expect(out.type).toBe("image/jpeg");
    // The canvas is the photo's UPRIGHT size — portrait, halved to fit —
    // and the stored landscape frame is drawn onto it under a quarter turn
    // clockwise, which is what Orientation 6 means.
    expect([seen.canvas.width, seen.canvas.height]).toEqual([1500, 2000]);
    const calls = seen.canvas.getContext("2d").calls;
    const turn = calls.find((c) => c.op === "transform");
    expect(turn.args).toEqual([0, 1, -1, 0, 1500, 0]);
    const draw = calls.find((c) => c.op === "drawImage");
    expect(draw.args.slice(1)).toEqual([0, 0, 2000, 1500]);
    // The backdrop still goes down first, over the whole upright frame.
    const fill = calls.find((c) => c.op === "fillRect");
    expect(fill.args).toEqual([0, 0, 1500, 2000]);
  });

  it("sends a photo as it is when the tag is left alone and there is no JPEG to read it from", async () => {
    // A PNG carries no tag this reads, so there is nothing to turn by hand;
    // the file goes untouched rather than re-encoded on a guess.
    stubDecoder({ appliesTag: false });
    stubCanvas();
    const file = bigPhoto();

    await expect(downscaleForUpload(file)).resolves.toBe(file);
  });

  it("sends a photo as it is when the browser's decode cannot be probed", async () => {
    // A probe that decodes to neither answer says nothing about the browser.
    // The one safe move is to send the photo with its tag on: the server
    // reads it, and turns the photo itself.
    stubDecoder({ appliesTag: true, width: 3000, height: 3000 });
    globalThis.createImageBitmap.mockImplementation(async () => (
      { width: 3000, height: 3000, close() {} }));
    stubCanvas();
    const file = bigPhoto();

    await expect(downscaleForUpload(file)).resolves.toBe(file);
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
