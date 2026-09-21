import { useRef, useState } from "react";
import { motion } from "framer-motion";
import { MessageSquareText, Sparkles, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { apiUrl } from "@/lib/platform";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Field, Textarea } from "@/components/ui/fields";

/* The guidance step: the last moment before the AI starts guessing.
 *
 * By the time this is on screen the photos are optimized and — in a batch —
 * already sorted into items, and nothing has been drafted. Everything the
 * draft is about to get wrong (the brand on a worn-off label, which of two
 * near-identical polos is the large, the mark on the left cuff, what it
 * actually IS) is known to the person holding the thing. Asking here costs
 * one line of typing; the alternative is correcting it afterwards, field by
 * field, across every draft in the pile.
 *
 * Every box is optional, and submitting with all of them blank produces
 * exactly the drafts a run without this step would have. That is deliberate:
 * this is a question, not a gate, so there is one button and no way to get
 * stuck behind it.
 *
 * Presentational on purpose — it takes the paused job's `pending_items`
 * straight off the status and hands back what was typed. Both flows use it:
 * the uploader (one item, one session) and the batch screen (one per group).
 *
 * And CONTROLLED, which is the one thing about it that is not obvious. What
 * the seller types has to live in the caller, because this component is
 * replaced by the progress card the moment the answer is sent — so state held
 * in here would be destroyed by exactly the failure it needs to survive. A
 * 503 on the submit has to come back to forty boxes still full of the
 * sentences the seller wrote, or the retry costs them the whole pile again.
 *
 * Deleted photos are the one thing held HERE, and for the opposite reason:
 * the server is their record, not this component. The moment the request
 * lands the photo is off the item for good, and every later read of the job
 * — the next poll, a reload, coming back tomorrow — already says so. What the
 * local set buys is the two seconds before that: the thumb goes at the tap,
 * and a batch screen polling every 1.5s cannot put it back on screen with a
 * status that was already in flight when the seller pressed it.
 */

// Mirrors listing_prompt.ITEM_NOTES_MAX_CHARS. Enforced here as well as on
// the server so the box stops taking characters it is about to drop, rather
// than silently truncating a line the seller watched themselves type.
export const MAX_ITEM_NOTES_CHARS = 600;

// One item's photos, as a row of thumbs. The paused status carries up to
// eight URLs and the true count, so a pile of twelve shots says so instead of
// quietly showing eight.
//
// Each thumb carries its own delete, because this is the one moment a bad
// shot is free to drop: the photos are optimized and grouped and NOTHING has
// been drafted from them yet, so the blurred one, the one of the floor and
// the receipt that got swept up with the pile cost nothing here and cost a
// draft plus an edit anywhere later. The button is always visible rather than
// on hover — half these sellers are on a phone, where there is no hover and
// an invisible control is no control at all.
function PhotoRow({ item, onDelete, deleted }) {
  const all = item.photos || [];
  const photos = all.filter((src) => !deleted.has(src));
  // What the row is NOT showing. Counted off the item's true total with this
  // row's own deletions taken out, so a twelve-shot item that has just lost
  // one of its visible eight still says "+4 more" — the four it never showed
  // are still there, and only the deleted one is gone.
  const gone = all.length - photos.length;
  const hidden = Math.max(
    0, (item.photo_count || all.length) - gone - photos.length);
  // The last photo stays. An item with none cannot be drafted at all, and the
  // server refuses it for that reason — this says so before the tap rather
  // than after it.
  const last = photos.length + hidden <= 1;
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {photos.map((src, i) => (
        <div key={src} className="relative shrink-0">
          <img
            src={apiUrl(`${src}?v=1`)}
            alt=""
            loading="lazy"
            className="size-16 rounded-[10px] object-cover border border-line"
            // Same as the batch screen's cards: a photo the volume has let go
            // must leave a gap, not a broken-image icon over the seller's item.
            onError={(e) => { e.currentTarget.style.display = "none"; }}
          />
          {onDelete && (
            <button
              type="button"
              onClick={() => onDelete(src)}
              disabled={last}
              aria-label={last
                ? `Remove photo ${i + 1} — not while it's the only one left`
                : `Remove photo ${i + 1}`}
              title={last
                ? "Every item needs at least one photo"
                : "Remove this photo — the AI won't see it"}
              className="absolute top-0.5 left-0.5 grid place-items-center size-6
                rounded-full bg-card/85 backdrop-blur border border-line text-ink-faint
                shadow-card cursor-pointer transition-colors duration-150
                hover:text-error hover:border-error/40
                disabled:opacity-35 disabled:pointer-events-none"
            >
              <Trash2 size={12} aria-hidden />
            </button>
          )}
        </div>
      ))}
      {hidden > 0 && (
        <span className="text-xs font-semibold text-ink-faint px-1">
          +{hidden} more
        </span>
      )}
    </div>
  );
}

export function AiNotesStep({ items, values, onChange, busy = false, onSubmit,
                              onDeletePhoto }) {
  // Keyed by the item's index in `pending_items` (`gi`), which is what the
  // server matches them back to — never by position in this array.
  const notes = values || {};
  const rows = items || [];
  const single = rows.length === 1;
  const typed = Object.values(notes).filter((t) => t && t.trim()).length;
  // Photo URLs this seller has dropped, by the exact string the status gave
  // them. Optimistic: see the note above the component.
  const [deleted, setDeleted] = useState(() => new Set());
  // Deletes go out one at a time. Each one rewrites the item's whole photo
  // list server-side, so two in flight together would be two rewrites of the
  // same list and the server refuses the second (it is written against the
  // list the first one changed) — which would put a photo back under the
  // hands of a seller who is clearly pruning. Queueing costs nothing: the
  // thumb is already gone at the tap, and the wait is behind it.
  const queue = useRef(Promise.resolve());

  const set = (gi, value) => onChange(gi, value.slice(0, MAX_ITEM_NOTES_CHARS));

  const drop = (gi, src) => {
    const forget = () => setDeleted((cur) => {
      const next = new Set(cur);
      next.delete(src);
      return next;
    });
    setDeleted((cur) => new Set(cur).add(src));
    // The catch is what keeps the queue alive AND is the undo: a delete the
    // server refused has to put the thumb back, or the seller submits a pile
    // believing a photo is out of it that isn't. The caller says why.
    queue.current = queue.current
      .then(() => onDeletePhoto(gi, src))
      .catch(forget);
  };

  const submit = async () => {
    if (busy) return;
    // Behind the deletes, so the answer is never the thing that races them:
    // submitting takes the job out of its pause, and a delete still in the
    // air would land on a job that has moved on and be refused. Resolved
    // already when nothing was deleted, which is the ordinary case.
    await queue.current;
    // Only what was actually typed. A box opened and left blank is the same
    // as one never touched, and the server drops empties anyway — this just
    // keeps the request honest about what the seller said.
    const out = {};
    rows.forEach(({ gi }) => {
      const text = (notes[gi] || "").trim();
      if (text) out[gi] = text;
    });
    onSubmit(out);
  };

  if (!rows.length) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className="flex flex-col gap-4"
    >
      <Card className="py-4 border-blue/30 bg-blue-soft">
        <div className="flex items-start gap-2.5">
          <MessageSquareText size={18} className="text-blue shrink-0 mt-0.5" aria-hidden />
          <div className="min-w-0">
            <h2 className="text-[15px] font-bold text-ink">
              {single
                ? "Anything the photos don't show?"
                : `Your photos are sorted into ${rows.length} items — anything the photos don't show?`}
            </h2>
            <p className="mt-1 text-[13px] text-ink-secondary">
              {single
                ? "Tell the AI what it's looking at before it writes the listing — "
                : "Tell the AI what each one is before it writes the listings — "}
              the brand on a worn-off label, the size, the era, a flaw worth
              mentioning. It reads this as the strongest hint it has about
              {single ? " your item" : " that item"}, and it&apos;s what stops
              you fixing the same field on every draft afterwards.
            </p>
          </div>
        </div>
      </Card>

      {rows.map((item, i) => {
        const value = notes[item.gi] || "";
        return (
          <Card key={item.gi} className="flex flex-col gap-3.5">
            <div className="flex items-baseline gap-2 flex-wrap">
              <p className="text-sm font-bold text-ink">
                {single ? "Your item" : `Item ${i + 1}`}
              </p>
              {/* The name the grouping pass gave it. Useful and not
                  authoritative — it is the AI's first guess, which is the
                  very thing this box exists to correct. */}
              {item.name && (
                <p className="text-[13px] text-ink-secondary min-w-0 truncate">
                  the AI thinks: {item.name}
                </p>
              )}
            </div>
            <PhotoRow
              item={item}
              deleted={deleted}
              onDelete={onDeletePhoto && !busy
                ? (src) => drop(item.gi, src) : undefined}
            />
            <Field
              label="Notes for the AI"
              hint="optional"
              help={"Anything you know that the photos don't show — brand, maker, "
                + "model, era, material, size, or a flaw worth disclosing. Write it "
                + "as a sentence; the AI treats it as a strong hint about this item "
                + "and won't invent details it doesn't cover."}
            >
              <Textarea
                rows={2}
                value={value}
                maxLength={MAX_ITEM_NOTES_CHARS}
                disabled={busy}
                // One item means the seller is here to type, so the cursor is
                // already in the box. With forty, it would be one scroll
                // position chosen for them out of forty.
                autoFocus={single}
                onChange={(e) => set(item.gi, e.target.value)}
                // The pile can be long, and reaching for the button at the
                // bottom of forty cards is the wrong ending to typing.
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
                }}
                placeholder={single
                  ? "e.g. men's L, bought in Tokyo 2019, small mark on the left cuff"
                  : "e.g. the darker one — Lacoste, size 5 (men's L), tiny hole by the hem"}
              />
            </Field>
            {value.length > MAX_ITEM_NOTES_CHARS - 100 && (
              <p className="-mt-2 text-xs text-ink-faint">
                {MAX_ITEM_NOTES_CHARS - value.length} characters left.
              </p>
            )}
          </Card>
        );
      })}

      {/* One button, and no way to get stuck behind it: leaving every box
          blank is the skip, and says so rather than needing a second control
          that means "throw away what I just typed". */}
      <div className={cn("flex flex-wrap items-center gap-3",
        rows.length > 2 && "sticky bottom-0 -mx-1 px-1 py-3 bg-bg/95 backdrop-blur")}>
        <Button variant="primary" size="lg" onClick={submit} loading={busy}>
          <Sparkles aria-hidden />
          {single ? "Write my listing" : `Write ${rows.length} listings`}
        </Button>
        <p className="text-[13px] text-ink-secondary min-w-0">
          {typed
            ? `${typed} of ${rows.length} filled in — the rest work from the photos alone.`
            : "Every box is optional — leave them blank and the AI works from the photos alone."}
        </p>
      </div>
    </motion.div>
  );
}
