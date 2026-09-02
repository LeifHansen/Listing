import { useEffect, useRef, useState } from "react";
import {
  Trash2, Pencil, RotateCw, Star, ChevronLeft, ChevronRight,
} from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";

/* PhotoTile — one uploaded photo as a rounded card.
 *
 * Ordering is explicit: "Make main" promotes a photo to the eBay hero shot in
 * one tap, and ‹ › nudge it one place. There is deliberately no drag.
 * Dragging looked like the obvious answer and was tried twice — a hand-rolled
 * pointer implementation and then @dnd-kit — and it is a poor fit for what
 * this actually is: a three-across grid of ~110px squares on a phone, where
 * the drag target, the scroll gesture and the tap targets all overlap. Buttons
 * cannot stutter, cannot be stolen by the page scroller, work with a keyboard
 * and a screen reader for free, and say what they will do before you commit.
 *
 * Rotation applies instantly. The optimized file is a perfect square, so
 * spinning the <img> 90 degrees in its square frame is exactly what the saved
 * photo will look like — no distortion, no guess. The CSS rotation holds until
 * the re-encoded file has loaded, so there is no flash of the old orientation.
 *
 * "Until the re-encoded file has loaded" is the whole trick, and it is not the
 * same moment as "until `version` changes". The version bumps the instant the
 * server answers, which only points the <img> at a new URL; the browser keeps
 * painting the OLD bytes until that fetch comes back. So the spin is tracked
 * against the version it was applied to and cleared by the load event of a
 * LATER one — never by the version prop on its own, which fires a frame after
 * the rotate lands and produced the exact flash described above on every
 * single rotate: turned, snapped back, turned again.
 */

// The `?v=` an <img> actually painted, read off the element's own src. React
// state has by then moved on to the version being fetched, and asking it
// instead is the confusion this component exists to avoid.
function loadedVersion(src) {
  const m = /[?&]v=(\d+)/.exec(src || "");
  return m ? Number(m[1]) : null;
}

export function PhotoTile({
  sessionId, name, version, index, total, onDelete, onRotate, onEdit,
  onMakeMain, onMove, reorderable,
}) {
  const [rotating, setRotating] = useState(false);
  // Degrees applied optimistically, ahead of the server.
  const [spin, setSpin] = useState(0);
  // The photo version those degrees sit ON TOP OF — i.e. the bytes the spin
  // is correcting. `settle` below compares the version that actually painted
  // against this one, which is what makes "the turn is in the file now" a
  // fact rather than a guess about timing.
  const spunOn = useRef(version);
  const isMain = index === 0;

  // Belt and braces: if the load event never arrives — a decode failure, the
  // new file 404ing, a load the browser coalesces away — the CSS turn still
  // has to come off, or the tile sits at 180 degrees for a 90 degree rotate.
  // Armed only while a spin is outstanding against a version that has already
  // been superseded, so in the normal case (onLoad settles it a few hundred
  // milliseconds later) this arms and clears without ever firing.
  useEffect(() => {
    if (!spin || version === spunOn.current) return undefined;
    const timer = setTimeout(() => {
      spunOn.current = version;
      setSpin(0);
    }, 8000);
    return () => clearTimeout(timer);
  }, [spin, version]);

  const rotate = async () => {
    if (rotating || !onRotate) return;
    setRotating(true);
    // Restored on failure along with the degrees: a spin that is taken back
    // must leave the tile pointing at the same bytes it was before, or the
    // next load settles the wrong turn against the wrong file.
    const before = spunOn.current;
    spunOn.current = version;
    setSpin((deg) => deg + 90);
    try {
      await onRotate();
    } catch (e) {
      // The saved photo did not turn, so neither should the tile. This is
      // only reachable because rotateImage rethrows after its toast.
      spunOn.current = before;
      setSpin((deg) => deg - 90);
    } finally {
      setRotating(false);
    }
  };

  // The re-encoded file is on screen, so the turn is in the pixels and the
  // CSS one comes off. Guarded on the version that ACTUALLY loaded: the
  // tile's first load, and any reload of the version the spin was applied
  // to, is still the un-turned photo, and clearing there is the flash this
  // is built to avoid. onError settles too — a turn held over a broken image
  // is meaningless, and would otherwise never be cleared.
  const settle = (e) => {
    const loaded = loadedVersion(e?.currentTarget?.src);
    if (loaded === null || loaded === spunOn.current) return;
    spunOn.current = loaded;
    setSpin(0);
  };

  const corner = "z-10 grid place-items-center size-8 rounded-full "
    + "bg-card/90 backdrop-blur border border-line text-ink-faint shadow-card "
    + "cursor-pointer transition-colors duration-150 "
    + "disabled:opacity-35 disabled:pointer-events-none";

  return (
    <div className={cn(
      "relative group rounded-tile overflow-hidden border bg-bg-sunken aspect-square",
      isMain ? "border-blue/60 ring-1 ring-blue/30" : "border-line",
    )}>
      <img
        src={`${mediaUrl(sessionId, name)}?v=${version}`}
        alt=""
        draggable={false}
        onLoad={settle}
        onError={settle}
        style={spin ? { transform: `rotate(${spin}deg)` } : undefined}
        className="size-full object-cover"
      />
      {/* Delete — always visible, no hover needed (works on touch). */}
      <button
        type="button"
        onClick={onDelete}
        aria-label={`Delete photo ${index + 1}`}
        title="Delete photo"
        className={cn(corner, "absolute top-1.5 left-1.5 hover:text-error hover:border-error/40")}
      >
        <Trash2 size={14} aria-hidden />
      </button>
      {/* Rotate 90° clockwise. */}
      {onRotate && (
        <button
          type="button"
          onClick={rotate}
          disabled={rotating}
          aria-label={`Rotate photo ${index + 1}`}
          title="Rotate 90°"
          className={cn(corner, "absolute top-1.5 right-1.5 hover:text-blue hover:border-blue/40")}
        >
          <RotateCw size={14} aria-hidden />
        </button>
      )}
      {/* The eBay gallery shot. A badge once it is, a one-tap promotion until
          then — which is the ordering question sellers actually have. */}
      {isMain ? (
        <span className="absolute bottom-1.5 left-1.5 z-10 px-2 py-0.5 rounded-full
          bg-blue text-on-accent text-[10px] font-bold tracking-wide shadow-card select-none">
          Main
        </span>
      ) : reorderable && (
        <button
          type="button"
          onClick={onMakeMain}
          aria-label={`Make photo ${index + 1} the main image`}
          title="Use as the main photo"
          className="absolute bottom-1.5 left-1.5 z-10 inline-flex items-center gap-1
            h-8 px-2.5 rounded-full bg-card/90 backdrop-blur border border-line
            text-[11px] font-bold text-ink-faint shadow-card cursor-pointer
            hover:text-blue hover:border-blue/40 transition-colors duration-150"
        >
          <Star size={12} aria-hidden /> Main
        </button>
      )}
      {/* Nudge one place. */}
      {reorderable && (
        <span className="absolute bottom-1.5 right-1.5 z-10 flex items-center gap-1">
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={index === 0}
            aria-label={`Move photo ${index + 1} earlier`}
            title="Move earlier"
            className={cn(corner, "hover:text-blue hover:border-blue/40")}
          >
            <ChevronLeft size={14} aria-hidden />
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={index === total - 1}
            aria-label={`Move photo ${index + 1} later`}
            title="Move later"
            className={cn(corner, "hover:text-blue hover:border-blue/40")}
          >
            <ChevronRight size={14} aria-hidden />
          </button>
        </span>
      )}
      <div
        className="ph-ov absolute inset-0 bg-ink/0 group-hover:bg-ink/25 transition-colors duration-200
          flex items-center justify-center p-2.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100"
      >
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex items-center gap-1.5 h-8 px-3.5 rounded-full bg-card text-ink text-xs font-semibold shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
        >
          <Pencil size={13} aria-hidden /> Edit
        </button>
      </div>
    </div>
  );
}
