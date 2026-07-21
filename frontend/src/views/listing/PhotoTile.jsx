import { useState } from "react";
import { motion } from "framer-motion";
import { Trash2, Pencil, RotateCw, GripVertical } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";

// PhotoTile — an uploaded photo as a rounded card with a single hover "Edit"
// action (opens the photo studio: clean up, remove background, crop) plus
// always-visible one-tap corners: rotate (right) and delete (left), and a
// drag handle (bottom-right) to reorder. The first photo is the eBay hero, so
// it wears a "Main" badge.
export function PhotoTile({
  sessionId, name, version, index, onDelete, onRotate, onEdit,
  reorderable, dragging, onDragStart, onDragMove, onDragEnd,
}) {
  const [rotating, setRotating] = useState(false);
  const rotate = async () => {
    if (rotating || !onRotate) return;
    setRotating(true);
    try { await onRotate(); } finally { setRotating(false); }
  };
  return (
    <motion.div
      layout="position"
      data-photo-idx={index}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: dragging ? 1.05 : 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.2 }}
      className={cn(
        "relative group rounded-tile overflow-hidden border bg-bg-sunken aspect-square",
        dragging ? "z-20 border-blue ring-2 ring-blue shadow-float" : "border-line",
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
      {/* Drag handle — reorder photos. touch-none so a drag doesn't scroll the
          page on mobile; pointer capture routes move/up back here. */}
      {reorderable && (
        <button
          type="button"
          aria-label={`Drag to reorder photo ${name}`}
          title="Drag to reorder"
          onPointerDown={onDragStart}
          onPointerMove={onDragMove}
          onPointerUp={onDragEnd}
          onPointerCancel={onDragEnd}
          className={cn(
            "absolute bottom-1.5 right-1.5 z-10 grid place-items-center size-7 rounded-full touch-none",
            "bg-card/85 backdrop-blur border border-line text-ink-faint shadow-card",
            "hover:text-blue hover:border-blue/40 transition-colors duration-150",
            dragging ? "cursor-grabbing text-blue border-blue/40" : "cursor-grab",
          )}
        >
          <GripVertical size={14} aria-hidden />
        </button>
      )}
      <div
        className="absolute inset-0 bg-ink/0 group-hover:bg-ink/25 transition-colors duration-200
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
