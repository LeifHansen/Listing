/* The photo studio's editing model, kept out of the component so it can be
   reasoned about (and tested) on its own.

   Three layers, never flattened until Save:

     original  the photo exactly as it arrived. Never written to.
     cutout    the current background-removed composite. Replaced wholesale by
               a big operation (remove background, crop, auto levels).
     mask      where the cutout shows. Opaque = cutout, transparent = the
               original underneath.
     paint     white the seller has brushed on, drawn last.

   Why it matters: the old editor had ONE canvas, and the only brush painted
   white onto it. That makes background removal a one-way door — when the
   cutout eats a strap or a heel there is nothing to paint back, and the
   seller's only recourse is Revert, which throws away the whole cutout along
   with the parts it got right. Keeping the original underneath means
   "restore" is just erasing the mask.

   Edits are recorded as STROKES (a tool, a radius, and the points), not as
   pixel snapshots. Undo replays what's left onto fresh layers, which is what
   makes the history affordable: a 1600x1600 snapshot is 10MB of ImageData,
   and ten of those is a tab that gets killed on a phone. */

export const RESTORE = "restore";  // bring the original back through the mask
export const ERASE = "erase";      // paint white over whatever is there

export function makeCanvas(width, height) {
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(width));
  c.height = Math.max(1, Math.round(height));
  return c;
}

export function cloneCanvas(src) {
  const c = makeCanvas(src.width, src.height);
  c.getContext("2d").drawImage(src, 0, 0);
  return c;
}

// A fresh layer set from one image (or canvas). cutout starts as a copy of the
// original, so an untouched photo composes back to exactly itself.
export function layersFrom(source) {
  const width = source.naturalWidth || source.width;
  const height = source.naturalHeight || source.height;
  const original = makeCanvas(width, height);
  original.getContext("2d").drawImage(source, 0, 0);
  const cutout = cloneCanvas(original);
  const mask = makeCanvas(width, height);
  const paint = makeCanvas(width, height);
  resetMask(mask);
  return { original, cutout, mask, paint, width, height };
}

// Opaque everywhere: with no strokes yet, the cutout is what shows.
export function resetMask(mask) {
  const ctx = mask.getContext("2d");
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, mask.width, mask.height);
}

// Draw one stroke into whichever layer it belongs to.
//
// The points are joined with lineTo rather than stamped as separate circles.
// Stamping is what the old brush did, and a quick drag outruns the pointer
// event stream — so the "stroke" arrived as a dotted line of disconnected
// blobs and the seller had to go back over the gaps.
export function drawStroke(layers, stroke) {
  const target = stroke.tool === RESTORE ? layers.mask : layers.paint;
  const ctx = target.getContext("2d");
  const pts = stroke.points;
  if (!pts || !pts.length) return;
  ctx.save();
  // RESTORE punches a hole in the mask (the original shows through); ERASE
  // lays white on top.
  ctx.globalCompositeOperation = stroke.tool === RESTORE
    ? "destination-out" : "source-over";
  ctx.strokeStyle = "#fff";
  ctx.fillStyle = "#fff";
  ctx.lineWidth = Math.max(1, stroke.radius * 2);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (pts.length === 1) {
    ctx.beginPath();
    ctx.arc(pts[0][0], pts[0][1], Math.max(0.5, stroke.radius), 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
    ctx.stroke();
  }
  ctx.restore();
}

// original -> cutout through the mask -> paint on top.
export function compose(layers, target) {
  if (target.width !== layers.width || target.height !== layers.height) {
    target.width = layers.width;
    target.height = layers.height;
  }
  const ctx = target.getContext("2d");
  ctx.clearRect(0, 0, target.width, target.height);
  ctx.drawImage(layers.original, 0, 0);
  // The mask is applied to a scratch copy so the cutout layer itself stays
  // intact — it has to survive being re-masked on every undo.
  const scratch = makeCanvas(layers.width, layers.height);
  const sctx = scratch.getContext("2d");
  sctx.drawImage(layers.cutout, 0, 0);
  sctx.globalCompositeOperation = "destination-in";
  sctx.drawImage(layers.mask, 0, 0);
  ctx.drawImage(scratch, 0, 0);
  ctx.drawImage(layers.paint, 0, 0);
}

// Rebuild mask + paint from a stroke list. The whole point of recording
// strokes instead of pixels: undo is "replay one fewer".
export function replay(layers, strokes) {
  resetMask(layers.mask);
  layers.paint.getContext("2d")
    .clearRect(0, 0, layers.paint.width, layers.paint.height);
  for (const stroke of strokes) drawStroke(layers, stroke);
}

/* Undo/redo over two kinds of edit: brush strokes, and the big operations
   that replace the cutout outright. Strokes replay; operations are snapshots,
   because there is nothing to replay — a cutout came back from a model.

   Capped, and the cap is about memory rather than tidiness: each operation
   entry holds two full-size canvases. */
const MAX_HISTORY = 24;

export function createHistory() {
  return { undo: [], redo: [] };
}

export function pushStroke(history, stroke) {
  history.undo.push({ kind: "stroke", stroke });
  if (history.undo.length > MAX_HISTORY) history.undo.shift();
  history.redo.length = 0;
}

// Call BEFORE replacing the cutout, with the layers as they are now.
export function snapshot(layers, strokes) {
  return {
    original: cloneCanvas(layers.original),
    cutout: cloneCanvas(layers.cutout),
    strokes: strokes.map((s) => ({ ...s, points: s.points.slice() })),
    width: layers.width,
    height: layers.height,
  };
}

export function pushOperation(history, before, after) {
  history.undo.push({ kind: "operation", before, after });
  if (history.undo.length > MAX_HISTORY) history.undo.shift();
  history.redo.length = 0;
}

// Restore a snapshot into `layers`/`strokes` in place. Returns the strokes.
export function applySnapshot(layers, snap) {
  layers.width = snap.width;
  layers.height = snap.height;
  layers.original = cloneCanvas(snap.original);
  layers.cutout = cloneCanvas(snap.cutout);
  layers.mask = makeCanvas(snap.width, snap.height);
  layers.paint = makeCanvas(snap.width, snap.height);
  const strokes = snap.strokes.map((s) => ({ ...s, points: s.points.slice() }));
  replay(layers, strokes);
  return strokes;
}
