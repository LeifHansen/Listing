import { useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle, Check, CheckCircle2, Combine, Loader2, MessageSquareText,
  Search, Send, Sparkles, Split, Trash2,
} from "lucide-react";
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
 *
 * Merging is the exception to "at the tap", on purpose. Folding two items
 * together is a new item — its photos, its name, its slot — that only the
 * server can say, and guessing at it here would mean undoing a guess. So the
 * cards stay as they are, ticked, until the answer lands, and what the seller
 * typed under each of them is carried into the one they became.
 *
 * The single-item question has the opposite escape, `onSplit`: the uploader's
 * bulk toggle left off puts two items' photos under ONE card here, and this
 * is the last moment that costs nothing to fix — the pile is sorted the way a
 * batch would have sorted it, and the question comes back per item. The line
 * typed under the one card goes with the request, as the seller's inventory
 * of the pile, which is the hint the sort reads first.
 */

/* THE REVIEW. Each card now shows what Google image search says the item is
 * and what it sells for (`google`, one answer per item off the paused job's
 * status, arriving as each search lands), with the box underneath it and one
 * button. The button is APPROVE while the box says nothing Google hasn't
 * already been asked with, and SUBMIT CHANGES the moment it does: the seller's
 * words go back to Google with the photo, the card shows the new answer, and
 * that is what they approve. The draft is written about the item Google named
 * and priced from Google's range, so approving here is approving the listing.
 *
 * One item: Approve writes it. Several: each is approved on its own card (or
 * all at once), and the batch is written when every one of them is.
 *
 * Without Google on the server (`googleEnabled` false) the card has no answer
 * to show, and the same two buttons keep their meaning: Submit Changes keeps
 * the note for the AI and approves, Approve approves.
 */

// Mirrors listing_prompt.ITEM_NOTES_MAX_CHARS. Enforced here as well as on
// the server so the box stops taking characters it is about to drop, rather
// than silently truncating a line the seller watched themselves type.
const MAX_ITEM_NOTES_CHARS = 600;

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

// A dollar range the way a seller says it: "$35–$55", "$1,200", "" for none.
function dollars(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "";
  return Number.isInteger(n)
    ? `$${n.toLocaleString("en-US")}`
    : `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function priceSpan(low, high) {
  const lo = dollars(low);
  const hi = dollars(high);
  if (!lo) return "";
  return !hi || hi === lo ? lo : `${lo}–${hi}`;
}

// Where a source link points, for its label when Google gave no name.
function hostOf(link) {
  try { return new URL(link).hostname.replace(/^www\./, ""); } catch { return ""; }
}

// What Google said about one item: the name, the price range, the answer in
// Google's own words and the pages it read. The seller is being asked to
// approve a listing on the strength of this, so every part of it is shown —
// the summary folded to a few lines, not cut.
export function GooglePanel({ entry, onRetry, retrying }) {
  const [open, setOpen] = useState(false);
  const status = entry?.status || "pending";
  if (status === "pending") {
    return (
      <div role="status"
        className="flex items-center gap-2.5 rounded-[12px] border border-line
          bg-bg-sunken px-3.5 py-3 text-[13px] text-ink-secondary">
        <Loader2 size={15} className="animate-spin text-blue shrink-0" aria-hidden />
        {entry?.hint
          ? "Asking Google again with your changes…"
          : "Asking Google what this is and what it sells for…"}
      </div>
    );
  }
  if (status !== "done") {
    return (
      <div className="flex flex-col gap-2 rounded-[12px] border border-warning/40
        bg-warning-soft px-3.5 py-3 text-[13px] text-ink">
        <p className="flex items-start gap-2">
          <AlertTriangle size={15} className="text-warning shrink-0 mt-0.5" aria-hidden />
          <span>
            {entry?.reason || "Google couldn't answer for this photo."}{" "}
            You can still approve it — the AI works from the photos and
            anything you write below.
          </span>
        </p>
        {onRetry && (
          <div>
            <Button variant="ghost" size="sm" onClick={onRetry} loading={retrying}>
              <Search aria-hidden /> Ask Google again
            </Button>
          </div>
        )}
      </div>
    );
  }
  const used = priceSpan(entry.used_low, entry.used_high);
  const fresh = priceSpan(entry.new_low, entry.new_high);
  const summary = (entry.summary || "").trim();
  const long = summary.length > 280 || summary.split("\n").length > 4;
  const sources = (entry.sources || []).filter((s) => /^https?:\/\//.test(s?.link || ""));
  return (
    <section aria-label="What Google says"
      className="flex flex-col gap-2 rounded-[12px] border border-blue/25
        bg-blue-soft/60 px-3.5 py-3">
      <p className="flex items-center gap-1.5 text-[11px] font-bold uppercase
        tracking-wide text-blue">
        <Search size={12} aria-hidden /> Google says
      </p>
      {entry.identified && (
        <p className="text-[15px] font-bold text-ink leading-snug">{entry.identified}</p>
      )}
      {used || fresh ? (
        <div className="flex flex-wrap gap-1.5">
          {used && (
            <span className="rounded-full bg-card border border-line px-2.5 py-0.5
              text-[12.5px] font-semibold text-ink">
              Used {used}
            </span>
          )}
          {fresh && (
            <span className="rounded-full bg-card border border-line px-2.5 py-0.5
              text-[12.5px] font-semibold text-ink">
              New {fresh}
            </span>
          )}
        </div>
      ) : (
        <p className="text-[12.5px] text-ink-secondary">
          Google didn&apos;t give a price — it will be priced from comparable
          eBay listings instead.
        </p>
      )}
      {entry.confidence === "low" && (
        <p className="flex items-start gap-1.5 text-[12.5px] text-ink">
          <AlertTriangle size={13} className="text-warning shrink-0 mt-0.5" aria-hidden />
          Google isn&apos;t sure about this one. If it&apos;s wrong, say what it
          is below and submit your changes.
        </p>
      )}
      {summary && (
        <p className={cn("text-[13px] text-ink-secondary whitespace-pre-line",
          long && !open && "line-clamp-4")}>
          {summary}
        </p>
      )}
      {long && (
        <button type="button" onClick={() => setOpen((v) => !v)}
          className="self-start text-[12.5px] font-semibold text-blue cursor-pointer
            hover:underline">
          {open ? "Show less" : "Show more"}
        </button>
      )}
      {sources.length > 0 && (
        <p className="text-[12px] text-ink-faint">
          From:{" "}
          {sources.map((src, i) => (
            <span key={src.link}>
              {i > 0 && " · "}
              <a href={src.link} target="_blank" rel="noopener noreferrer"
                title={src.title || src.link}
                className="underline decoration-line hover:text-ink">
                {src.source || hostOf(src.link) || "source"}
              </a>
            </span>
          ))}
        </p>
      )}
    </section>
  );
}

// An item's identity, as opposed to its slot. `gi` is where the server will
// look an answer up, and it is renumbered when an item above it is removed;
// `key` never is. Notes are held against the key so a removal cannot slide
// one item's words under its neighbour. (A server that predates removal sends
// no key, and there the slot never moves, so it serves as one.)
const idOf = (item) => item.key ?? item.gi;

// What the seller typed under several items, as the one line the item they
// became is drafted from. Each item's line was about the same thing (that is
// why they are being merged), so a line typed twice is said once — and the
// server reads the box as one line, so they are joined as sentences rather
// than on a newline it would flatten into a run-on.
export function joinItemNotes(parts) {
  const seen = new Set();
  const lines = [];
  (parts || []).forEach((p) => {
    const text = (p || "").trim();
    if (!text || seen.has(text.toLowerCase())) return;
    seen.add(text.toLowerCase());
    lines.push(text);
  });
  return lines
    .reduce((acc, text) => (!acc ? text
      : /[.!?;,:]$/.test(acc) ? `${acc} ${text}` : `${acc}. ${text}`), "")
    .slice(0, MAX_ITEM_NOTES_CHARS);
}

export function AiNotesStep({ items, values, onChange, onSubmit,
                              onDeletePhoto, onDeleteItem, onMergeItems,
                              onSplit, google, googleEnabled, onAskGoogle,
                              approved: approvedProp, onApprovedChange }) {
  const notes = values || {};
  const googleOn = !!googleEnabled;
  // Approvals, by item identity. Controlled when the caller holds them —
  // for the same reason it holds the notes: a failed submit remounts this
  // step, and forty approvals are not something to ask for twice — and
  // held here otherwise.
  const [ownApproved, setOwnApproved] = useState({});
  const approvals = approvedProp || ownApproved;
  const setApprovals = (fn) => {
    const next = fn(approvals);
    if (onApprovedChange) onApprovedChange(next);
    else setOwnApproved(next);
  };
  // "Submit Changes" answers at once with the card's new, pending lookup.
  // A poll already in the air when it landed still carries the OLD answer,
  // which must not flash back onto the card — so the pending one is held
  // here until the status catches up with it (same ticket).
  const [asked, setAsked] = useState({});
  const [asking, setAsking] = useState({});
  // What was submitted for each card when there is no Google to ask: the
  // note is kept for the AI and the card approved with it.
  const [submitted, setSubmitted] = useState({});
  // The rows as the last delete left them, once there has been one. While
  // the job is paused a delete is the ONLY thing that changes them, so its
  // answer is never older than a poll — but a poll already in the air when
  // it landed is, and would put a removed item back on screen.
  const [latest, setLatest] = useState(null);
  // Items this seller has removed, by identity. Optimistic, like `deleted`.
  const [goneItems, setGoneItems] = useState(() => new Set());
  const rows = (latest || items || []).filter((it) => !goneItems.has(idOf(it)));
  // Items ticked to be merged, by identity, and whether a merge is out.
  // Read through `rows` rather than on their own, so an item removed while
  // ticked is simply no longer part of the merge.
  const [picked, setPicked] = useState(() => new Set());
  const [merging, setMerging] = useState(false);
  const pickedRows = rows.filter((it) => picked.has(idOf(it)));
  const canMerge = !!onMergeItems && rows.length > 1;
  const single = rows.length === 1;
  // One card holding more than one photo can be two items. One photo cannot.
  const canSplit = !!onSplit && single
    && ((rows[0].photo_count || rows[0].photos?.length || 0) >= 2);
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
  // What the server last said the rows are, readable from inside the queue.
  // A removal renumbers every item after it, so the `gi` a tap saw can be out
  // of date by the time its turn comes — each queued request looks its item
  // up again here, by the photo or key it was aimed at, when it goes out.
  const known = useRef(null);
  const now = () => known.current || items || [];

  // The Google answer a card shows: the status's, unless this card has just
  // asked again and the status has not caught up yet.
  const entryFor = (item) => {
    const k = String(idOf(item));
    const polled = (google || {})[k];
    const mine = asked[k];
    if (mine && polled?.ticket !== mine.ticket) return mine;
    return polled;
  };
  // What the box says now, against what it said when Google was last asked
  // (or, with no Google, when it was last submitted). Different means the
  // seller has changes to send, and Approve becomes Submit Changes.
  const typedFor = (item) => (notes[idOf(item)] || "").trim();
  const sentFor = (item) => (googleOn
    ? (entryFor(item)?.hint || "")
    : (submitted[idOf(item)] || "")).trim();
  const changedFor = (item) => typedFor(item) !== sentFor(item);
  // Google is still working on this card: nothing to approve yet.
  const waitingFor = (item) => {
    if (!googleOn) return false;
    const entry = entryFor(item);
    return !entry || entry.status === "pending";
  };
  // An approval holds for the answer it was given to. A new answer (a photo
  // removed, a re-ask) or an edit to the box since takes it back.
  const stampFor = (item) => (googleOn ? (entryFor(item)?.ticket || "") : "ok");
  const isApproved = (item) => {
    const stamp = approvals[idOf(item)];
    return stamp !== undefined && stamp === stampFor(item)
      && !changedFor(item) && !waitingFor(item);
  };

  const set = (key, value) => onChange(key, value.slice(0, MAX_ITEM_NOTES_CHARS));

  const settle = (res) => {
    if (Array.isArray(res?.pending_items)) {
      known.current = res.pending_items;
      setLatest(res.pending_items);
    }
  };

  const drop = (key, src) => {
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
      .then(() => {
        const row = now().find((it) => idOf(it) === key);
        return onDeletePhoto(row ? row.gi : key, src);
      })
      .then(settle)
      .catch(forget);
  };

  // A whole item out of the batch. The same bargain as a photo: it goes at
  // the tap and comes back if the server says no, and since nothing has been
  // drafted from it there is nothing to confirm or to undo.
  const dropItem = (key) => {
    const forget = () => setGoneItems((cur) => {
      const next = new Set(cur);
      next.delete(key);
      return next;
    });
    setGoneItems((cur) => new Set(cur).add(key));
    queue.current = queue.current
      .then(() => {
        const row = now().find((it) => idOf(it) === key);
        // Sent with one of its photos: the server checks the slot still
        // holds this item, rather than removing whatever is there now.
        const photo = (row?.photos || []).find((p) => !deleted.has(p))
          || row?.photos?.[0];
        if (!row || !photo) throw new Error("gone");
        return onDeleteItem(row.gi, photo);
      })
      .then(settle)
      .catch(forget);
  };

  const togglePick = (key) => setPicked((cur) => {
    const next = new Set(cur);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    return next;
  });

  // Fold the ticked items into one. The grouping's usual mistake is one
  // thing shot from enough angles to look like two or three, and this is the
  // last moment that costs nothing to fix: after drafting it is a draft paid
  // for per piece, a merge dialog and the deletes.
  //
  // Queued behind the deletes like everything else that renumbers the rows,
  // and each item is looked up again when its turn comes — by identity, sent
  // with one of its own photos — so a removal that landed first cannot make
  // this merge whatever slid into those slots.
  const mergePicked = () => {
    const keys = pickedRows.map(idOf);
    if (keys.length < 2 || merging) return;
    // The notes as they stand at the tap. The ticked cards' boxes are locked
    // until the answer lands, so this is also what they say when it does.
    const atTap = Object.fromEntries(keys.map((k) => [k, notes[k] || ""]));
    setMerging(true);
    queue.current = queue.current
      .then(async () => {
        const chosen = now().filter((it) => keys.includes(idOf(it)));
        const sent = chosen.map((row) => ({
          gi: row.gi,
          photo: (row.photos || []).find((p) => !deleted.has(p))
            || row.photos?.[0],
        }));
        if (sent.length < 2 || sent.some((it) => !it.photo)) {
          throw new Error("gone");
        }
        const res = await onMergeItems(sent);
        settle(res);
        // The item they became keeps the first one's identity, which is
        // where its notes live — so the others' lines join it there.
        const into = res?.pending_items?.[res?.merged];
        const survivor = into ? idOf(into) : idOf(chosen[0]);
        const text = joinItemNotes(chosen.map((row) => atTap[idOf(row)]));
        chosen.forEach((row) => {
          const k = idOf(row);
          if (k === survivor) {
            if (text !== (atTap[k] || "")) set(k, text);
          } else if (atTap[k]) {
            set(k, "");
          }
        });
        setPicked(new Set());
      })
      // Refused, or no longer there to merge: the cards stay as they were and
      // still ticked, so trying again is one tap. The caller says why.
      .catch(() => {})
      .finally(() => setMerging(false));
  };

  const approve = (item) => {
    if (waitingFor(item) || changedFor(item)) return;
    const stamp = stampFor(item);
    setApprovals((cur) => ({ ...cur, [idOf(item)]: stamp }));
    // One item: approving it IS writing the listing.
    if (single) submit();
  };

  const unapprove = (item) => setApprovals((cur) => {
    const next = { ...cur };
    delete next[idOf(item)];
    return next;
  });

  // Every card that can be approved as it stands — answered, and with no
  // changes in its box waiting to be sent to Google. Without Google there is
  // nothing to send them to, so a typed note is taken as submitted and its
  // card approved with it: the note goes to the AI either way.
  const approveAll = () => {
    if (!googleOn) {
      setSubmitted((cur) => {
        const next = { ...cur };
        rows.forEach((it) => { next[idOf(it)] = typedFor(it); });
        return next;
      });
    }
    setApprovals((cur) => {
      const next = { ...cur };
      rows.forEach((it) => {
        if (!waitingFor(it) && (!googleOn || !changedFor(it))) {
          next[idOf(it)] = stampFor(it);
        }
      });
      return next;
    });
  };

  // "Submit Changes": the seller's words go back to Google with the photo,
  // and the card waits for the new answer. Queued behind the deletes like
  // every other request that names a slot, and the slot looked up again
  // when its turn comes.
  const submitChanges = (item) => {
    const key = idOf(item);
    const text = typedFor(item);
    if (!googleOn || !onAskGoogle) {
      setSubmitted((cur) => ({ ...cur, [key]: text }));
      setApprovals((cur) => ({ ...cur, [key]: "ok" }));
      if (single) submit();
      return;
    }
    setAsking((cur) => ({ ...cur, [key]: true }));
    queue.current = queue.current
      .then(async () => {
        const row = now().find((it) => idOf(it) === key);
        const photo = (row?.photos || []).find((p) => !deleted.has(p))
          || row?.photos?.[0];
        if (!row || !photo) throw new Error("gone");
        const res = await onAskGoogle(row.gi, photo, text);
        const entry = res?.google?.[String(key)];
        if (entry) setAsked((cur) => ({ ...cur, [String(key)]: entry }));
      })
      // Refused (the caller says why): the card keeps its old answer and the
      // button stays Submit Changes, so trying again is one tap.
      .catch(() => {})
      .finally(() => setAsking((cur) => ({ ...cur, [key]: false })));
  };

  const submit = async () => {
    // Not while a merge is out: its answer moves notes between items, and
    // the boxes this would read are the ones from before it did.
    if (merging) return;
    // Behind the deletes, so the answer is never the thing that races them:
    // submitting takes the job out of its pause, and a delete still in the
    // air would land on a job that has moved on and be refused. Resolved
    // already when nothing was deleted, which is the ordinary case.
    await queue.current;
    // Only what was actually typed. A box opened and left blank is the same
    // as one never touched, and the server drops empties anyway — this just
    // keeps the request honest about what the seller said.
    //
    // Read off the rows as the server now has them, not this render's: a
    // removal the await just waited out has renumbered the slots it sends.
    const out = {};
    now().forEach((it) => {
      const text = (notes[idOf(it)] || "").trim();
      if (text) out[it.gi] = text;
    });
    onSubmit(out);
  };

  // Where the batch stands, for the footer: how many cards are approved,
  // how many could be with one press, and what is holding up the rest.
  const approvedCount = rows.filter((it) => isApproved(it)).length;
  const allApproved = rows.length > 0 && approvedCount === rows.length;
  const readyCount = rows.filter((it) => !isApproved(it) && !waitingFor(it)
    && (!googleOn || !changedFor(it))).length;
  const waitingCount = rows.filter((it) => waitingFor(it)).length;
  const unsentCount = rows.filter((it) => changedFor(it)).length;

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
              {googleOn
                ? (single
                  ? "Check what Google says before the listing is written"
                  : `Your photos are sorted into ${rows.length} items — check what Google says about each one`)
                : (single
                  ? "Anything the photos don't show?"
                  : `Your photos are sorted into ${rows.length} items — anything the photos don't show?`)}
            </h2>
            {googleOn ? (
              <p className="mt-1 text-[13px] text-ink-secondary">
                Google image search looked {single ? "your item" : "each item"} up
                and says what it is and what it sells for — the listing is
                written about that item and priced from that range. If it&apos;s
                right, press <strong>Approve</strong>. If it isn&apos;t, say what
                it is in the box and press <strong>Submit Changes</strong>:
                Google is asked again with your words.
              </p>
            ) : (
              <p className="mt-1 text-[13px] text-ink-secondary">
                {single
                  ? "Tell the AI what it's looking at before it writes the listing — "
                  : "Tell the AI what each one is before it writes the listings — "}
                the brand on a worn-off label, the size, the era, a flaw worth
                mentioning. It reads this as the strongest hint it has about
                {single ? " your item" : " that item"}, and it&apos;s what stops
                you fixing the same field on every draft afterwards.
              </p>
            )}
            {canMerge && (
              <p className="mt-1.5 text-[13px] text-ink-secondary">
                One thing split into two? Tick <strong>Same item</strong> on
                each and merge them — it becomes one listing with all their
                photos.
              </p>
            )}
            {canSplit && (
              <p className="mt-1.5 text-[13px] text-ink-secondary">
                More than one item in these photos? Press{" "}
                <strong>It&apos;s more than one item</strong> below — the AI
                sorts the photos into items and writes a listing for each.
                Saying what they are in the box helps it get the split right.
              </p>
            )}
          </div>
        </div>
      </Card>

      {rows.map((item, i) => {
        const value = notes[idOf(item)] || "";
        const isPicked = picked.has(idOf(item));
        const entry = entryFor(item);
        const approvedHere = isApproved(item);
        const changedHere = changedFor(item);
        const waitingHere = waitingFor(item);
        const askingHere = !!asking[idOf(item)];
        // Locked while the merge it is part of is out: the line in it is
        // about to be carried into another card (see mergePicked).
        const locked = merging && isPicked;
        return (
          <Card key={idOf(item)}
            className={cn("flex flex-col gap-3.5",
              approvedHere && !isPicked && "border-success/50",
              isPicked && "border-blue ring-2 ring-blue/25")}>
            {/* Wraps rather than squeezes: on a phone the two controls
                drop under the name, which is how the seller tells two
                look-alike cards apart and must not shrink to "the…". */}
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1.5">
              <p className="text-sm font-bold text-ink shrink-0">
                {single ? "Your item" : `Item ${i + 1}`}
              </p>
              {approvedHere && (
                <span className="inline-flex items-center gap-1 text-[12.5px]
                  font-bold text-success shrink-0">
                  <CheckCircle2 size={14} aria-hidden /> Approved
                </span>
              )}
              {/* The name the grouping pass gave it. Useful and not
                  authoritative — it is the AI's first guess, which is the
                  very thing this box exists to correct. */}
              {item.name && (
                <p className="text-[13px] text-ink-secondary min-w-[8rem] flex-1 truncate">
                  the AI thinks: {item.name}
                </p>
              )}
              <div className="ml-auto self-center shrink-0 flex items-center gap-1.5">
                {/* A tick rather than a button: the usual split is one
                    thing in three, and three ticks and one merge is one
                    step, where pairwise is two. */}
                {canMerge && item.photos?.length > 0 && (
                  <label
                    title="Tick every card that's the same thing, then merge them into one listing"
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1",
                      "text-xs font-semibold border transition-colors duration-150",
                      merging ? "cursor-default opacity-60" : "cursor-pointer",
                      isPicked
                        ? "text-blue border-blue/50 bg-blue-soft"
                        : "text-ink-faint border-line hover:text-ink hover:border-line-strong",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={isPicked}
                      disabled={merging}
                      onChange={() => togglePick(idOf(item))}
                      aria-label={`Item ${i + 1} is the same item as another — merge it`}
                      className="size-3.5 accent-blue"
                    />
                    Same item
                  </label>
                )}
                {/* Not on the last one: an empty batch is a stopped batch,
                    and the screen already has a button for that. */}
                {onDeleteItem && rows.length > 1 && item.photos?.length > 0 && (
                  <button
                    type="button"
                    onClick={() => dropItem(idOf(item))}
                    disabled={merging && isPicked}
                    aria-label={`Remove item ${i + 1}`}
                    title="Leave this one out — nothing is drafted or charged for it"
                    className="shrink-0 inline-flex items-center gap-1
                      rounded-full px-2.5 py-1 text-xs font-semibold text-ink-faint
                      border border-line cursor-pointer transition-colors duration-150
                      hover:text-error hover:border-error/40
                      disabled:opacity-35 disabled:pointer-events-none"
                  >
                    <Trash2 size={12} aria-hidden />
                    Remove item
                  </button>
                )}
              </div>
            </div>
            <PhotoRow
              item={item}
              deleted={deleted}
              onDelete={onDeletePhoto && !locked
                ? (src) => drop(idOf(item), src) : undefined}
            />
            {googleOn && (
              <GooglePanel
                entry={entry}
                retrying={askingHere}
                onRetry={entry?.status === "failed" && !locked
                  ? () => submitChanges(item) : undefined}
              />
            )}
            <Field
              label={googleOn ? "Is this right?" : "Notes for the AI"}
              hint="optional"
              help={googleOn
                ? "If Google got it wrong or missed something — the exact model, "
                  + "size, edition or era, or a flaw worth disclosing — say so here "
                  + "and press Submit Changes. Google is asked again with your "
                  + "words, and the listing is written from what it says."
                : "Anything you know that the photos don't show — brand, maker, "
                  + "model, era, material, size, or a flaw worth disclosing. Write it "
                  + "as a sentence; the AI treats it as a strong hint about this item "
                  + "and won't invent details it doesn't cover."}
            >
              <Textarea
                rows={2}
                value={value}
                maxLength={MAX_ITEM_NOTES_CHARS}
                disabled={locked}
                // One item means the seller is here to type, so the cursor is
                // already in the box. With forty, it would be one scroll
                // position chosen for them out of forty.
                autoFocus={single}
                onChange={(e) => set(idOf(item), e.target.value)}
                // The pile can be long, and reaching for the button at the
                // bottom of forty cards is the wrong ending to typing.
                onKeyDown={(e) => {
                  if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey)) return;
                  if (changedHere) submitChanges(item);
                  else approve(item);
                }}
                placeholder={googleOn
                  ? "e.g. it's the 2.5 qt 403, not the 404 — small chip on the rim"
                  : single
                    ? "e.g. men's L, bought in Tokyo 2019, small mark on the left cuff"
                    : "e.g. the darker one — Lacoste, size 5 (men's L), tiny hole by the hem"}
              />
            </Field>
            {value.length > MAX_ITEM_NOTES_CHARS - 100 && (
              <p className="-mt-2 text-xs text-ink-faint">
                {MAX_ITEM_NOTES_CHARS - value.length} characters left.
              </p>
            )}
            {/* One button, and which one says what happens next: Approve
                while the box says nothing Google has not been asked with,
                Submit Changes the moment it does. */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              {approvedHere ? (
                <>
                  <Button variant="ghost" size="sm" onClick={() => unapprove(item)}
                    disabled={locked}>
                    Undo approval
                  </Button>
                  <p className="text-[12.5px] text-ink-secondary">
                    Edit the box to change it.
                  </p>
                </>
              ) : changedHere ? (
                <>
                  <Button variant="primary" onClick={() => submitChanges(item)}
                    loading={askingHere} disabled={locked}>
                    <Send aria-hidden /> Submit Changes
                  </Button>
                  <p className="text-[12.5px] text-ink-secondary min-w-0">
                    {googleOn
                      ? "Google is asked again with what you wrote."
                      : single
                        ? "Your note goes to the AI with the photos, and the listing is written."
                        : "Your note goes to the AI with the photos."}
                  </p>
                </>
              ) : (
                <>
                  <Button variant="success" onClick={() => approve(item)}
                    disabled={locked || waitingHere}>
                    <Check aria-hidden />
                    {waitingHere ? "Waiting for Google…" : "Approve"}
                  </Button>
                  {single && !waitingHere && (
                    <p className="text-[12.5px] text-ink-secondary min-w-0">
                      Approving writes your listing.
                    </p>
                  )}
                </>
              )}
            </div>
          </Card>
        );
      })}

      {/* The batch's own controls: the merge while cards are ticked, and
          the button that writes every approved item. Leaving a box blank is
          still no obstacle — an untouched box is approved as it stands. */}
      <div className={cn("flex flex-col gap-3",
        (rows.length > 2 || pickedRows.length > 0)
          && "sticky bottom-0 -mx-1 px-1 py-3 bg-bg/95 backdrop-blur")}>
        {/* The merge, while anything is ticked. In the sticky footer so it
            is in reach from wherever the second tick was made, which on a
            long pile is a long way from the first. */}
        {canMerge && pickedRows.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2 rounded-button
            border border-blue/30 bg-blue-soft px-3 py-2.5">
            {pickedRows.length > 1 ? (
              <Button variant="secondary" size="sm" onClick={mergePicked}
                loading={merging}
                title="Fold the ticked cards into one item: one listing, with all their photos and anything you typed under them">
                <Combine aria-hidden />
                Merge {pickedRows.length} items into one
              </Button>
            ) : null}
            {/* Its own line above the buttons on a phone, where beside them
                it was squeezed to a word per line. */}
            <p className="text-[13px] text-ink-secondary min-w-0 basis-full order-first
              sm:basis-0 sm:flex-1 sm:order-none">
              {pickedRows.length > 1
                ? "They'll become one listing with all their photos — and anything you typed under them."
                : "Now tick the other card (or cards) that's the same thing."}
            </p>
            <Button variant="ghost" size="sm" disabled={merging}
              onClick={() => setPicked(new Set())}>
              Cancel
            </Button>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          {/* One item is written by its own Approve; the footer is only the
              way out to a split. A batch is written from here, once every
              card is approved — "Approve all" is the fast way there for a
              pile whose answers all look right. */}
          {!single && (
            <Button variant="primary" size="lg" onClick={submit}
              disabled={merging || !allApproved}
              title={allApproved ? undefined
                : "Approve every item first — or press Approve all"}>
              <Sparkles aria-hidden />
              {`Write ${rows.length} listings`}
            </Button>
          )}
          {!single && !allApproved && (
            <Button variant="secondary" size="lg" onClick={approveAll}
              disabled={merging || readyCount === 0}>
              <Check aria-hidden /> Approve all{readyCount ? ` (${readyCount})` : ""}
            </Button>
          )}
          {/* The way back from the bulk toggle the seller did not tick.
              Beside the button that writes ONE listing, because that is the
              moment they notice the pile is two. */}
          {canSplit && (
            <Button variant="secondary" size="lg"
              onClick={() => onSplit(notes[idOf(rows[0])] || "")}
              title="Sort these photos into separate items, each with its own listing — nothing is drafted until you've checked the split">
              <Split aria-hidden />
              It&apos;s more than one item
            </Button>
          )}
          {!single && (
            <p className="text-[13px] text-ink-secondary min-w-0">
              {`${approvedCount} of ${rows.length} approved`}
              {waitingCount > 0 && ` · ${waitingCount} waiting on Google`}
              {unsentCount > 0
                && ` · ${unsentCount} with changes to submit`}
            </p>
          )}
        </div>
      </div>
    </motion.div>
  );
}
