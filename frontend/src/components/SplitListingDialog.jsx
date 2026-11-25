import { useState } from "react";
import { AlertTriangle, Check, Split } from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Textarea, Toggle } from "@/components/ui/fields";

/* SplitListingDialog — the inverse of the merge, for a draft that was
   written for two items.

   The uploader has a bulk toggle, and a seller who forgot it got one draft
   with both items' photos and a title that names both. This takes the draft
   apart: tap the photos that belong to the OTHER item, and they move to a new
   draft the AI writes from them. By default the AI rewrites this draft too,
   from the photos it keeps, because its text was about both — that is a
   switch, for the seller who has already fixed the text by hand.

   Two optional boxes say which item is which, the same line the guidance
   step asks for: with "the bottle is a 1970s Perrier-Jouët" under the new
   draft, the AI is not guessing which of the two it is writing about.

   Presentational. `onSplit(body)` is the caller's and it THROWS on failure,
   which keeps the dialog up with the ticks in place so trying again is one
   tap; the message is shown under the buttons. */

// Mirrors the guidance step's cap (listing_prompt.ITEM_NOTES_MAX_CHARS).
const MAX_NOTE_CHARS = 600;

function Tile({ sessionId, name, index, picked, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={picked}
      aria-label={`Photo ${index + 1}: ${picked ? "the other item" : "this listing"}`}
      title={picked ? "Goes to the new draft — tap to keep it here"
        : "Stays on this listing — tap to move it to the new draft"}
      className={cn(
        "relative aspect-square rounded-tile overflow-hidden border-2 bg-bg-sunken",
        "cursor-pointer transition-colors duration-150 focus:outline-none",
        "focus-visible:ring-2 focus-visible:ring-blue focus-visible:ring-offset-1",
        picked ? "border-blue" : "border-transparent hover:border-line-strong",
      )}
    >
      <img src={mediaUrl(sessionId, name, 1)} alt="" loading="lazy"
        className={cn("size-full object-cover transition-opacity",
          picked ? "opacity-100" : "opacity-80")} />
      <span
        aria-hidden
        className={cn(
          "absolute top-1.5 left-1.5 grid place-items-center size-6 rounded-full",
          "border shadow-card backdrop-blur text-[11px] font-bold",
          picked ? "bg-blue border-blue text-on-accent"
            : "bg-card/85 border-line text-ink-faint",
        )}
      >
        {picked ? <Check size={13} /> : index + 1}
      </span>
    </button>
  );
}

export function SplitListingDialog({ open, onClose, sessionId, images, title, onSplit }) {
  const all = images || [];
  const [picked, setPicked] = useState(() => new Set());
  const [redraft, setRedraft] = useState(true);
  const [notes, setNotes] = useState("");
  const [keepNotes, setKeepNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const moving = all.filter((n) => picked.has(n));
  const staying = all.length - moving.length;
  // Both drafts need a photo: a draft with none cannot be written.
  const ready = moving.length > 0 && staying > 0;

  const toggle = (name) => setPicked((cur) => {
    const next = new Set(cur);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return next;
  });

  const split = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSplit({
        photos: moving,
        redraft,
        notes: notes.trim(),
        ...(redraft ? { keep_notes: keepNotes.trim() } : {}),
      });
    } catch (e) {
      setError(e?.message || "Couldn't split this listing.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="Split into two listings" wide>
      <div className="flex flex-col gap-4">
        <p className="text-sm text-ink-secondary">
          Tap the photos that belong to the <strong className="text-ink">other item</strong>.
          They move to a new draft, and the AI writes that listing from them.
          {title ? <> This listing (<em>{title}</em>) keeps the rest.</> : " This listing keeps the rest."}
        </p>

        <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 gap-2">
          {all.map((name, i) => (
            <Tile key={name} sessionId={sessionId} name={name} index={i}
              picked={picked.has(name)} onToggle={() => toggle(name)} />
          ))}
        </div>

        <p className="text-[13px] text-ink-secondary tabular-nums" aria-live="polite">
          {moving.length === 0
            ? "Nothing picked yet — tap the other item's photos."
            : `${moving.length} photo${moving.length === 1 ? "" : "s"} move to the new draft · `
              + `${staying} stay${staying === 1 ? "s" : ""} here`}
          {moving.length > 0 && staying === 0 && " — leave at least one on this listing."}
        </p>

        <Field label="What is the other item?" hint="optional"
          help={"One line about the item that's moving — brand, what it is, size, a "
            + "flaw. The AI reads it as the strongest hint it has, and won't invent "
            + "details it doesn't cover."}>
          <Textarea rows={2} value={notes} maxLength={MAX_NOTE_CHARS}
            disabled={busy}
            onChange={(e) => setNotes(e.target.value.slice(0, MAX_NOTE_CHARS))}
            placeholder="e.g. a 1970s Perrier-Jouët champagne bottle, hand painted, empty" />
        </Field>

        <Toggle checked={redraft} onChange={setRedraft} disabled={busy}
          label="Rewrite this listing from the photos it keeps"
          note={"Its title and description were written for both items. Off, the text "
            + "stays exactly as it is and only the photos leave."} />

        {redraft && (
          <Field label="And what does this listing keep?" hint="optional">
            <Textarea rows={2} value={keepNotes} maxLength={MAX_NOTE_CHARS}
              disabled={busy}
              onChange={(e) => setKeepNotes(e.target.value.slice(0, MAX_NOTE_CHARS))}
              placeholder="e.g. a blue Lacoste polo, men's L, small mark on the cuff" />
          </Field>
        )}

        {error && (
          <p className="flex items-start gap-1.5 text-[13px] text-error">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden /> {error}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          <p className="text-[12.5px] text-ink-faint mr-auto">
            {redraft ? "Two AI drafts: one each." : "One AI draft, for the new listing."}
          </p>
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button variant="primary" onClick={split} loading={busy} disabled={!ready}>
            <Split aria-hidden /> Split into two listings
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
