/**
 * The camera's Orientation tag, read and applied by hand.
 *
 * The uploader re-encodes phone photos through a canvas, and a canvas keeps
 * pixels, not tags: if the browser did not turn the photo when it decoded
 * it, the server receives a sideways photo with nothing left to say so.
 * These pin the three pieces that make the uploader independent of the
 * browser's decode: the probe that asks the browser what it does, the
 * reader that takes the tag off a JPEG's bytes, and the draw that applies
 * it — the same eight cases as the server's Pillow pass.
 */
import { describe, expect, it } from "vitest";

import {
  decodeAppliesOrientation, drawOriented, isJpeg, jpegOrientation,
  orientedSize, probeBlob,
} from "./photoOrientation";

// A JPEG head: SOI, then the segments given, then image data. Enough for a
// reader that stops at the first Exif APP1 or at the image data.
function jpeg(...segments) {
  const parts = [[0xff, 0xd8], ...segments, [0xff, 0xda, 0x00, 0x02]];
  return new Uint8Array(parts.flat()).buffer;
}

function app1(payload) {
  const length = payload.length + 2;
  return [0xff, 0xe1, length >> 8, length & 0xff, ...payload];
}

// An Exif APP1 whose IFD0 holds only the Orientation entry, in either byte
// order — a SHORT's value sits in the entry's last four bytes.
function exif(tag, { little = false, extraEntriesFirst = 0 } = {}) {
  const u16 = (n) => (little ? [n & 0xff, n >> 8] : [n >> 8, n & 0xff]);
  const u32 = (n) => (little
    ? [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, n >>> 24]
    : [n >>> 24, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff]);
  const entries = [];
  for (let i = 0; i < extraEntriesFirst; i++) {
    entries.push(...u16(0x010f), ...u16(2), ...u32(6), ...u32(0));  // Make
  }
  entries.push(...u16(0x0112), ...u16(3), ...u32(1), ...u16(tag), 0, 0);
  const tiff = [
    ...(little ? [0x49, 0x49] : [0x4d, 0x4d]), ...u16(42), ...u32(8),
    ...u16(extraEntriesFirst + 1), ...entries, ...u32(0),
  ];
  return app1([0x45, 0x78, 0x69, 0x66, 0, 0, ...tiff]);  // "Exif\0\0"
}

const XMP = app1([...Buffer.from("http://ns.adobe.com/xap/1.0/\0<x:xmpmeta/>")]);

describe("jpegOrientation", () => {
  it("reads the tag in either byte order", () => {
    expect(jpegOrientation(jpeg(exif(6)))).toBe(6);
    expect(jpegOrientation(jpeg(exif(8, { little: true })))).toBe(8);
    expect(jpegOrientation(jpeg(exif(3, { extraEntriesFirst: 2 })))).toBe(3);
  });

  it("is 1 for a photo with no tag, and for anything that is not a JPEG", () => {
    expect(jpegOrientation(jpeg())).toBe(1);
    expect(jpegOrientation(jpeg(XMP))).toBe(1);
    expect(jpegOrientation(new Uint8Array([0x89, 0x50, 0x4e, 0x47]).buffer)).toBe(1);
    expect(jpegOrientation(new ArrayBuffer(0))).toBe(1);
    expect(isJpeg(jpeg())).toBe(true);
    expect(isJpeg(new Uint8Array([0x89, 0x50, 0x4e, 0x47]).buffer)).toBe(false);
  });

  it("finds the tag behind an XMP segment, and never trusts a value off the scale", () => {
    expect(jpegOrientation(jpeg(XMP, exif(6)))).toBe(6);
    expect(jpegOrientation(jpeg(exif(9)))).toBe(1);
    expect(jpegOrientation(jpeg(exif(0)))).toBe(1);
  });

  it("gives up cleanly on a truncated head", () => {
    const whole = new Uint8Array(jpeg(exif(6)));
    for (let cut = 0; cut < whole.length; cut++) {
      expect([1, 6]).toContain(jpegOrientation(whole.slice(0, cut).buffer));
    }
  });
});

describe("decodeAppliesOrientation", () => {
  const decodeAs = (width, height) => async () => ({ width, height, close() {} });

  it("says true when the two-pixel probe comes out turned, false when it does not", async () => {
    expect(await decodeAppliesOrientation(decodeAs(1, 2))).toBe(true);
    expect(await decodeAppliesOrientation(decodeAs(2, 1))).toBe(false);
  });

  it("says nothing at all when the probe cannot be read", async () => {
    expect(await decodeAppliesOrientation(async () => { throw new Error("no"); })).toBe(null);
    expect(await decodeAppliesOrientation(decodeAs(3000, 3000))).toBe(null);
  });

  it("hands the decode a JPEG blob", () => {
    const blob = probeBlob();
    expect(blob.type).toBe("image/jpeg");
    expect(blob.size).toBeGreaterThan(100);
  });
});

describe("drawOriented", () => {
  function recorder() {
    const calls = [];
    return {
      calls,
      save: () => calls.push(["save"]),
      restore: () => calls.push(["restore"]),
      transform: (...a) => calls.push(["transform", ...a]),
      drawImage: (...a) => calls.push(["drawImage", ...a]),
    };
  }

  it("draws an upright photo as it is", () => {
    const ctx = recorder();
    drawOriented(ctx, "src", 1, 1500, 2000);
    expect(ctx.calls).toEqual([["save"], ["drawImage", "src", 0, 0, 1500, 2000], ["restore"]]);
  });

  it("turns a quarter-turned photo, drawing the stored frame the other way round", () => {
    const ctx = recorder();
    drawOriented(ctx, "src", 6, 1500, 2000);
    expect(ctx.calls).toEqual([
      ["save"], ["transform", 0, 1, -1, 0, 1500, 0],
      ["drawImage", "src", 0, 0, 2000, 1500], ["restore"],
    ]);
    const ctx8 = recorder();
    drawOriented(ctx8, "src", 8, 1500, 2000);
    expect(ctx8.calls[1]).toEqual(["transform", 0, -1, 1, 0, 0, 2000]);
    expect(ctx8.calls[2]).toEqual(["drawImage", "src", 0, 0, 2000, 1500]);
  });

  it("turns an upside-down photo without swapping its sides", () => {
    const ctx = recorder();
    drawOriented(ctx, "src", 3, 1500, 2000);
    expect(ctx.calls[1]).toEqual(["transform", -1, 0, 0, -1, 1500, 2000]);
    expect(ctx.calls[2]).toEqual(["drawImage", "src", 0, 0, 1500, 2000]);
  });

  it("knows which tags swap the sides", () => {
    expect(orientedSize(4000, 3000, 1)).toEqual([4000, 3000]);
    expect(orientedSize(4000, 3000, 3)).toEqual([4000, 3000]);
    expect(orientedSize(4000, 3000, 6)).toEqual([3000, 4000]);
    expect(orientedSize(4000, 3000, 8)).toEqual([3000, 4000]);
  });
});
