import { Trash2 } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";

/* PhotoTile — one uploaded photo as a rounded card.
 *
 * The photo is the tile. The only control laid over it is the bin; the rest
 * of what a seller does here is done TO the tile rather than with buttons on
 * it: tap it to open the studio, drag it (press and hold, on a phone) to put
 * it somewhere else in the order, arrow keys to move it when focused. The
 * previous tile carried rotate, "Main", ‹ › and a hover Edit as well, and on
 * a phone-sized grid the six of them covered the picture they were about.
 *
 * The drag itself lives in useDragReorder, owned by the grid; this tile just
 * takes the handlers it is given. `focused` is `isMain` for the blue ring —
 * the first photo is the eBay gallery shot, and the ring plus the small badge
 * is how the grid says so without a button.
 */

export function PhotoTile({
  sessionId, name, version, index, total, onDelete, onEdit,
  dragProps, lifted, wasDrag,
}) {
  const isMain = index === 0;
  const label = `Photo ${index + 1} of ${total}${isMain ? ", the main photo" : ""}. `
    + `Enter to edit${total > 1 ? ", arrow keys to move" : ""}`;

  return (
    <div
      {...dragProps}
      className={cn(
        "relative rounded-tile overflow-hidden border bg-bg-sunken aspect-square select-none",
        "transition-opacity duration-150",
        isMain ? "border-blue/60 ring-1 ring-blue/30" : "border-line",
        // The slot a lifted photo came from: the ghost under the finger is
        // the photo, this is where it will land if nothing else is chosen.
        lifted && "opacity-30 border-dashed border-blue",
      )}
    >
      <button
        type="button"
        onClick={(e) => {
          // The release that ends a drag is also a click; it is not an ask
          // to open the studio.
          if (wasDrag?.()) { e.preventDefault(); return; }
          onEdit?.();
        }}
        aria-label={label}
        className="block size-full cursor-pointer focus:outline-none
          focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-inset
          [-webkit-touch-callout:none]"
      >
        <img
          src={`${mediaUrl(sessionId, name)}?v=${version}`}
          alt=""
          // A listing may carry up to MAX_PHOTOS (24) of these, and the Photos
          // card mounts them all at once. Without this the editor opens by
          // fetching and decoding every one, on a phone, before the seller
          // has scrolled to the second row.
          loading="lazy"
          draggable={false}
          className="size-full object-cover pointer-events-none [-webkit-touch-callout:none]"
        />
      </button>
      {/* Delete — always visible, no hover needed (works on touch). Marked so
          a press on it never starts a drag. */}
      <button
        type="button"
        data-no-drag
        onClick={onDelete}
        aria-label={`Delete photo ${index + 1}`}
        title="Delete photo"
        className="absolute top-1.5 left-1.5 z-10 grid place-items-center size-8 rounded-full
          bg-card/90 backdrop-blur border border-line text-ink-faint shadow-card
          cursor-pointer transition-colors duration-150 hover:text-error hover:border-error/40"
      >
        <Trash2 size={14} aria-hidden />
      </button>
      {isMain && (
        <span className="absolute bottom-1.5 left-1.5 z-10 px-2 py-0.5 rounded-full
          bg-blue text-on-accent text-[10px] font-bold tracking-wide shadow-card
          pointer-events-none">
          Main
        </span>
      )}
    </div>
  );
}
