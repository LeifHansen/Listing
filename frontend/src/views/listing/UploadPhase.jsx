import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Sparkles, FolderOpen, Trash2, Camera, MessageSquareText, Check, CheckCheck, X,
  ChevronDown, ChevronUp, ImagePlus,
} from "lucide-react";
import { cn, once } from "@/lib/utils";
import { turnedUprightMessage } from "@/lib/turnedUpright";
import {
  api, pollJob, postJson, downscaleAllForUpload, isPhotoFile, PHOTO_ACCEPT,
  UPLOAD_TIMEOUT_MS,
} from "@/lib/api";
import { useApp } from "@/store";
import { readLocal, writeLocal, clearLocal } from "@/lib/localPrefs";
import { Button } from "@/components/ui/Button";
import { Field, Textarea, Toggle } from "@/components/ui/fields";
import { Card } from "@/components/ui/Card";
import { BrandPulse } from "@/components/ui/AIStatus";
import { PhotoUploadIllustration } from "@/components/ui/illustrations";
import { useToast } from "@/components/ui/Toaster";
import { MAX_PHOTOS } from "./blockers";
import { AiNotesStep } from "./AiNotesStep";

// When background removal can't run (out of credits, bad key, rate limit) the
// server KEEPS the original photo — the right call, but silent: the photos just
// come back with their backgrounds, looking like the feature does nothing. Say
// so, with the reason the server gave.
function bgFailureMessage(results, total) {
  const failed = (results || []).filter((r) => r && r.bg_error);
  if (!failed.length) return null;
  const scope = failed.length === total
    ? "Backgrounds weren't removed"
    : `${failed.length} of ${total} photos kept their background`;
  // The server's reason is free text and usually has no trailing period, so
  // it ran straight into the next sentence ("cutout failed Your photos…").
  const reason = String(failed[0].bg_error).trim().replace(/[.!?]*$/, "");
  return `${scope} — ${reason}. Your photos were saved unchanged.`;
}

// One listing holds MAX_PHOTOS (eBay's own ceiling of 24 — the publish
// blocker list refuses more, so a bigger pile could only ever have been a
// draft the seller then had to thin out one confirm at a time); a bulk batch
// (many items) takes up to 250, the server's cap. Past MAX_PHOTOS the pile
// can only be a bulk batch, so the toggle locks on rather than letting the
// upload bounce off the server with an error.
const MAX_SINGLE_FILES = MAX_PHOTOS;
const MAX_BATCH_FILES = 250;

// Mirrors listing_prompt.SELLER_NOTES_MAX_CHARS. Enforced here as well as on
// the server so the box stops taking characters it is about to drop, rather
// than silently truncating a hint the seller watched themselves type.
const MAX_NOTES_CHARS = 1000;

// Where a paused upload's job id waits while the seller is somewhere else.
// See the restore effect below for why it has to be written down at all.
const ASK_KEY = "ask";

// The seller's hints, one per comma — the same split the server prompt does,
// so the count under the box is the number of hints the AI will actually see.
function countHints(notes) {
  return notes.split(",").filter((p) => p.trim()).length;
}

// The photo uploader — centerpiece of a new listing. Big friendly drop zone,
// rounded photo cards, then one tap to let the AI take over. With several
// photos it can also run in bulk mode: one pile, many listings.
//
// @param defaultOpen  start unfolded. The List tab passes it: the drop zone
//                     is what that tab is FOR, so arriving to a one-line bar
//                     would be the page hiding its own point. See the fold
//                     below for why anywhere else still starts shut.
export function UploadPhase({ defaultOpen = false }) {
  const { setSession, runBulkUpload, bulkRetry, clearBulkRetry,
          invalidateListings } = useApp();
  const { toast } = useToast();
  const inputRef = useRef(null);
  const cameraRef = useRef(null);
  // A bulk upload that failed hands its pile back here — this component was
  // unmounted while it ran, so the photos would otherwise be gone.
  const [files, setFiles] = useState(() => bulkRetry?.files || []); // { file, url }
  // Off for every pile — the seller ticks it, nobody ticks it for them.
  // It used to open pre-ticked from their last choice, so one upload where
  // they wanted cutouts silently replaced the background of every pile after
  // it: a decision made weeks ago about other photos, re-applied to these
  // without being asked. Replacing a background is destructive (the shot is
  // what the buyer trusts) and it spends a credit per photo, so it belongs to
  // THIS pile, chosen while looking at these photos. A failed bulk upload
  // still hands its own choice back with its files — same pile, seconds
  // later, not a default.
  const [removeBg, setRemoveBg] = useState(() => !!bulkRetry?.removeBg);
  // Hints for the AI, comma-separated: what the seller knows and the camera
  // can't show. Like the cutout toggle it describes THIS pile and is never
  // carried over — last week's pile leaking into this one is worse than
  // typing it again. Seeded from a failed bulk retry, though: the seller
  // wrote it seconds ago.
  const [notes, setNotes] = useState(() => bulkRetry?.notes || "");
  const [bulk, setBulk] = useState(() => !!bulkRetry);
  const [drag, setDrag] = useState(false);
  // Is the drop zone unfolded? Seeded from `defaultOpen` on every mount, and
  // deliberately never remembered.
  //
  // The fold was an answer to a screen that no longer exists. This panel used
  // to sit at the top of "Sell", above the drafts strip AND the whole listing
  // manager, so a seller who came to look at what they were already selling
  // scrolled past a box asking for photos first, every single visit — folded,
  // it was one line and the lists were where the screen started. Splitting
  // Sell into List and Manage answers that properly: the lists have their own
  // tab, so on List the box can be the box again, and `defaultOpen` says so.
  //
  // This is where #333's `openUploaderRef` went. That flag existed to make
  // ONE arrival open — the dashboard's create-a-listing buttons, whose whole
  // promise is a box to put photos in — while the nav, the top bar and a deep
  // link stayed folded. The List tab keeps that promise for every door now,
  // so the flag had nothing left to decide: the only place this component
  // renders passes `defaultOpen`, and a one-shot that can never change an
  // outcome is just a comment insisting on an invariant that has stopped
  // being true.
  //
  // The fold itself stays, for the seller who has scrolled or wants the
  // drafts up — it is just no longer the arrival state. What must NOT come
  // back is remembering it: "open, like last time" is the same trap the
  // cutout toggle above documents, a decision made on another visit
  // re-applied to this one without being asked.
  const [open, setOpen] = useState(defaultOpen);
  const [busy, setBusy] = useState(false);
  // Select mode: pick several photos out of the pile and drop them in one go.
  // A per-tile trash is one tap for one wrong photo, and forty taps for the
  // half of a shoot that came out dark -- which is what a seller sorting a
  // 250-photo batch actually has. Selection is keyed by the object URL, not
  // the index: an index shifts under every removal, so deleting a set by
  // index deletes the wrong photos as soon as the first one goes.
  const [selectMode, setSelectMode] = useState(false);
  const [picked, setPicked] = useState(() => new Set());
  // Derived, not synced: an empty pile has no grid and so no toolbar, and
  // deriving that is what keeps it true no matter which path emptied it. The
  // effect that used to reset the flag was both a lint error (setState inside
  // an effect, cascading renders) and a race waiting to happen.
  const selecting = selectMode && files.length > 0;
  // Counted against the pile rather than read off the set, so a URL left in
  // `picked` for a photo that has since gone cannot inflate "N of M".
  const pickedCount = files.reduce((n, f) => n + (picked.has(f.url) ? 1 : 0), 0);
  // Live status of the background pipeline job (phase/current/total_photos),
  // so the wait card reports what's actually happening instead of guessing.
  const [stage, setStage] = useState(null);
  // The guidance step. The pipeline job stops once the photos are optimized
  // and before anything is drafted, and hands back the item it is about to
  // write — so this holds that question while the seller answers it.
  // { sessionId, jobId, items } or null.
  const [pending, setPending] = useState(null);
  // What the seller has typed into it, keyed by item index. Held HERE rather
  // than inside the step, which is unmounted the moment the answer is sent —
  // see AiNotesStep: a failed submit has to come back to the words they
  // wrote, not to an empty box.
  const [itemNotes, setItemNotes] = useState({});
  // Answering it. Separate from `busy` because the two render differently:
  // `busy` is the wait before the question, this is the wait after it.
  const [notesBusy, setNotesBusy] = useState(false);
  // Taken once, on mount — a later retry must not re-seed the drop zone.
  useEffect(() => { clearBulkRetry(); }, [clearBulkRetry]);

  // A question the seller walked away from.
  //
  // The step lives in this component, so opening Drafts — or reloading —
  // unmounts it. That used to cost nothing: the old straight-through pass
  // finished in the background and left a draft behind whether anyone was
  // watching or not. A paused job drafts NOTHING until it is answered, so
  // losing the question loses the whole upload. Hence the id on the way into
  // the pause, and this on the way back: return to Sell and the question is
  // there again.
  //
  // Only the ids are stored. The items are re-read from the job, which is the
  // one copy that cannot be stale — and the same read is what settles a
  // stored id that is no longer worth putting back.
  useEffect(() => {
    const raw = readLocal(ASK_KEY);
    if (!raw) return undefined;
    let live = true;
    (async () => {
      let saved = null;
      try { saved = JSON.parse(raw); } catch { /* unreadable — drop it */ }
      // Both ids or neither: the job id finds the question, the session id is
      // what the answer's draft is opened from. Half a record cannot do the
      // job and must not put a question on screen that leads nowhere.
      if (!saved?.jobId || !saved?.sessionId) { clearLocal(ASK_KEY); return; }
      try {
        const j = await api(`/api/bulk/status/${saved.jobId}`);
        if (!live) return;
        if (!j.done && j.phase === "awaiting_notes") {
          setPending({ sessionId: saved.sessionId, jobId: saved.jobId,
                       items: j.pending_items || [] });
          return;
        }
      } catch (e) { /* 404, or not ours: no question left to put back */ }
      // It finished, failed, or the server has no record of it. Anything it
      // drafted is in Drafts, and a stale id must not sit in front of the
      // drop zone forever.
      if (live) clearLocal(ASK_KEY);
    })();
    return () => { live = false; };
    // Once, on mount: this is a restore, not a subscription.
  }, []);
  // Past the single-listing cap the pile can only be a bulk batch.
  const forceBulk = files.length > MAX_SINGLE_FILES;
  const bulkOn = bulk || forceBulk;

  // Same photo, twice? Name AND size: an iPhone calls every photo in the
  // library `image.jpg`, so matching on the name alone would throw away a
  // whole pile of different photos as one.
  const alreadyHere = (f, list) =>
    list.some((e) => e.file.name === f.name && e.file.size === f.size);

  const addFiles = (fileList) => {
    // Photos arriving is the panel's cue to open — dropped onto the folded
    // bar, picked from the library, or handed back by a failed batch. It
    // stays open afterwards even if the pile is emptied again: a seller
    // deleting the last wrong shot is about to pick another one, not asking
    // for the uploader to fold away under their hands.
    setOpen(true);
    // Copied before anything else runs. `fileList` is the input's own LIVE
    // FileList, and the change handler clears the input the moment this
    // returns (`e.target.value = ""`, without which picking the same photo
    // twice in a row fires no second event) — which, per the HTML spec,
    // empties the very list this is reading. Nothing here may hold onto it
    // past this line. The editor's "Add photos" has always copied first.
    const picked = Array.from(fileList || []);
    const rejected = picked.filter((f) => !isPhotoFile(f));
    // Everything is decided out here rather than inside the state updater: an
    // updater has to be pure (React runs it again, and twice over in
    // development), and this both mints object URLs and counts what the
    // seller is about to be told. The merge is against `files` as it stands,
    // which is what the picker was just opened on top of.
    const added = [];
    let duplicates = 0;
    let overflow = 0;
    for (const f of picked.filter(isPhotoFile)) {
      if (alreadyHere(f, files) || alreadyHere(f, added)) { duplicates += 1; continue; }
      if (files.length + added.length >= MAX_BATCH_FILES) { overflow += 1; continue; }
      added.push({ file: f, url: URL.createObjectURL(f) });
    }
    if (added.length) setFiles((cur) => [...cur, ...added]);
    // Nothing is dropped in silence. A photo that does not arrive leaves a
    // screen identical to one where the picker never worked at all — which is
    // exactly how this was reported: chose photos from the library, nothing
    // happened. Whatever the reason, it is on screen, with the file named.
    if (rejected.length) {
      const names = rejected.slice(0, 3).map((f) => f.name || "that file").join(", ");
      const more = rejected.length > 3 ? ` and ${rejected.length - 3} more` : "";
      toast(`${names}${more} ${rejected.length === 1 ? "isn't" : "aren't"} a photo `
        + "we can use — pick JPEG, PNG, HEIC or WebP images.", { kind: "warning" });
    }
    if (duplicates) {
      toast(duplicates === 1
        ? "That photo is already in the pile."
        : `${duplicates} of those are already in the pile.`, { kind: "warning" });
    }
    if (overflow) {
      toast(`A batch takes up to ${MAX_BATCH_FILES} photos — ${overflow} weren't added. Run them as a second batch.`,
        { kind: "warning" });
    }
  };

  const removeFile = (i) => {
    setFiles((cur) => {
      URL.revokeObjectURL(cur[i].url);
      return cur.filter((_, j) => j !== i);
    });
  };

  const togglePick = (url) => {
    setPicked((cur) => {
      const next = new Set(cur);
      if (!next.delete(url)) next.add(url);
      return next;
    });
  };

  // Leave select mode with nothing held, so the next time it is entered it
  // does not open on a selection the seller made minutes ago.
  const endSelecting = () => {
    setSelectMode(false);
    setPicked(new Set());
  };

  const removePicked = () => {
    setFiles((cur) => {
      const kept = cur.filter((f) => !picked.has(f.url));
      // Revoke only what actually left: a URL still on screen must keep
      // working, and a revoked one renders as a broken tile.
      cur.forEach((f) => { if (picked.has(f.url)) URL.revokeObjectURL(f.url); });
      return kept;
    });
    endSelecting();
  };

  // The batch screen takes over on the click — no waiting on the upload with
  // the Sell tab still on screen — which unmounts this component, so the
  // upload itself runs from the store (see runBulkUpload).
  const startBulk = once("bulk", async () => {
    if (!files.length) return;
    await runBulkUpload(files, removeBg, notes);
  });

  // The draft has landed — the same ending for the pass that ran straight
  // through and the one that waited for the seller's notes.
  const finish = (sessionId, result) => {
    setSession({
      sessionId,
      listing: result.listing,
      confidence: result.confidence,
      // Server already ran the specifics/maker enrichment for this draft —
      // the editor's autofill effect skips its (re-charging) re-run.
      specificsAutofilled: !!result.specifics_autofilled,
    });
    // A listing that did not exist a moment ago now does. Nothing else asks
    // the server again on its own, so without this the new draft is absent
    // from Drafts, from the tab counts and from the dashboard until some
    // unrelated refresh happens along. See store.invalidateListings.
    invalidateListings();
  };

  // The seller has answered the guidance step (or left every box blank, which
  // is the same request and the same drafts a run without the step would have
  // produced). `pending` is deliberately NOT cleared until the draft lands:
  // a failed submit has to come back to the question, not to a dead screen —
  // the photos are still on the server and the job is still paused.
  const submitNotes = async (notes) => {
    if (!pending || notesBusy) return;
    setNotesBusy(true);
    setStage({ phase: "identifying" });
    try {
      await postJson(`/api/bulk/notes/${pending.jobId}`, { notes });
      const result = await pollJob(pending.jobId, {
        onUpdate: (j) => setStage(j),
      });
      clearLocal(ASK_KEY);  // answered — there is no question to come back to
      setPending(null);
      finish(pending.sessionId, result);
    } catch (e) {
      // 409 is the server saying this question has already been answered —
      // another tab, or a reload that raced the first press. Keeping the
      // boxes up would be a dead end: every press from here is refused. So
      // the question goes, and the seller is pointed at the draft it made.
      //
      // `e.status`, not the message: api() uses the sentence the server wrote
      // AS the message and puts the code on the error, so a status match is
      // the only reliable one.
      if (e.status === 409) {
        clearLocal(ASK_KEY);
        setPending(null);
        invalidateListings();
        toast("This upload has already been written — find it in Drafts.",
              { kind: "info" });
      } else {
        toast(`Error: ${e.message}`, { kind: "error" });
      }
    } finally {
      setNotesBusy(false);
      setStage(null);
    }
  };

  // Drop one photo from the item the question is about. The job is still
  // paused, so nothing has been drafted from it and nothing is refunded —
  // the photo simply isn't there when the AI starts reading.
  //
  // It THROWS on failure, which is the contract AiNotesStep's queue reads to
  // put the thumb back: a photo that is still on the server has to be back on
  // screen, or the seller presses on believing it is out of their listing.
  const deletePhoto = async (gi, photo) => {
    if (!pending) return;
    try {
      return await postJson(`/api/bulk/notes/${pending.jobId}/delete-photo`,
                            { gi, photo });
    } catch (e) {
      toast(`Couldn't remove that photo: ${e.message}`, { kind: "error" });
      throw e;
    }
  };

  const process = once("process", async () => {
    if (!files.length) return;
    if (bulkOn) return startBulk();
    setBusy(true);
    setStage(null);
    try {
      const prepped = await downscaleAllForUpload(files.map((f) => f.file));
      const fd = new FormData();
      prepped.forEach((f) => fd.append("files", f));
      fd.append("remove_bg", removeBg ? "true" : "false");
      // Saved with the session server-side, so a later "Start over" re-drafts
      // with the same hints instead of forgetting them.
      fd.append("notes", notes);
      // The upload returns as soon as the originals are saved; optimization
      // and the whole identify chain run as ONE background job we poll, with
      // real per-stage progress instead of a request that blocks silently
      // through the photo pass.
      fd.append("pipeline", "true");
      const up = await api("/api/upload",
        { method: "POST", body: fd, timeoutMs: UPLOAD_TIMEOUT_MS });
      let last = null;
      // Stops at the guidance step, where the job pauses with the photos
      // ready and nothing drafted — waiting on the seller rather than on the
      // machine, which is why the poll has to be told this is not a job that
      // has gone quiet. A server that ran straight through (nothing paused)
      // hands back the identify result exactly as it always did, so the two
      // are told apart by what came back rather than by trusting either.
      const out = await pollJob(up.job_id, {
        onUpdate: (j) => { last = j; setStage(j); },
        stopWhen: (j) => j.phase === "awaiting_notes",
      });
      const optResults = last?.upload?.optimize_results || [];
      if (removeBg) {
        const warning = bgFailureMessage(optResults, prepped.length);
        if (warning) toast(warning, { kind: "warning", ttl: 10000 });
      }
      // A photo the pass turned upright is a photo the seller did not see
      // change. Say so, and how to turn it back if the pass got it wrong.
      const turned = turnedUprightMessage(optResults);
      if (turned) toast(turned, { kind: "info", ttl: 10000 });
      // The photos are on the server now and everything from here shows
      // THOSE, so the local previews have done their job.
      files.forEach((f) => URL.revokeObjectURL(f.url));
      if (out?.phase === "awaiting_notes") {
        // Written down BEFORE the question goes up, so a seller who leaves
        // the screen a second later can still be brought back to it.
        writeLocal(ASK_KEY, JSON.stringify({ sessionId: up.session_id,
                                             jobId: up.job_id }));
        setPending({ sessionId: up.session_id, jobId: up.job_id,
                     items: out.pending_items || [] });
        return;
      }
      finish(up.session_id, out);
    } catch (e) {
      toast(`Error: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
      setStage(null);
    }
  });

  if (busy || notesBusy) {
    // Real pipeline stages from the job status; the pre-job moment (uploading
    // the files themselves) is the only guessed line.
    const total = stage?.total_photos || files.length;
    const doneCount = Math.min((stage?.current || 0) + 1, total);
    const stageLine = !stage?.phase ? "Uploading your photos…"
      : stage.phase === "optimizing"
        ? `${removeBg ? "Cleaning up" : "Optimizing"} photo ${doneCount} of ${total}…`
        : stage.phase === "identifying" ? "Identifying your item…"
          : stage.phase === "category" ? "Finding the right eBay category…"
            : stage.phase === "specifics" ? "Filling item specifics from your photos…"
              : stage.phase === "maker" ? "Double-checking the brand…"
                // The last pass, after research has settled what this is:
                // whatever the draft still has blank that the item itself
                // can answer. There is no button for it — this IS it.
                : stage.phase === "finishing" ? "Filling in the last details…"
                : stage.phase === "artwork" ? "Naming the artist and the work…"
                // The web lookup. It is the slowest stage and the one worth
                // waiting for, so it says what it is doing rather than
                // hiding behind "Finishing up".
                : stage.phase === "research"
                  ? "Looking it up online — what it is and what it's worth…"
                  : "Finishing up…";
    // The pulsing mark and the stage in words, and nothing else: the grey
    // skeleton bars that used to sit under a small status card read as a
    // page that had failed to load, not one that was working.
    return (
      <div className="flex flex-col gap-5">
        <BrandPulse
          message={stageLine}
          detail="Your photos are safe here — this can take a minute or two."
        />
      </div>
    );
  }

  // The guidance step, in place of the uploader: the photos are already on
  // the server, so there is nothing to drop and nothing to pick — the only
  // thing left before the AI writes is what the seller knows about the item.
  if (pending) {
    return (
      <div className="flex flex-col gap-5">
        <AiNotesStep
          items={pending.items}
          values={itemNotes}
          onChange={(gi, text) => setItemNotes((cur) => ({ ...cur, [gi]: text }))}
          onSubmit={submitNotes}
          onDeletePhoto={deletePhoto}
        />
      </div>
    );
  }

  // A pile on screen is never folded away. The staged photos, the notes box
  // and the button that starts the AI all live in the card below this one —
  // hiding them behind a bar that reads "Add photos" is how a seller loses a
  // shoot they thought was queued. So the fold is offered only while there is
  // nothing in it, which is also the only state it was ever in the way in.
  const collapsed = !open && files.length === 0;

  // One drop target's worth of handlers, shared by the folded bar and the
  // open drop zone: dropping photos has to work in both, or folding the panel
  // would quietly take the app's main gesture away with it.
  const dropHandlers = {
    onDragOver: (e) => { e.preventDefault(); setDrag(true); },
    onDragEnter: (e) => { e.preventDefault(); setDrag(true); },
    onDragLeave: (e) => { e.preventDefault(); setDrag(false); },
    onDrop: (e) => {
      e.preventDefault();
      setDrag(false);
      addFiles(e.dataTransfer.files);
    },
  };

  return (
    <div className="flex flex-col gap-5">
      {/* Mounted in both states. Browse Files and the camera reach these
          through their refs, and a photo handed straight to the input — which
          is what a phone's library picker does — must land in the pile
          whether or not the panel happens to be open. */}
      <input
        ref={inputRef} type="file" accept={PHOTO_ACCEPT} multiple hidden
        onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
      />
      <input
        ref={cameraRef} type="file" accept={PHOTO_ACCEPT} capture="environment" hidden
        onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
      />

      {collapsed ? (
        /* Folded: one line at the top of Sell instead of half the screen —
           and still a drop target, because that is the gesture the big box
           spent its whole life teaching. Photos dropped here open it. */
        <Card
          className={cn(
            "p-0 overflow-hidden transition-colors duration-200",
            drag ? "border-blue bg-blue-soft" : "hover:border-line-strong",
          )}
          {...dropHandlers}
        >
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-expanded={false}
            className="w-full flex items-center gap-3 p-4 sm:px-5 text-left cursor-pointer group"
          >
            <span className="grid place-items-center size-10 rounded-[13px] bg-blue-soft text-blue shrink-0">
              <ImagePlus size={19} strokeWidth={2} aria-hidden />
            </span>
            <span className="flex-1 min-w-0">
              <span className="block font-bold text-[16px] text-ink">Add photos</span>
              {/* Wraps rather than truncates: on a phone this is the line
                  that says the bar is still a drop target, and half of it
                  followed by an ellipsis says nothing. */}
              <span className="block text-[13px] text-ink-secondary">
                Drop them here, or open it to browse and shoot.
              </span>
            </span>
            <ChevronDown
              size={19} aria-hidden
              className="shrink-0 text-ink-faint group-hover:text-ink transition-colors duration-150"
            />
          </button>
        </Card>
      ) : (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          transition={{ duration: 0.2, ease: "easeOut" }}
          className="overflow-hidden"
        >
          <Card
            className={cn(
              "relative border-2 border-dashed transition-colors duration-200 cursor-pointer",
              drag ? "border-blue bg-blue-soft" : "border-line-strong hover:border-blue/60",
            )}
            onClick={() => inputRef.current?.click()}
            {...dropHandlers}
          >
            {/* The way back to one line. Offered only while the pile is empty,
                for the reason `collapsed` gives — with photos staged this would
                be a button that visibly does nothing. stopPropagation because the
                card it sits in is itself one big "open the picker" target. */}
            {files.length === 0 && (
              <Button
                variant="ghost" size="sm"
                aria-expanded={true}
                className="absolute top-3 right-3 text-ink-faint"
                onClick={(e) => { e.stopPropagation(); setOpen(false); }}
              >
                <ChevronUp aria-hidden /> Hide
              </Button>
            )}
            <div className="flex flex-col items-center text-center gap-3 py-8">
              <PhotoUploadIllustration />
              <h2 className="text-xl font-bold text-ink">Drag photos here</h2>
              <p className="text-sm text-ink-secondary">
                or bring them in another way — the AI writes the listing from your shots.
              </p>
              <div className="flex flex-wrap justify-center gap-2.5 mt-1">
                <Button
                  variant="primary" size="lg"
                  onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}
                >
                  <FolderOpen aria-hidden /> Browse Files
                </Button>
                <Button
                  variant="secondary" size="lg"
                  onClick={(e) => { e.stopPropagation(); cameraRef.current?.click(); }}
                >
                  <Camera aria-hidden /> Take Photos
                </Button>
              </div>
            </div>
          </Card>
        </motion.div>
      )}

      <AnimatePresence>
        {files.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <Card className="flex flex-col gap-5">
              {/* The pile's own toolbar. Out of select mode it is a count and
                  one way in; in select mode it is what the seller is doing:
                  how many are held, select-all/none, and the delete. */}
              <div className="flex items-center gap-2 flex-wrap">
                <p className="text-sm font-semibold text-ink">
                  {selecting
                    ? `${pickedCount} of ${files.length} selected`
                    : `${files.length} photo${files.length === 1 ? "" : "s"}`}
                </p>
                <div className="ml-auto flex items-center gap-2">
                  {selecting ? (
                    <>
                      <Button
                        variant="ghost" size="sm"
                        onClick={() => setPicked(pickedCount === files.length
                          ? new Set()
                          : new Set(files.map((f) => f.url)))}
                      >
                        <CheckCheck aria-hidden />
                        {pickedCount === files.length ? "Select none" : "Select all"}
                      </Button>
                      <Button
                        variant="danger" size="sm"
                        disabled={!pickedCount}
                        onClick={removePicked}
                      >
                        <Trash2 aria-hidden />
                        Delete{pickedCount ? ` ${pickedCount}` : ""}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={endSelecting}>
                        <X aria-hidden /> Done
                      </Button>
                    </>
                  ) : (
                    <Button variant="secondary" size="sm"
                      onClick={() => setSelectMode(true)}>
                      <Check aria-hidden /> Select
                    </Button>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-3">
                <AnimatePresence>
                  {files.map((f, i) => {
                    const on = picked.has(f.url);
                    return (
                      <motion.div
                        key={f.url}
                        layout
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.85 }}
                        transition={{ duration: 0.18 }}
                        className={cn(
                          "relative rounded-tile overflow-hidden border aspect-square group",
                          on ? "border-blue ring-2 ring-blue" : "border-line",
                        )}
                      >
                        {/* In select mode the whole tile is the target — a
                            7px corner control is not something to hit forty
                            times on a phone. The button IS the tile, so it
                            carries the label and the pressed state. */}
                        {selecting ? (
                          <button
                            type="button"
                            aria-pressed={on}
                            aria-label={`${on ? "Deselect" : "Select"} photo ${i + 1}`}
                            onClick={() => togglePick(f.url)}
                            className="absolute inset-0 z-10 cursor-pointer"
                          >
                            <img src={f.url} alt="" className="size-full object-cover" />
                            <span className={cn(
                              "absolute inset-0 transition-colors duration-150",
                              on ? "bg-blue/25" : "bg-transparent hover:bg-ink/10",
                            )} />
                            <span className={cn(
                              "absolute top-1.5 left-1.5 grid place-items-center size-7 rounded-full border shadow-card transition-colors duration-150",
                              on ? "bg-blue border-blue text-on-accent"
                                : "bg-card/85 backdrop-blur border-line text-transparent",
                            )}>
                              <Check size={15} strokeWidth={3} aria-hidden />
                            </span>
                          </button>
                        ) : (
                          <>
                            <img src={f.url} alt="" className="size-full object-cover" />
                            <button
                              type="button"
                              aria-label="Remove photo"
                              title="Remove photo"
                              onClick={() => removeFile(i)}
                              className="absolute top-1.5 left-1.5 z-10 grid place-items-center size-7 rounded-full
                                bg-card/85 backdrop-blur border border-line text-ink-faint shadow-card cursor-pointer
                                hover:text-error hover:border-error/40 transition-colors duration-150"
                            >
                              <Trash2 size={13} aria-hidden />
                            </button>
                          </>
                        )}
                      </motion.div>
                    );
                  })}
                </AnimatePresence>
              </div>

              {/* Hints for the AI. The model reads pixels; the seller is
                  holding the thing — the brand on a worn-off label, the era,
                  and above all HOW MANY items are in a bulk pile are exactly
                  what it gets wrong and exactly what one typed line fixes. */}
              <Field
                label={(
                  <span className="inline-flex items-center gap-1.5">
                    <MessageSquareText size={14} aria-hidden className="text-blue" />
                    Notes for the AI
                  </span>
                )}
                hint="optional"
                help={"Anything you know that the photos don't show — brand, maker, era, "
                  + "material, size, or how many separate items are in the pile. "
                  + "Separate each one with a comma. The AI treats them as strong hints "
                  + "and won't invent details they don't cover."}
              >
                <Textarea
                  rows={3}
                  value={notes}
                  maxLength={MAX_NOTES_CHARS}
                  onChange={(e) => setNotes(e.target.value.slice(0, MAX_NOTES_CHARS))}
                  placeholder={"e.g. one perrier vintage hand painted champagne bottle, "
                    + "one vintage ralph lauren polo, two lacoste polos different size color"}
                />
              </Field>
              {/* The hint count is the format teaching itself: type a comma,
                  watch it go from 1 to 2. The character count only shows up
                  once it is close enough to the cap to matter. */}
              <p className="-mt-3 text-xs text-ink-faint">
                {notes.trim()
                  ? `${countHints(notes)} hint${countHints(notes) === 1 ? "" : "s"} — the AI reads each one separately.`
                  : "Skip it and the AI works from the photos alone."}
                {notes.length > MAX_NOTES_CHARS - 100
                  && ` ${MAX_NOTES_CHARS - notes.length} characters left.`}
              </p>

              <Toggle
                checked={removeBg}
                onChange={setRemoveBg}
                label="Remove background & replace with white"
                help="Cleaner, eBay-friendly product shots. Adds a few seconds per photo."
              />

              {forceBulk ? (
                <p className="text-sm text-ink-secondary">
                  Bulk mode is on automatically — {files.length} photos is more
                  than one listing holds (max {MAX_SINGLE_FILES}), so the AI
                  will sort this pile into separate items.
                </p>
              ) : files.length >= 2 && (
                <Toggle
                  checked={bulk}
                  onChange={setBulk}
                  label="Bulk mode — this pile has multiple items"
                  help="The AI sorts the photos into items and drafts a listing for each one."
                />
              )}

              <Button variant="primary" size="lg" className="self-start" onClick={process}>
                <Sparkles aria-hidden />
                {bulkOn
                  ? `Split ${files.length} photos into listings`
                  : "Identify with AI"}
              </Button>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
