// The photo pass turns a photo whose ITEM lay sideways or on its head — a
// vision model's call, not the seller's (backend/services/orient.py). When it
// does, say so: a turn nobody asked for and nobody saw happen reads as the
// upload mangling the photo, and a wrong one is a tap of ↻ on the tile or
// Restore original in the studio — but only if the seller knows to look.
// Null when nothing was turned, so a pile of upright photos says nothing.
export function turnedUprightMessage(results) {
  const turned = (results || []).filter((r) => r && r.rotated);
  if (!turned.length) return null;
  const n = turned.length;
  return `Turned ${n === 1 ? "1 photo" : `${n} photos`} upright. Not right? `
    + "Tap ↻ on the photo to turn it, or Restore original in the studio.";
}
