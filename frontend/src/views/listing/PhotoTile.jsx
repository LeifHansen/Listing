import { useState } from "react";
import { motion } from "framer-motion";
import {
  Trash2, Brush, Crop, ChevronLeft, ChevronRight, RotateCw, Loader2,
} from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";

// PhotoTile — an uploaded photo as a rounded card. Click opens the editor;
// drag to reorder (arrow buttons cover touch); index 0 is the cover.
// NOTE the outer element is a PLAIN div: native HTML5 drag handlers
// (onDragStart/onDragEnd) must not sit on a motion.div, because Framer Motion
// intercepts those prop names for its own gesture system and the DOM events
// never fire — which silently broke drag-to-reorder.
export function PhotoTile({
  sessionId, name, version, index, count, isCover, dragging,
  onDelete, onEdit, onSmartCrop, onRotate, onMove, onDragStart, onDragEnter, onDragEnd,
}) {
  const [rotating, setRotating] = useState(false);
  const rotate = async () => {
    if (rotating) return;
    setRotating(true);
    try { await onRotate(); } finally { setRotating(false); }
  };
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDragEnd={onDragEnd}
    >
      <motion.div
        layout
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.9 }}
        transition={{ duration: 0.2 }}
        className={cn(
          "relative group rounded-tile overflow-hidden border bg-bg-sunken aspect-square",
          "cursor-grab active:cursor-grabbing",
          dragging ? "border-blue ring-2 ring-blue/40 opacity-70" : "border-line",
        )}
      >
        <img
          src={`${mediaUrl(sessionId, name)}?v=${version}`}
          alt=""
          draggable={false}
          onClick={onEdit}
          className="size-full object-contain transition-transform duration-200 group-hover:scale-[1.03] cursor-zoom-in"
        />
        {isCover && (
          <span className="absolute top-1.5 left-1.5 rounded-full bg-blue text-on-accent text-[10px] font-bold px-2 py-0.5 shadow-card pointer-events-none">
            Cover
          </span>
        )}
        {/* Rotate + reorder. Rotate is always available; arrows cover touch
            reordering since HTML5 drag is mouse-only. */}
        <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-100 [@media(hover:hover)]:opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity duration-150">
          <button
            type="button"
            aria-label="Rotate photo 90 degrees"
            title="Rotate"
            onClick={rotate}
            disabled={rotating}
            className="grid place-items-center size-7 rounded-full bg-card/90 text-ink shadow-card cursor-pointer disabled:cursor-wait"
          >
            {rotating
              ? <Loader2 size={14} className="animate-spin" aria-hidden />
              : <RotateCw size={14} aria-hidden />}
          </button>
          {index > 0 && (
            <button
              type="button"
              aria-label="Move photo earlier"
              onClick={() => onMove(index, index - 1)}
              className="grid place-items-center size-7 rounded-full bg-card/90 text-ink shadow-card cursor-pointer"
            >
              <ChevronLeft size={14} aria-hidden />
            </button>
          )}
          {index < count - 1 && (
            <button
              type="button"
              aria-label="Move photo later"
              onClick={() => onMove(index, index + 1)}
              className="grid place-items-center size-7 rounded-full bg-card/90 text-ink shadow-card cursor-pointer"
            >
              <ChevronRight size={14} aria-hidden />
            </button>
          )}
        </div>
        <div
          className="absolute inset-x-0 bottom-0 pointer-events-none
            flex items-end justify-center gap-2 p-3 transition-opacity duration-200
            opacity-100 [@media(hover:hover)]:opacity-0 group-hover:opacity-100 focus-within:opacity-100"
        >
          <button
            type="button"
            onClick={onEdit}
            className="pointer-events-auto inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-card text-ink text-xs font-semibold shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
          >
            <Brush size={13} aria-hidden /> Clean up
          </button>
          <button
            type="button"
            onClick={onSmartCrop}
            className="pointer-events-auto inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-card text-ink text-xs font-semibold shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
          >
            <Crop size={13} aria-hidden /> Smart crop
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={`Delete photo ${name}`}
            className="pointer-events-auto grid place-items-center size-9 rounded-full bg-card text-error shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
          >
            <Trash2 size={15} aria-hidden />
          </button>
        </div>
      </motion.div>
    </div>
  );
}
