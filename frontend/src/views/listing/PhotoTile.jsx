import { useEffect, useState } from "react";
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
 */
export function PhotoTile({
  sessionId, name, version, index, total, onDelete, onRotate, onEdit,
  onMakeMain, onMove, reorderable,
}) {
  const [rotating, setRotating] = useState(false);
  // Degrees applied optimistically, ahead of the server.
  const [spin, setSpin] = useState(0);
  const isMain = index === 0;

  // Belt and braces: if a load event is ever missed, a version bump still
  // clears the optimistic spin rather than leaving the tile turned twice.
  useEffect(() => { setSpin(0); }, [version]);

  const rotate = async () => {
    if (rotating || !onRotate) return;
    setRotating(true);
    setSpin((deg) => deg + 90);
    try {
      await onRotate();
    } catch (e) {
      setSpin((deg) => deg - 90);
    } finally {
      setRotating(false);
    }
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
        onLoad={() => setSpin(0)}
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
