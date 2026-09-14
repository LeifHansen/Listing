/* photoOrientation — which way up a phone photo is, read off the file
 * itself, for a browser that will not read it for us.
 *
 * A phone stores the sensor's pixels and writes how it was held into the
 * EXIF Orientation tag; every viewer turns the photo on display. The
 * uploader re-encodes a photo through a canvas before sending it (see
 * api.downscaleForUpload), and a canvas keeps pixels, not tags: whatever
 * turn the browser applied when it DECODED the photo is the only turn the
 * server will ever see. Browsers have disagreed about that decode for
 * years — the option that asks for the tag to be applied has been renamed
 * once and defaulted differently across engines and versions — and the
 * seller cannot tell which one they are holding. What they can tell is
 * that every item in a batch came out lying on its side.
 *
 * So nothing here takes a browser's word for it. `decodeAppliesOrientation`
 * decodes a two-pixel probe tagged "turn 90 degrees" exactly the way a
 * photo is decoded and looks at which way it came out. When the browser
 * turned it, every photo it decodes is upright and nothing more is needed.
 * When it did not, `jpegOrientation` reads the tag straight out of the
 * file's bytes and `drawOriented` applies the same eight cases the server's
 * own pass does (Pillow's exif_transpose, services/images._load) while
 * drawing to the canvas — so the photo the server receives is upright
 * whichever way the browser went. A browser the probe cannot read at all
 * answers null, and the uploader sends the photo exactly as it is: the tag
 * rides along and the server turns it.
 */

// A 2x1 JPEG tagged Orientation 6 (turn 90° clockwise to view). Decoded the
// way a photo is decoded, it comes out 1x2 when the browser applied the tag
// and 2x1 when it did not. Made with Pillow:
//   exif = Image.Exif(); exif[274] = 6
//   Image.new("L", (2, 1), 128).save(buf, "JPEG", quality=30, exif=exif)
const PROBE_BASE64 =
  "/9j/4AAQSkZJRgABAQAAAQABAAD/4QAiRXhpZgAATU0AKgAAAAgAAQESAAMAAAABAAYAAAAA"
  + "AAD/2wBDABsSFBcUERsXFhceHBsgKEIrKCUlKFE6PTBCYFVlZF9VXVtqeJmBanGQc1tdhbWG"
  + "kJ6jq62rZ4C8ybqmx5moq6T/wAALCAABAAIBAREA/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAEC"
  + "AwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEI"
  + "I0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZn"
  + "aGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJ"
  + "ytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/9oACAEBAAA/ACv/2Q==";

export function probeBlob() {
  const bin = atob(PROBE_BASE64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: "image/jpeg" });
}

// Whether `decode` — the uploader's own decode, resolving { width, height,
// close } — applies the Orientation tag. true when it does, false when it
// leaves the tag alone, and null when it could not decode the probe at all,
// which says nothing about the browser and must not be read as either.
export async function decodeAppliesOrientation(decode) {
  let probe;
  try {
    probe = await decode(probeBlob());
  } catch (e) {
    return null;
  }
  try {
    if (probe.width === 1 && probe.height === 2) return true;
    if (probe.width === 2 && probe.height === 1) return false;
    return null;
  } finally {
    probe.close?.();
  }
}

// The first `bytes` of a file, as an ArrayBuffer. FileReader rather than
// Blob.arrayBuffer(): the browsers this exists for are the older ones, and
// the reader is the API all of them have.
export function fileHead(file, bytes = 256 * 1024) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsArrayBuffer(file.slice(0, bytes));
  });
}

export function isJpeg(buffer) {
  const v = new DataView(buffer);
  return v.byteLength >= 2 && v.getUint16(0) === 0xffd8;
}

// The Orientation tag of a JPEG, 1-8 — 1 (as stored) when it has none, or
// when the bytes are not a JPEG's. The tag sits in an EXIF APP1 segment
// ahead of the image data; a segment holds 64KB at most, so a camera's tag
// is in the first 64KB of its file, and a file that leads with an XMP or ICC
// segment pushes it only a little further. Hand this the file's head.
export function jpegOrientation(buffer) {
  const v = new DataView(buffer);
  if (!isJpeg(buffer)) return 1;
  let at = 2;
  while (at + 4 <= v.byteLength) {
    const marker = v.getUint16(at);
    // Image data (SOS) or the end (EOI): there is no metadata past here.
    // A byte that is not a marker at all is a file this does not read.
    if (marker === 0xffda || marker === 0xffd9 || (marker & 0xff00) !== 0xff00) return 1;
    const length = v.getUint16(at + 2);
    // APP1 starting "Exif" — XMP lives in an APP1 too, and is skipped by
    // the same test.
    if (marker === 0xffe1 && at + 10 <= v.byteLength
        && v.getUint32(at + 4) === 0x45786966) {
      return tiffOrientation(v, at + 10);
    }
    at += 2 + length;
  }
  return 1;
}

// The Orientation entry of the TIFF structure that follows "Exif\0\0":
// byte order, the number 42, the offset of the first directory, then that
// directory's twelve-byte entries. The value of a SHORT sits in the entry's
// last four bytes, in the file's own byte order.
function tiffOrientation(v, tiff) {
  if (tiff + 8 > v.byteLength) return 1;
  const order = v.getUint16(tiff);
  const little = order === 0x4949;
  if (!little && order !== 0x4d4d) return 1;
  if (v.getUint16(tiff + 2, little) !== 42) return 1;
  const ifd = tiff + v.getUint32(tiff + 4, little);
  if (ifd + 2 > v.byteLength) return 1;
  const entries = v.getUint16(ifd, little);
  for (let i = 0; i < entries; i++) {
    const entry = ifd + 2 + i * 12;
    if (entry + 12 > v.byteLength) return 1;
    if (v.getUint16(entry, little) !== 0x0112) continue;
    const value = v.getUint16(entry + 8, little);
    return value >= 1 && value <= 8 ? value : 1;
  }
  return 1;
}

// The size a photo shows at, from the size it is stored at: a quarter turn
// (tags 5-8) swaps the two.
export function orientedSize(width, height, orientation) {
  return orientation >= 5 ? [height, width] : [width, height];
}

// Draw `source` — a decoded image at its STORED pixels — so that it fills a
// `width` x `height` canvas the right way up. One transform per tag, each
// the turn that undoes how the sensor lay; Pillow's exif_transpose applies
// the same eight on the server. For a quarter turn the source is drawn at
// the canvas's size turned, which is its own.
export function drawOriented(ctx, source, orientation, width, height) {
  const [drawW, drawH] = orientation >= 5 ? [height, width] : [width, height];
  ctx.save();
  switch (orientation) {
    case 2: ctx.transform(-1, 0, 0, 1, width, 0); break;          // mirrored
    case 3: ctx.transform(-1, 0, 0, -1, width, height); break;    // upside down
    case 4: ctx.transform(1, 0, 0, -1, 0, height); break;         // flipped
    case 5: ctx.transform(0, 1, 1, 0, 0, 0); break;               // transposed
    case 6: ctx.transform(0, 1, -1, 0, width, 0); break;          // 90° clockwise
    case 7: ctx.transform(0, -1, -1, 0, width, height); break;    // transversed
    case 8: ctx.transform(0, -1, 1, 0, 0, height); break;         // 90° anticlockwise
    default: break;
  }
  ctx.drawImage(source, 0, 0, drawW, drawH);
  ctx.restore();
}
