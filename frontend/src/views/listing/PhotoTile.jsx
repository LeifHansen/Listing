import { motion } from "framer-motion";
import { Trash2, Brush, Crop } from "lucide-react";
import { mediaUrl } from "@/lib/utils";

// PhotoTile — an uploaded photo as a rounded card with hover actions.
export function PhotoTile({ sessionId, name, version, onDelete, onEdit, onSmartCrop }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9 }}
      transition={{ duration: 0.2 }}
      className="relative group rounded-tile overflow-hidden border border-line bg-bg-sunken aspect-square"
    >
      <img
        src={`${mediaUrl(sessionId, name)}?v=${version}`}
        alt=""
        className="size-full object-cover transition-transform duration-200 group-hover:scale-[1.04]"
      />
      {/* One-click delete — always visible, no hover needed (works on touch). */}
      <button
        type="button"
        onClick={onDelete}
        aria-label={`Delete photo ${name}`}
        title="Delete photo"
        className="absolute top-1.5 right-1.5 z-10 grid place-items-center size-7 rounded-full
          bg-card/85 backdrop-blur border border-line text-ink-faint shadow-card cursor-pointer
          hover:text-error hover:border-error/40 transition-colors duration-150"
      >
        <Trash2 size={13} aria-hidden />
      </button>
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
      </div>
    </motion.div>
  );
}
