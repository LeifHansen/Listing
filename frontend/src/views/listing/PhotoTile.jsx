import { motion } from "framer-motion";
import { Trash2, Brush, Crop, ChevronLeft, ChevronRight } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";

// PhotoTile — an uploaded photo as a rounded card with hover actions.
// Draggable to reorder (arrow buttons cover touch); index 0 is the cover.
export function PhotoTile({
  sessionId, name, version, index, count, isCover, dragging,
  onDelete, onEdit, onSmartCrop, onMove, onDragStart, onDragEnter, onDragEnd,
}) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.2 }}
      draggable
      onDragStart={onDragStart}
      onDragEnter={onDragEnter}
      onDragOver={(e) => e.preventDefault()}
      onDragEnd={onDragEnd}
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
        className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.04]"
      />
      {isCover && (
        <span className="absolute top-1.5 left-1.5 rounded-full bg-blue text-on-accent text-[10px] font-bold px-2 py-0.5 shadow-card">
          Cover
        </span>
      )}
      {/* Reorder arrows — also the touch path, since HTML5 drag is mouse-only. */}
      <div className="absolute top-1.5 right-1.5 flex gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity duration-150">
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
        className="absolute inset-0 bg-ink/0 group-hover:bg-ink/25 transition-colors duration-200
          flex items-end justify-center gap-2 p-3 opacity-0 group-hover:opacity-100 focus-within:opacity-100"
      >
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-card text-ink text-xs font-semibold shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
        >
          <Brush size={13} aria-hidden /> Clean up
        </button>
        <button
          type="button"
          onClick={onSmartCrop}
          className="inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-card text-ink text-xs font-semibold shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
        >
          <Crop size={13} aria-hidden /> Smart crop
        </button>
        <button
          type="button"
          onClick={onDelete}
          aria-label={`Delete photo ${name}`}
          className="grid place-items-center size-9 rounded-full bg-card text-error shadow-float cursor-pointer hover:-translate-y-0.5 transition-transform duration-150"
        >
          <Trash2 size={15} aria-hidden />
        </button>
      </div>
    </motion.div>
  );
}
