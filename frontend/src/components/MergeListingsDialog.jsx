import { useMemo, useState } from "react";
import {
  AlertTriangle, ArrowLeft, ArrowRight, Check, Combine, Crown, Images,
  Sparkles, Trash2,
} from "lucide-react";
import { cn, formatMoney, mediaUrl } from "@/lib/utils";
import { postJson } from "@/lib/api";
import { apiUrl } from "@/lib/platform";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";

/* MergeListingsDialog — consolidating duplicate drafts, as a decision rather
   than a button.

   One item photographed twice comes out of a bulk batch as two half-right
   drafts: one got the better title, the other got the price and the brand.
   Merging used to combine the photos under whichever draft happened to be
   selected first and delete the rest — so the other drafts' writing went in
   the bin, silently, and the seller found out later.

   So the merge asks, in this order:

     0. What does it merge with? Ticking ONE draft in the queue is enough to
        start — this step offers the rest of the batch as partners. Tick two
        or more up front and the question is already answered, so it's skipped.
     1. Which draft is the master? Everything is kept under it; the others are
        consolidated into it and deleted.
     2. Where the drafts disagree, whose entry wins? Only fields two drafts
        genuinely fill in differently are asked about — the master's own entry
        is always pre-selected, and a field only one draft filled in is just
        carried over.

   The server works out both (POST /api/listings/merge/preview) so the review
   can promise exactly what the merge writes; this renders that and sends the
   answers back. */

function thumbOf(item) {
  const first = item.listing?.images?.[0];
  if (first) return mediaUrl(item.session_id, first, 1);
  return item.thumb ? apiUrl(`${item.thumb}?v=1`) : null;
}

function titleOf(item) {
  return item.listing?.title || item.title || "Untitled draft";
}

function photoCount(item) {
  return item.listing?.images?.length || (item.thumb ? 1 : 0);
}

// One draft's row in step one. The whole row is the radio's label, so the
// picture and the title are as clickable as the control itself.
function MasterRow({ item, selected, onSelect }) {
  const src = thumbOf(item);
  const price = formatMoney(item.listing?.price, item.listing?.currency || "USD");
  const photos = photoCount(item);
  return (
    <label
      className={cn(
        "flex items-center gap-3 rounded-[13px] border p-3 cursor-pointer",
        "transition-colors duration-150",
        selected ? "border-blue bg-blue-soft/40" : "border-line bg-bg hover:border-line-strong",
      )}
    >
      <input
        type="radio"
        name="merge-master"
        className="size-4 shrink-0 accent-(--brand-blue)"
        checked={selected}
        onChange={() => onSelect(item.session_id)}
      />
      {src
        ? <img src={src} alt="" className="size-12 rounded-[10px] object-cover bg-bg-sunken shrink-0" />
        : <span className="size-12 rounded-[10px] bg-bg-sunken shrink-0" aria-hidden />}
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-sm text-ink line-clamp-2">
          {titleOf(item)}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-secondary tabular-nums">
          <span className="inline-flex items-center gap-1">
            <Images size={12} aria-hidden /> {photos} photo{photos === 1 ? "" : "s"}
          </span>
          {price && <span>· {price}</span>}
        </span>
      </span>
      {selected
        ? <TagPill tone="blue" className="shrink-0"><Crown size={12} aria-hidden /> Master</TagPill>
        : <TagPill className="shrink-0">Merged in</TagPill>}
    </label>
  );
}

// One candidate in step zero: another draft in the batch this one could be a
// duplicate of. A checkbox, not a radio — one item can be split across three
// drafts as easily as two, and all of them merge in one pass.
function PartnerRow({ item, selected, onToggle }) {
  const src = thumbOf(item);
  const price = formatMoney(item.listing?.price, item.listing?.currency || "USD");
  const photos = photoCount(item);
  return (
    <label
      className={cn(
        "flex items-center gap-3 rounded-[13px] border p-3 cursor-pointer",
        "transition-colors duration-150",
        selected ? "border-blue bg-blue-soft/40" : "border-line bg-bg hover:border-line-strong",
      )}
    >
      <input
        type="checkbox"
        className="size-4 shrink-0 accent-(--brand-blue)"
        checked={selected}
        onChange={() => onToggle(item.session_id)}
      />
      {src
        ? <img src={src} alt="" className="size-12 rounded-[10px] object-cover bg-bg-sunken shrink-0" />
        : <span className="size-12 rounded-[10px] bg-bg-sunken shrink-0" aria-hidden />}
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-sm text-ink line-clamp-2">
          {titleOf(item)}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-secondary tabular-nums">
          <span className="inline-flex items-center gap-1">
            <Images size={12} aria-hidden /> {photos} photo{photos === 1 ? "" : "s"}
          </span>
          {price && <span>· {price}</span>}
        </span>
      </span>
      {selected && <TagPill tone="blue" className="shrink-0">Merging</TagPill>}
    </label>
  );
}

// One field the drafts disagree about: every distinct entry on offer, labelled
// with the draft it came from. Long values (a description) are clamped rather
// than truncated so the picker keeps its shape.
function ConflictField({ conflict, drafts, chosen, onChoose }) {
  return (
    <fieldset className="rounded-[13px] border border-line bg-bg p-3">
      <legend className="px-1 text-[13px] font-semibold text-ink">{conflict.label}</legend>
      <div className="flex flex-col gap-2 pt-1">
        {conflict.options.map((option) => {
          const selected = chosen === option.listing_id;
          const from = drafts[option.listing_id];
          return (
            <label
              key={option.listing_id}
              className={cn(
                "flex items-start gap-2.5 rounded-[10px] border px-3 py-2 cursor-pointer",
                "transition-colors duration-150",
                selected ? "border-blue bg-blue-soft/40" : "border-line hover:border-line-strong",
              )}
            >
              <input
                type="radio"
                name={`merge-${conflict.key}`}
                className="mt-1 size-4 shrink-0 accent-(--brand-blue)"
                checked={selected}
                onChange={() => onChoose(conflict.key, option.listing_id)}
              />
              <span className="min-w-0 flex-1">
                <span className="block text-[14px] text-ink line-clamp-4">
                  {option.display}
                </span>
                <span className="mt-0.5 block text-[12px] text-ink-faint">
                  {from?.master
                    ? "from the master draft"
                    : `from "${(from?.title || "another draft").slice(0, 44)}"`}
                </span>
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}

export function MergeListingsDialog({ open, drafts, candidates = [], onClose, onMerged }) {
  // One ticked draft can't merge with itself, so the dialog opens by asking
  // what it merges with. Two or more ticked already answers that — it opens
  // on the master, exactly as it always did.
  const needsPartner = drafts.length < 2 && candidates.length > 0;
  const [partnerIds, setPartnerIds] = useState([]);
  const [masterId, setMasterId] = useState(drafts[0]?.session_id || "");
  const [step, setStep] = useState(needsPartner ? "partner" : "master");
  const [preview, setPreview] = useState(null);
  const [choices, setChoices] = useState({});
  const [loading, setLoading] = useState(false);
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState("");

  // Everything this merge covers: what was ticked in the queue, plus whatever
  // partners were picked in step zero.
  const pool = useMemo(
    () => [...drafts, ...candidates.filter((c) => partnerIds.includes(c.session_id))],
    [drafts, candidates, partnerIds],
  );
  const sources = pool.filter((d) => d.session_id !== masterId);
  const masterTitle = titleOf(pool.find((d) => d.session_id === masterId) || pool[0] || {});
  // Which draft an entry came from, for the "from …" line under each option.
  const byId = useMemo(() => Object.fromEntries(pool.map((d) => [
    d.session_id, { title: titleOf(d), master: d.session_id === masterId },
  ])), [pool, masterId]);

  // Dropping a partner that had been picked as master hands the role back to
  // the draft the seller ticked in the queue — the merge must always have a
  // master that's actually in it.
  const togglePartner = (id) => {
    const on = partnerIds.includes(id);
    setPartnerIds(on ? partnerIds.filter((x) => x !== id) : [...partnerIds, id]);
    if (on && masterId === id) setMasterId(drafts[0]?.session_id || "");
  };

  const review = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await postJson("/api/listings/merge/preview", {
        target_id: masterId,
        source_ids: sources.map((s) => s.session_id),
      });
      setPreview(res);
      setChoices(Object.fromEntries((res.conflicts || []).map((c) => [c.key, c.default])));
      setStep("review");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const merge = async () => {
    setMerging(true);
    setError("");
    try {
      const res = await postJson("/api/listings/merge", {
        target_id: masterId,
        source_ids: sources.map((s) => s.session_id),
        field_choices: choices,
      });
      onMerged(res, { masterId, title: masterTitle });
    } catch (e) {
      setError(e.message);
      setMerging(false);
    }
  };

  const conflicts = preview?.conflicts || [];
  const filled = preview?.auto_filled || [];

  return (
    <Dialog
      open={open}
      onClose={merging ? () => {} : onClose}
      wide
      title={step === "partner"
        ? "Which listing should it merge with?"
        : step === "master" ? "Which draft is the master?" : "Which entries win?"}
    >
      {step === "partner" ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink-secondary">
            Merging{" "}
            <strong className="font-semibold text-ink">"{titleOf(drafts[0] || {})}"</strong>{" "}
            — tick the draft it's a duplicate of{candidates.length > 1
              ? " (more than one is fine)" : ""}. Which one you keep, and what
            carries over from the other, you choose next.
          </p>
          <div className="flex flex-col gap-2 max-h-[46vh] overflow-y-auto pr-1">
            {candidates.map((item) => (
              <PartnerRow
                key={item.session_id}
                item={item}
                selected={partnerIds.includes(item.session_id)}
                onToggle={togglePartner}
              />
            ))}
          </div>
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button variant="primary" onClick={() => setStep("master")}
              disabled={!partnerIds.length}>
              Next: pick the master <ArrowRight aria-hidden />
            </Button>
          </div>
        </div>
      ) : step === "master" ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink-secondary">
            Every photo ends up on the master. The other{" "}
            {sources.length === 1 ? "draft is" : `${sources.length} drafts are`}{" "}
            consolidated into it and deleted — so pick the one you'd rather keep
            writing from.
          </p>
          <div className="flex flex-col gap-2">
            {pool.map((item) => (
              <MasterRow
                key={item.session_id}
                item={item}
                selected={item.session_id === masterId}
                onSelect={setMasterId}
              />
            ))}
          </div>
          {error && (
            <p className="flex items-start gap-1.5 text-[13px] text-error">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden /> {error}
            </p>
          )}
          <div className="flex flex-wrap justify-end gap-2 pt-1">
            {needsPartner ? (
              <Button variant="ghost" onClick={() => setStep("partner")} disabled={loading}>
                <ArrowLeft aria-hidden /> Change what merges
              </Button>
            ) : (
              <Button variant="ghost" onClick={onClose}>Cancel</Button>
            )}
            <Button variant="primary" onClick={review} loading={loading}
              disabled={!masterId || !sources.length}>
              Next: check the fields <ArrowRight aria-hidden />
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="rounded-[13px] border border-line bg-bg-sunken px-3.5 py-3">
            <p className="flex items-start gap-2 text-sm text-ink">
              <Crown size={16} className="mt-0.5 shrink-0 text-blue" aria-hidden />
              <span className="min-w-0">
                Keeping <strong className="font-semibold">"{masterTitle}"</strong>{" "}
                <span className="text-ink-secondary tabular-nums">
                  · {preview?.added_photos || 0} photo{preview?.added_photos === 1 ? "" : "s"} move over
                </span>
              </span>
            </p>
            <p className="mt-1.5 flex items-start gap-2 text-[13px] text-ink-secondary">
              <Trash2 size={14} className="mt-0.5 shrink-0" aria-hidden />
              <span className="min-w-0">
                {sources.map((s) => `"${titleOf(s).slice(0, 36)}"`).join(", ")}{" "}
                {sources.length === 1 ? "is" : "are"} deleted once this is done.
              </span>
            </p>
          </div>

          {conflicts.length > 0 ? (
            <>
              <p className="text-sm text-ink-secondary">
                {conflicts.length === 1
                  ? "One field is filled in differently on these drafts"
                  : `${conflicts.length} fields are filled in differently on these drafts`}
                {" "}— the master's entry is picked unless you say otherwise.
              </p>
              <div className="flex flex-col gap-2.5 max-h-[46vh] overflow-y-auto pr-1">
                {conflicts.map((conflict) => (
                  <ConflictField
                    key={conflict.key}
                    conflict={conflict}
                    drafts={byId}
                    chosen={choices[conflict.key]}
                    onChoose={(key, listingId) =>
                      setChoices((c) => ({ ...c, [key]: listingId }))}
                  />
                ))}
              </div>
            </>
          ) : (
            <p className="flex items-start gap-2 text-sm text-ink-secondary">
              <Check size={16} className="mt-0.5 shrink-0 text-success" aria-hidden />
              Nothing clashes — every field these drafts both filled in says the
              same thing.
            </p>
          )}

          {/* Blanks on the master that a duplicate fills in. Not a question —
              nothing is overwritten — but worth showing, because it is the
              half of the merge that quietly makes the listing better. */}
          {filled.length > 0 && (
            <div className="rounded-[13px] border border-line bg-bg p-3">
              <p className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
                <Sparkles size={13} className="text-blue" aria-hidden />
                Filled in from the other draft{sources.length === 1 ? "" : "s"}
              </p>
              <ul className="mt-1.5 flex flex-col gap-1">
                {filled.map((f) => (
                  <li key={f.key} className="text-[13px] text-ink-secondary">
                    <span className="font-semibold text-ink">{f.label}:</span>{" "}
                    <span className="line-clamp-2">{f.display}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {error && (
            <p className="flex items-start gap-1.5 text-[13px] text-error">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden /> {error}
            </p>
          )}

          <div className="flex flex-wrap justify-end gap-2 pt-1">
            <Button variant="ghost" onClick={() => setStep("master")} disabled={merging}>
              <ArrowLeft aria-hidden /> Change master
            </Button>
            <Button variant="primary" onClick={merge} loading={merging}>
              <Combine aria-hidden /> Merge {pool.length} drafts
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
