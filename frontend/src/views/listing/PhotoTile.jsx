import { useState } from "react";
import { motion } from "framer-motion";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Trash2, Pencil, RotateCw, GripVertical } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";

// PhotoTile — an uploaded photo as a rounded card with a single hover "Edit"
// action (opens the photo studio: clean up, remove background, crop) plus
// always-visible one-tap corners: rotate (right) and delete (left), and a
// drag handle (bottom-right) to reorder. The first photo is the eBay hero, so
// it wears a "Main" badge.
export function PhotoTile({
  sessionId, name, version, index, onDelete, onRotate, onEdit, reorderable,
}) {
  const [rotating, setRotating] = useState(false);
  // Reordering runs on @dnd-kit rather than the hand-rolled pointer-capture +
  // elementFromPoint drag this replaced. That version hit-tested the grid on
  // every pointer move, which is why dragging stuttered; it fought the page's
  // own scrolling on touch, which is why a drag on a phone half the time
  // scrolled instead; and being pointer events only, it was unreachable by
  // keyboard entirely.
  const {
    attributes, listeners, setNodeRef, setActivatorNodeRef, transform,
    transition, isDragging,
  } = useSortable({ id: name });
  const rotate = async () => {
    if (rotating || !onRotate) return;
    setRotating(true);
    try { await onRotate(); } finally { setRotating(false); }
  };
  return (
    <motion.div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.2 }}
      className={cn(
        "relative group rounded-tile overflow-hidden border bg-bg-sunken aspect-square",
        isDragging ? "z-20 border-blue ring-2 ring-blue shadow-float opacity-90"
          : "border-line",
      )}
    >
      <img
        src={`${mediaUrl(sessionId, name)}?v=${version}`}
        alt=""
        draggable={false}
        className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.04]"
      />
      {/* One-click delete — always visible, no hover needed (works on touch). */}
      <button
        type="button"
        onClick={onDelete}
        aria-label={`Delete photo ${name}`}
        title="Delete photo"
        className="absolute top-1.5 left-1.5 z-10 grid place-items-center size-7 rounded-full
          bg-card/85 backdrop-blur border border-line text-ink-faint shadow-card cursor-pointer
          hover:text-error hover:border-error/40 transition-colors duration-150"
      >
        <Trash2 size={13} aria-hidden />
      </button>
      {/* One-click rotate 90° — always visible, top-right. */}
      {onRotate && (
        <button
          type="button"
          onClick={rotate}
          disabled={rotating}
          aria-label={`Rotate photo ${name}`}
          title="Rotate 90°"
          className={cn(
            "absolute top-1.5 right-1.5 z-10 grid place-items-center size-7 rounded-full",
            "bg-card/85 backdrop-blur border border-line text-ink-faint shadow-card cursor-pointer",
            "hover:text-blue hover:border-blue/40 transition-colors duration-150",
            rotating && "animate-spin text-blue",
          )}
        >
          <RotateCw size={13} aria-hidden />
        </button>
      )}
      {/* Hero indicator — the first photo is eBay's gallery/main image. */}
      {index === 0 && (
        <span className="absolute bottom-1.5 left-1.5 z-10 px-2 py-0.5 rounded-full
          bg-blue text-on-accent text-[10px] font-bold tracking-wide shadow-card select-none">
          Main
        </span>
      )}
      {/* Drag handle. 44x44 hit area (Apple's and Google's minimum) with the
          visible pill inside it: at the old 28px this was a coin-sized target
          on a phone, which is most of why reordering felt like a fight.
          touch-none stops the browser claiming the gesture for scrolling. */}
      {reorderable && (
        <button
          ref={setActivatorNodeRef}
          type="button"
          aria-label={`Reorder photo ${index + 1}. Press space, then use the arrow keys.`}
          title="Drag to reorder"
          {...attributes}
          {...listeners}
          className={cn(
            "absolute -bottom-1 -right-1 z-10 grid place-items-center size-11 touch-none",
            "text-ink-faint hover:text-blue transition-colors duration-150",
            isDragging ? "cursor-grabbing text-blue" : "cursor-grab",
          )}
        >
          <span className={cn(
            "grid place-items-center size-7 rounded-full bg-card/85 backdrop-blur",
            "border shadow-card pointer-events-none",
            isDragging ? "border-blue/40" : "border-line",
          )}>
            <GripVertical size={14} aria-hidden />
          </span>
        </button>
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
    </motion.div>
  );
}
