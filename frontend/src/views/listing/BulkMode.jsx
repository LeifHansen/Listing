import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Rocket, PenLine, ExternalLink, CheckCircle2, AlertTriangle, Combine, Trash2,
  ArrowRight, X,
} from "lucide-react";
import { cn, mediaUrl } from "@/lib/utils";
import {
  CONDITIONS, conditionLabel, conditionsFor, nearestCondition,
} from "@/lib/conditions";
import { api, postJson } from "@/lib/api";
import { apiUrl } from "@/lib/platform";
import { useApp } from "@/store";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input, Select, Toggle } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { AIStatusCard } from "@/components/ui/AIStatus";
import { BrandProgress } from "@/components/ui/Progress";
import { useToast } from "@/components/ui/Toaster";
import { MergeListingsDialog } from "@/components/MergeListingsDialog";
import { CategoryQuickPick } from "./CategoryQuickPick";
import { ShippingPolicySelect } from "./ShippingPolicySelect";
import {
  MarketTargetChips, publishListing, usePublishTargets, publishTally,
  UNCONFIRMED_PUBLISH,
} from "./publishShared";
import { blockerLabels, ebayBlockers, TITLE_MAX } from "./blockers";

/* Bulk mode: one photo dump spanning many items. The server groups the photos,
   identifies each item, and (optionally) publishes them; this component polls
   the job and renders a live queue with inline edits + publish controls. */

const PHASE_MESSAGES = {
  uploading: ["Uploading your photo pile…"],
  optimizing: ["Optimizing photos…", "Straightening sideways shots…"],
  grouping: ["Sorting photos into items…", "Matching angles of the same item…"],
  identifying: ["Identifying items…", "Writing titles & prices…", "Detecting brands…"],
};

// Background removal is a per-upload choice, so the progress text has to be
// one too: this said "removing backgrounds" over every batch, including the
// ones that never asked for it. job.remove_bg is what the batch was actually
// started with (absent on batches started before this shipped — which reads
// as "off", the safe way round: it never claims work that isn't happening).
function phaseMessages(phase, removeBg) {
  if (phase === "optimizing" && removeBg) {
    return ["Optimizing photos…", "Straightening & removing backgrounds…"];
  }
  return PHASE_MESSAGES[phase] || ["Working…"];
}

// Duplicate-suspect detection: two drafts whose titles share most of their
// meaningful words are probably the same item split in two — surface a hint
// pointing at "Merge into one" instead of silently letting both publish.
// Ticking either one of them is enough; the merge dialog asks for the other.
const STOP_WORDS = new Set(["the", "and", "with", "for", "size", "mens", "womens", "new", "vintage"]);
function titleTokens(t) {
  return new Set((t || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").split(/\s+/)
    .map((w) => w.replace(/s$/, ""))  // light stemming: phases ≈ phase
    .filter((w) => w.length > 2 && !STOP_WORDS.has(w)));
}
function sharesEnough(A, B) {
  if (A.size < 3 || B.size < 3) return false;
  let shared = 0;
  A.forEach((w) => { if (B.has(w)) shared += 1; });
  return shared >= 3 && shared / Math.min(A.size, B.size) >= 0.4;
}
function duplicateSuspects(drafts) {
  // Tokenize ONCE per draft, not once per pair: the comparison is already
  // quadratic, and re-splitting both titles inside it made a big batch chew
  // through thousands of regex passes on every render.
  const tokens = drafts.map((d) => titleTokens(d.listing?.title || d.title || ""));
  const pairs = [];
  for (let i = 0; i < drafts.length; i++) {
    for (let j = i + 1; j < drafts.length; j++) {
      if (sharesEnough(tokens[i], tokens[j])) pairs.push([drafts[i], drafts[j]]);
    }
  }
  return pairs;
}

// Selling formats, mirroring the full editor's Pricing card.
const LISTING_FORMATS = [
  ["FIXED_PRICE", "Buy It Now"],
  ["AUCTION", "Auction"],
  ["AUCTION_BIN", "Auction + BIN"],
];
// eBay's own recommendation replaces this at publish time; it's just the
// starting number in the box.
const DEFAULT_AD_RATE = 10;

/* eBay's conditions for one category, fetched once per category (conditionsFor
   caches), or null while it is loading or when the lookup could not be made —
   which every rule below reads as "we don't know", never as "anything goes". */
function useCategoryConditions(categoryId) {
  const cid = String(categoryId || "").trim();
  // The category is stored WITH the answer so a card whose category has just
  // changed reads as "don't know yet" rather than briefly showing the old
  // category's conditions — without a synchronous reset on every render.
  const [got, setGot] = useState({ cid: null, list: null });
  useEffect(() => {
    let live = true;
    conditionsFor(cid).then((list) => { if (live) setGot({ cid, list }); });
    return () => { live = false; };
  }, [cid]);
  return got.cid === cid ? got.list : null;
}

/* The dropdown's options: what the category offers once we know, the full
   list until then — plus whatever the listing is currently set to, always. A
   controlled <select> whose value isn't among its options renders BLANK, and
   a condition that looks unset is how a seller "fixes" a field that was
   already right. */
function conditionOptions(conditions, current) {
  const options = (conditions && conditions.length)
    ? conditions.map((c) => ({ value: c.enum, label: c.label || conditionLabel(c.enum) }))
    : CONDITIONS.map((c) => ({ value: c, label: conditionLabel(c) }));
  return options.some((o) => o.value === current) || !current
    ? options
    : [{ value: current, label: conditionLabel(current) }, ...options];
}

function BulkItemCard({
  item, checked, onCheck, onChange, onOpen, onPublish, publishing,
  onDelete, deleting, onDeletePhoto, targets,
}) {
  const l = item.listing || {};
  const editable = item.status !== "error";
  const fmt = (l.listing_format || "FIXED_PRICE").toUpperCase();
  const isAuction = fmt.startsWith("AUCTION");
  // Which conditions eBay offers for THIS item's category. The queue publishes
  // without ever opening the editor, so this is the only place the seller can
  // see them — and before it existed the dropdown offered all thirteen grades
  // for every category, which is how a bone fish figurine and a ceramic bear
  // went out as "Used - Good" and came back as error 25021.
  const conditions = useCategoryConditions(l.category_id);
  // A draft made before the server started fitting conditions to categories
  // (or one whose category the seller has just changed) can be sitting on a
  // grade this category doesn't offer. Move it to the closest one that fits,
  // where the seller can see it happen, rather than letting them press
  // Publish into a refusal. nearestCondition never crosses the new/used line;
  // where nothing fits it returns null and the blocker below stands.
  useEffect(() => {
    if (!editable || !conditions || !conditions.length || !l.condition) return;
    const fitted = nearestCondition(l.condition, conditions.map((c) => c.enum));
    if (fitted && fitted !== l.condition) onChange({ ...l, condition: fitted });
    // `l` is rebuilt on every change; the condition and the list are what
    // this actually watches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conditions, l.condition, editable]);
  // What is stopping THIS item from reaching eBay — the same rules the
  // editor and the drafts strip use (blockers.js). Target-aware: an
  // Etsy-only publish must not be gated on eBay-only fields (package weight,
  // eBay category).
  const blockers = item.status === "draft"
    ? ebayBlockers(l, { targets, conditions }) : [];
  // All of the item's photos, not just the first. An item that failed before
  // a listing existed still has the server-picked `thumb`.
  const photos = l.images?.length
    ? l.images.map((n) => ({ name: n, src: mediaUrl(item.session_id, n, 1) }))
    : (item.thumb ? [{ name: null, src: apiUrl(`${item.thumb}?v=1`) }] : []);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "bg-card rounded-card border shadow-card p-4 flex flex-col gap-3",
        item.status === "error" ? "border-warning/50" : "border-line",
      )}
    >
      <div className="flex items-center gap-3">
        {item.status === "draft" && (
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => onCheck(e.target.checked)}
            aria-label={`Select ${l.title || item.title || "item"}`}
            className="size-4 accent-(--brand-blue) shrink-0"
          />
        )}
        {/* One row of thumbnails, as many as the card is wide — extras wrap
            below the max-height and are clipped away. */}
        <div className="flex-1 min-w-0 flex flex-wrap gap-1.5 max-h-12 overflow-hidden">
          {photos.map((ph, i) => (
            <div key={ph.name || ph.src} className="relative size-12 shrink-0">
              <img
                src={ph.src}
                alt=""
                loading="lazy"
                className="size-full rounded-[10px] object-cover border border-line"
                onError={(e) => {
                  // Hide the whole tile, not just the <img> — otherwise a photo
                  // that 404s leaves its remove button floating over nothing.
                  const tile = e.currentTarget.parentElement;
                  if (tile) tile.style.display = "none";
                }}
              />
              {/* Quick-delete this photo without leaving the queue. Always
                  visible (a hover-only control is unreachable on touch), and
                  only where there's a real file behind the tile — the fallback
                  `thumb` isn't a photo of this listing's own to delete. */}
              {ph.name && item.status === "draft" && onDeletePhoto && (
                <button
                  type="button"
                  onClick={() => onDeletePhoto(ph.name)}
                  aria-label={`Remove photo ${i + 1}`}
                  title="Remove this photo"
                  className={cn(
                    "absolute top-0.5 right-0.5 z-10 grid place-items-center size-[18px]",
                    "rounded-full bg-card/90 backdrop-blur border border-line shadow-card",
                    "text-ink-faint cursor-pointer transition-colors",
                    "hover:text-error hover:border-error/40",
                  )}
                >
                  <X size={11} strokeWidth={3} aria-hidden />
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="shrink-0">
          {item.status === "published" && (
            <TagPill tone="green">
              <CheckCircle2 size={12} aria-hidden /> Live{item.listing_id ? ` · ${item.listing_id}` : ""}
            </TagPill>
          )}
          {item.status === "draft" && (
            blockers.length
              ? (
                <TagPill tone="yellow"
                  title={`eBay won't take this yet: ${blockerLabels(blockers)}`}>
                  <AlertTriangle size={12} aria-hidden /> Blocked
                </TagPill>
              )
              : <TagPill tone="blue">Draft</TagPill>
          )}
          {item.status === "error" && (
            <TagPill tone="yellow"><AlertTriangle size={12} aria-hidden /> Needs attention</TagPill>
          )}
        </div>
      </div>

      {editable ? (
        <>
          <div className="flex flex-col gap-1">
            <Input
              maxLength={TITLE_MAX}
              value={l.title || item.title || ""}
              placeholder="Title"
              aria-label="Title"
              onChange={(e) => onChange({ ...l, title: e.target.value })}
            />
            {/* Only once it starts to matter — these cards are dense, and a
                counter on every one of forty drafts is noise. */}
            {(l.title || item.title || "").length >= TITLE_MAX - 8 && (
              <span className="self-end text-[11px] font-semibold tabular-nums text-warning">
                {(l.title || item.title || "").length}/{TITLE_MAX}
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2.5">
            <Input
              type="number" step="0.01" min="0"
              placeholder={isAuction ? "Buy It Now" : "Price"} inputMode="decimal"
              aria-label={isAuction ? "Buy It Now price" : "Price"}
              value={l.price != null ? l.price : ""}
              onChange={(e) => onChange({ ...l, price: e.target.value === "" ? null : parseFloat(e.target.value) })}
            />
            <Select
              aria-label="Condition"
              value={l.condition || ""}
              onChange={(e) => onChange({ ...l, condition: e.target.value })}
            >
              {conditionOptions(conditions, l.condition).map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </Select>
          </div>

          {/* Selling format, and the fields each one needs. Defaults come from
              the account's listing settings, so a whole batch is priced the way
              the seller set up once — per-item overrides stay one tap away. */}
          <div className="grid grid-cols-2 gap-2.5">
            <Select
              aria-label="Listing format"
              value={fmt}
              onChange={(e) => onChange({ ...l, listing_format: e.target.value })}
            >
              {LISTING_FORMATS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </Select>
            {isAuction ? (
              <Input
                type="number" step="0.01" min="0" inputMode="decimal"
                placeholder="Start price" aria-label="Auction start price"
                value={l.auction_start_price != null ? l.auction_start_price : ""}
                onChange={(e) => onChange({
                  ...l,
                  auction_start_price: e.target.value === "" ? null : parseFloat(e.target.value),
                })}
              />
            ) : (
              <Select
                aria-label="Quantity"
                value={String(l.quantity || 1)}
                onChange={(e) => onChange({ ...l, quantity: parseInt(e.target.value, 10) })}
              >
                {[1, 2, 3, 4, 5, 10].map((q) => (
                  <option key={q} value={q}>{q === 1 ? "Qty 1" : `Qty ${q}`}</option>
                ))}
              </Select>
            )}
          </div>

          {/* Category on the card face: bulk batches are exactly where a
              wrong AI category slips through unnoticed. Edits ride the same
              local-then-save-on-publish path as the fields above. */}
          <CategoryQuickPick
            listing={l}
            onPick={(patch) => onChange({ ...l, ...patch })}
          />

          <ShippingPolicySelect
            value={l.fulfillment_policy_id}
            onChange={(id) => onChange({ ...l, fulfillment_policy_id: id })}
          />

          <div className="flex flex-wrap items-center gap-2.5">
            <Toggle
              checked={!!l.promote}
              onChange={(on) => onChange({
                ...l, promote: on,
                ad_rate_percent: on ? (l.ad_rate_percent || DEFAULT_AD_RATE) : 0,
              })}
              label="Promote"
            />
            {l.promote && (
              <Input
                type="number" step="0.5" min="0.5" max="100" inputMode="decimal"
                aria-label="Ad rate percent"
                className="w-24"
                value={l.ad_rate_percent || DEFAULT_AD_RATE}
                onChange={(e) => onChange({
                  ...l, ad_rate_percent: parseFloat(e.target.value) || DEFAULT_AD_RATE,
                })}
              />
            )}
            {l.promote && <span className="text-xs text-ink-faint">% ad rate</span>}
          </div>
        </>
      ) : (
        <p className="text-[13px] text-ink-secondary">
          {item.error || "Couldn't identify this item."}
        </p>
      )}
      {item.status === "draft" && blockers.length > 0 && (
        <p className="text-xs text-warning font-medium"
          title={blockers.map((b) => `${b.label}: ${b.why}`).join("\n")}>
          Keeping this off eBay: {blockerLabels(blockers)}
        </p>
      )}
      {item.status === "draft" && item.error && (
        <p className="text-xs text-warning font-medium">{item.error}</p>
      )}

      {/* flex-wrap: the buttons can't shrink (nowrap labels), so on narrow
          cards Publish must drop to its own right-aligned line instead of
          poking out past the card edge. */}
      <div className="flex flex-wrap items-center gap-2 mt-auto">
        {editable && (
          <Button variant="ghost" size="sm" onClick={onOpen}>
            <ExternalLink aria-hidden /> Review &amp; List
          </Button>
        )}
        {/* Not every auto-created draft is worth keeping — a duplicate you
            don't want to merge, or something the AI shouldn't have drafted.
            Deleting it here beats hunting for its card later. */}
        <Button variant="ghost" size="sm" onClick={onDelete} loading={deleting}
          aria-label="Delete this draft"
          className="text-ink-faint hover:text-error">
          <Trash2 aria-hidden /> Delete
        </Button>
        {item.status === "draft" && (
          <Button variant="secondary" size="sm" className="ml-auto"
            onClick={onPublish} loading={publishing}>
            <Rocket aria-hidden /> Publish
          </Button>
        )}
      </div>
    </motion.div>
  );
}

export function BulkQueue({ jobId, onExit, onSettled }) {
  const { setSession, loadListings, connectedMarketplaces } = useApp();
  const { toast, confirm } = useToast();

  // Bulk publish targets — the same remembered selection as the single-item
  // publish bar and the drafts strip (see publishShared).
  const {
    selected: bulkTargets, toggle: toggleBulkTarget, otherConnected,
    effectiveTargets,
  } = usePublishTargets();
  const [job, setJob] = useState(null);
  const [items, setItems] = useState([]);
  const [checked, setChecked] = useState({});
  const [publishing, setPublishing] = useState({});
  // { done, total } while a whole-batch publish is running; null otherwise.
  // Drives both the progress label and the disabled state that stops a second
  // concurrent pass. Same shape as DraftsStrip's.
  const [bulkProgress, setBulkProgress] = useState(null);
  const [deleting, setDeleting] = useState({});
  // The merge review dialog. `key` bumps on every open so the dialog remounts
  // with fresh state (which draft merges in, which is master, which entries
  // win) instead of reopening on the last merge's answers; `drafts` (ticked)
  // and `candidates` (the rest of the batch, offered as merge partners) are
  // the snapshot it was opened on, so the queue polling underneath can't
  // reshuffle it mid-review.
  const [merge, setMerge] = useState({ open: false, drafts: [], candidates: [], key: 0 });
  // Watching was given up on (job gone, or too many failed polls). Without it
  // the pre-first-poll "Uploading…" state below would spin forever.
  const [unwatched, setUnwatched] = useState(false);
  const stopped = useRef(false);
  const fails = useRef(0);
  const notFound = useRef(0);
  // Items merged away client-side — the still-running job's status would
  // otherwise resurrect them on the next poll.
  const removed = useRef(new Set());

  // Poll the job until done; items render as they arrive. Resilient to transient
  // poll failures — a busy server (heavy batch) can blip a request even though
  // the job is still running, so we retry instead of abandoning the batch.
  useEffect(() => {
    // No job id yet — the batch is still uploading from the store, and the
    // screen shows the upload phase until the id lands.
    if (!jobId) return;
    stopped.current = false;
    fails.current = 0;
    notFound.current = 0;
    // Resets "we stopped watching" for the NEW job. It cannot cascade — the
    // effect keys on jobId and this writes neither jobId nor anything jobId
    // is derived from — and it has to happen here rather than during render,
    // because what it is synchronizing with is the poll loop started below,
    // not anything React can see.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUnwatched(false);
    let timer;
    const poll = async () => {
      try {
        const j = await api(`/api/bulk/status/${jobId}`);
        if (stopped.current) return;
        fails.current = 0;
        notFound.current = 0;
        setJob(j);
        if (j.items?.length) {
          // Merge WITHOUT clobbering the user's inline edits: the server never
          // re-edits an item once it's identified, so for items we already have
          // we keep the local listing (which may hold edits like a changed
          // condition/price) and only pick up new items from the poll.
          //
          // The publish outcome is local too, and keeping only `listing` threw
          // it away. Cards are live while the batch still runs, so a seller can
          // publish one and have this poll overwrite status/listing_id/error
          // 1.5s later with the server's still-"draft" row: a failure lost its
          // reason and read as an un-published draft, and a SUCCESS did too --
          // inviting a second, duplicate, fee-incurring live listing.
          setItems((cur) => {
            const mine = new Map(cur.map((it) => [it.session_id, it]));
            return j.items
              .filter((srv) => !removed.current.has(srv.session_id))
              .map((srv) => {
                const local = mine.get(srv.session_id);
                if (!local) return srv;
                const merged = { ...srv, listing: local.listing ?? srv.listing };
                // Once this client has published an item, its own record of
                // that is newer than anything the batch job knows.
                if (local.status === "published" || local.listing_id || local.error) {
                  merged.status = local.status ?? merged.status;
                  merged.listing_id = local.listing_id ?? merged.listing_id;
                  merged.error = local.error ?? merged.error;
                }
                return merged;
              });
          });
          // Nothing is ticked for you. Selection means "I picked these" — so
          // the destructive buttons it arms (delete, merge) can never act on
          // a set the seller didn't choose, and publishing the whole batch is
          // its own button rather than the accident of leaving boxes alone.
        }
        if (!j.done) {
          timer = setTimeout(poll, 1500);
        } else {
          stopped.current = true;  // nothing left to watch — don't re-poll on focus
          loadListings({ quiet: true });
          onSettled?.();  // stop persisting; a reload shouldn't restore a done batch
        }
      } catch (e) {
        if (stopped.current) return;
        fails.current += 1;
        // A 404 means the server has no record of this job at all. Confirm it
        // with a second poll before believing it: a status check can 404 on a
        // blip (an auth hiccup mid-batch does it) while the batch itself is
        // still running, and declaring it dead is not something we can undo.
        const gone = (e.message || "").includes("(404)");
        notFound.current = gone ? notFound.current + 1 : 0;
        if (gone && notFound.current < 2) {
          timer = setTimeout(poll, 3000);
        } else if (gone) {
          // Terminal. Stop polling for good — including the visibility/focus
          // handler below, which otherwise re-ran this on every tab switch —
          // and mark the batch finished so the queue shows what happened.
          // Without this the progress bar sat spinning on the photo the batch
          // died at, with no result, no error, and no way out of the screen.
          stopped.current = true;
          onSettled?.();
          setUnwatched(true);
          loadListings({ quiet: true });  // whatever it did finish is in Drafts
          // A finished job, so the queue renders the outcome card with this
          // reason and a way off the screen. Clearing `busy` alone (unwatched)
          // stops the spinner but leaves nothing behind it: no explanation,
          // and no exit at all for a batch that died before drafting anything.
          setJob((j) => ({
            ...(j || {}),
            done: true,
            error: "This batch stopped early — the server restarted while it "
              + "was working, and no record of it survived. Anything it "
              + "finished is saved in Drafts; the rest need another run.",
          }));
          toast("This batch was interrupted (the server restarted). Any items it finished are saved in Drafts.",
            { kind: "warning" });
        } else if (fails.current < 6) {
          timer = setTimeout(poll, 3000);  // transient — the job is likely still running
        } else {
          setUnwatched(true);
          // Give up watching but KEEP it persisted — the batch may still be
          // finishing server-side, so the banner lets the user reopen and resume.
          toast("Lost the connection while watching this batch — it may still be finishing. Reopen the Sell tab to check, and see Drafts for completed items.",
            { kind: "warning" });
        }
      }
    };
    poll();
    // Browsers throttle timers hard in a hidden tab (down to about once a
    // minute, and frozen entirely after a while), so a batch watched from a
    // background tab looks stuck until a manual refresh. Poll immediately
    // whenever the tab comes back to the foreground.
    const onVisible = () => {
      if (document.visibilityState !== "visible" || stopped.current) return;
      clearTimeout(timer);
      fails.current = 0;
      poll();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      stopped.current = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [jobId, loadListings, toast, onSettled]);

  const updateItem = (sid, listing) => {
    setItems((cur) => cur.map((it) =>
      it.session_id === sid ? { ...it, listing } : it));
  };

  // Remove one photo from a draft straight from its thumbnail strip — the
  // fastest way to drop a blurry shot or a stray photo the grouper attached to
  // the wrong item. Optimistic: the tile goes immediately and comes back only
  // if the server delete fails. The shortened list is saved right away because
  // the file is really gone — an unsaved draft would keep pointing at it.
  const deletePhoto = async (it, name) => {
    const l = it.listing || {};
    const images = l.images || [];
    if (images.length <= 1) {
      toast("A listing needs at least one photo — add another before deleting this one.",
        { kind: "warning" });
      return;
    }
    const next = { ...l, images: images.filter((n) => n !== name) };
    updateItem(it.session_id, next);
    try {
      await postJson("/api/delete-image", { session_id: it.session_id, name });
      // Awaited. The file is really gone by this point, so a save that fails
      // silently leaves the draft pointing at a deleted photo -- and the
      // publish then hands eBay an image URL that 404s, which is the opaque
      // 25001 the README describes chasing.
      await postJson(`/api/save/${it.session_id}`, next);
    } catch (e) {
      updateItem(it.session_id, l);
      toast(`Couldn't delete the photo: ${e.message}`, { kind: "error" });
    }
  };

  // Open one item in the full editor. The batch stays in memory (no onExit)
  // so the editor's "Back to batch" button can bring this queue straight back.
  const openItem = (it) => {
    setSession({ sessionId: it.session_id, listing: it.listing, confidence: null });
  };

  const publishOne = useCallback(async (it) => {
    setPublishing((p) => ({ ...p, [it.session_id]: true }));
    try {
      // Persist inline edits first, then publish (one request per item — the
      // backend fans out to every selected marketplace); see publishShared.
      const res = await publishListing(it.session_id, it.listing, effectiveTargets);
      // Multi responses summarize per marketplace: "eBay ✓ · Etsy ✗".
      const summary = res.multi
        ? Object.entries(res.results || {})
            .map(([key, r]) => `${key === "ebay" ? "eBay" : key.charAt(0).toUpperCase() + key.slice(1)} ${r.published ? "✓" : r.ok ? "—" : "✗"}`)
            .join(" · ")
        : null;
      // Refused, unanswered, or live — the three outcomes, decided in one
      // place (see publishShared.publishTally). Not blockedReason directly:
      // eBay's catch-all for an account-level hold blames the title, and an
      // outcome the SERVER could not establish is not a rejection at all.
      const tally = publishTally(
        res, "Publish blocked — open the full editor to fix.");
      setItems((cur) => cur.map((x) => x.session_id === it.session_id
        ? {
            ...x,
            status: tally.published ? "published" : "draft",
            listing_id: (res.multi
              ? res.results?.ebay?.listing_id : res.listing_id) || null,
            error: tally.published
              ? (res.multi && Object.values(res.results || {}).some((r) => !r.ok)
                  ? `${summary} — open the full editor to fix the rest.` : null)
              : tally.reason,
          }
        : x));
      return tally;
    } catch (e) {
      // publishListing has already asked the server what became of a publish
      // whose answer was lost; reaching here with that flag still set means
      // it could not tell. Say so as its own outcome — "refused" it is not,
      // and the one thing this seller must not do is publish it again
      // without looking.
      const unconfirmed = !!e?.unknownOutcome;
      const message = unconfirmed ? UNCONFIRMED_PUBLISH : e.message;
      setItems((cur) => cur.map((x) => x.session_id === it.session_id
        ? { ...x, error: message } : x));
      return { published: false, unconfirmed, reason: message };
    } finally {
      setPublishing((p) => ({ ...p, [it.session_id]: false }));
    }
  }, [effectiveTargets]);

  // One card's Publish button. Asks first — it posts a real, fee-incurring
  // listing; "Publish selected" asks once for its whole set instead, which is
  // why the confirm lives here rather than inside publishOne.
  const confirmPublishOne = useCallback(async (it) => {
    const name = it.listing?.title || it.title || "this draft";
    if (!(await confirm({
      title: "Publish this draft live?",
      message: `"${name}" goes straight to your store.`,
      confirmLabel: "Publish live",
    }))) return;
    if ((await publishOne(it)).published) loadListings({ quiet: true });
  }, [confirm, publishOne, loadListings]);

  // Delete a draft straight from the queue — the counterpart to Merge for
  // duplicates you don't want to keep at all, and the way out for anything
  // the AI shouldn't have drafted.
  const deleteOne = async (it) => {
    const name = it.listing?.title || it.title || "this draft";
    if (!(await confirm({
      title: "Delete this draft?",
      message: `"${name}" will be permanently removed, photos included. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    }))) return;
    setDeleting((d) => ({ ...d, [it.session_id]: true }));
    try {
      await api(`/api/listings/${it.session_id}`, { method: "DELETE" });
    } catch (e) {
      // An item that never produced a record (a failed identify) has nothing
      // to delete server-side — dropping it from the queue is the whole job.
      if (!(e.message || "").includes("(404)")) {
        toast(`Couldn't delete: ${e.message}`, { kind: "error" });
        setDeleting((d) => ({ ...d, [it.session_id]: false }));
        return;
      }
    }
    removed.current.add(it.session_id);
    setItems((cur) => cur.filter((x) => x.session_id !== it.session_id));
    setChecked((c) => { const n = { ...c }; delete n[it.session_id]; return n; });
    setDeleting((d) => ({ ...d, [it.session_id]: false }));
    loadListings({ quiet: true });
  };

  const deleteSelected = async () => {
    const targets = items.filter((it) => checked[it.session_id]);
    if (!targets.length) { toast("Nothing selected to delete.", { kind: "warning" }); return; }
    if (!(await confirm({
      title: `Delete ${targets.length} draft${targets.length === 1 ? "" : "s"}?`,
      message: "They'll be permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete all selected",
      danger: true,
    }))) return;
    const ids = targets.map((t) => t.session_id);
    try {
      const res = await postJson("/api/listings/bulk-delete", { ids });
      const gone = new Set(res.deleted || []);
      gone.forEach((id) => removed.current.add(id));
      setItems((cur) => cur.filter((x) => !gone.has(x.session_id)));
      setChecked({});
      loadListings({ quiet: true });
      toast(`Deleted ${gone.size} draft${gone.size === 1 ? "" : "s"}.`
        + (res.skipped?.length ? ` ${res.skipped.length} couldn't be removed.` : ""),
        { kind: res.skipped?.length ? "warning" : "success" });
    } catch (e) {
      toast(`Couldn't delete: ${e.message}`, { kind: "error" });
    }
  };

  // Merge duplicate drafts of the SAME item into one listing. One tick is
  // enough to start: which OTHER draft it merges with, which of them is the
  // master, and whose entry wins where they disagree are all the seller's
  // calls — MergeListingsDialog asks them, in that order, before anything is
  // written.
  const mergeSelected = () => {
    const picked = items.filter((it) => it.status === "draft" && checked[it.session_id]);
    if (!picked.length) {
      toast("Tick the draft you want to merge.", { kind: "warning" });
      return;
    }
    // Everything else still in the batch is a candidate to merge it with; the
    // dialog only asks when the seller hasn't already ticked a second draft.
    const others = items.filter((it) => it.status === "draft" && !checked[it.session_id]);
    if (picked.length < 2 && !others.length) {
      toast("There's no other draft in this batch to merge with.", { kind: "warning" });
      return;
    }
    setMerge((m) => ({ open: true, drafts: picked, candidates: others, key: m.key + 1 }));
  };

  // The merge went through: drop the consolidated drafts, take the master's
  // merged listing back, and say what moved over.
  const onMerged = (res, { masterId, title }) => {
    const gone = new Set(res.removed || []);
    gone.forEach((id) => removed.current.add(id));
    setItems((cur) => cur
      .filter((it) => !removed.current.has(it.session_id))
      .map((it) => (it.session_id === masterId ? { ...it, listing: res.listing } : it)));
    setChecked((c) => {
      const next = { ...c };
      gone.forEach((id) => delete next[id]);
      return next;
    });
    setMerge((m) => ({ ...m, open: false }));
    loadListings({ quiet: true });
    const fields = res.applied?.length
      ? `, ${res.applied.length} field${res.applied.length === 1 ? "" : "s"} carried over`
      : "";
    toast(`Merged into "${title}" — ${res.added} photo${res.added === 1 ? "" : "s"} moved over${fields}.`,
      { kind: "success" });
  };

  // Publish a set of drafts: one request each (the backend fans each out to
  // every selected marketplace), behind ONE confirm for the whole set — the
  // point of a batch is not answering the same question twenty times.
  const publishMany = async (targets, { all = false } = {}) => {
    if (!targets.length) {
      toast(all ? "No drafts to publish." : "Tick the drafts you want to publish.",
        { kind: "warning" });
      return;
    }
    const targetNames = effectiveTargets && effectiveTargets.length > 1
      ? effectiveTargets
          .map((k) => (connectedMarketplaces.find((m) => m.key === k) || {}).label || k)
          .join(" and ")
      : "your eBay store";
    const n = targets.length;
    if (!(await confirm({
      title: `Publish ${all ? "all " : ""}${n} listing${n === 1 ? "" : "s"} live?`,
      message: `Each goes straight to ${targetNames}.`,
      confirmLabel: "Publish live",
    }))) return;
    let ok = 0, failed = 0, unconfirmed = 0;
    const reasons = [];
    // Guarded like the drafts strip's equivalent: this is a loop of real,
    // fee-incurring eBay calls that runs for minutes on a full batch, and the
    // button had no disabled state, no loading state and no once() wrapper --
    // so a second click started a CONCURRENT pass over the drafts the first
    // one had not reached yet, and published them twice.
    setBulkProgress({ done: 0, total: targets.length });
    try {
      for (const it of targets) {
        const res = await publishOne(it);
        if (res.published) ok++;
        // Counted apart from the refusals, because it is a different
        // instruction. A refused draft is opened and fixed; one whose answer
        // never came back is checked on eBay first, and lumping the two
        // together under "need attention" is how a live listing gets
        // published a second time.
        else if (res.unconfirmed) unconfirmed++;
        else { failed++; if (res.reason) reasons.push(res.reason); }
        setBulkProgress((p) => ({ ...p, done: ok + failed + unconfirmed }));
      }
    } finally {
      setBulkProgress(null);
    }
    loadListings({ quiet: true });
    // Say WHY, not just how many. A count on its own is unactionable, and the
    // failures here are usually all the same account-level hold -- five
    // rejections reading "5 need attention" sent the seller to inspect five
    // listings that were never the problem.
    const shared = reasons.length && reasons.every((r) => r === reasons[0])
      ? reasons[0] : null;
    toast(`Published ${ok} listing${ok === 1 ? "" : "s"}.`
      + (failed
          ? (shared
              ? ` ${failed} refused: ${shared}`
              : ` ${failed} need attention — see the queue.`)
          : "")
      + (unconfirmed
          ? ` ${unconfirmed} didn't answer in time and may already be live — `
            + `check your eBay store before publishing ${unconfirmed === 1 ? "it" : "them"} again.`
          : ""),
      { kind: failed || unconfirmed ? "warning" : "success" });
  };

  // The whole batch, no ticking required — the common ending for a batch the
  // seller has read through and is happy with.
  const publishAll = () =>
    publishMany(items.filter((it) => it.status === "draft"), { all: true });

  const publishSelected = () =>
    publishMany(items.filter((it) => it.status === "draft" && checked[it.session_id]));

  // Busy from the very first frame: the batch screen goes up on the click, so
  // it opens on "Uploading your photo pile…" while the photos are still going
  // out — before there is a job id, let alone a status to poll.
  const busy = !unwatched && (!job || !job.done);
  const phase = job?.phase || "uploading";
  // Phase-weighted % for the progress bar shown while the job runs; the
  // cards stream in below it as each item is drafted.
  const pct = Math.round((() => {
    const frac = (cur, tot) => (tot ? Math.min(1, (cur || 0) / tot) : 0);
    if (!job) return 3;
    if (job.done) return 100;
    if (phase === "uploading") return 5;
    if (phase === "optimizing") return 10 + 35 * frac(job.current, job.total_photos);
    if (phase === "grouping") return 50;
    if (phase === "identifying") return 55 + 44 * frac(job.current, job.total_items);
    return 95;
  })());
  const drafts = items.filter((it) => it.status === "draft");
  // What the selection-driven buttons are armed by. Drafts for publish/merge
  // (a published or failed item is neither), every ticked item for delete.
  const selectedDrafts = drafts.filter((d) => checked[d.session_id]).length;
  const selectedCount = items.filter((it) => checked[it.session_id]).length;
  const blocked = drafts.filter(
    (it) => ebayBlockers(it.listing, { targets: effectiveTargets }).length > 0);
  // Memoized: the queue re-renders on every status poll and on every keystroke
  // in a card, and the pairwise scan is quadratic in the size of the batch.
  // Keyed on what the scan actually reads — ids and titles — so a poll that
  // changed nothing, or a keystroke in a price field, re-uses the last answer.
  const dupeKey = drafts
    .map((d) => `${d.session_id}:${d.listing?.title || d.title || ""}`)
    .join("|");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const dupes = useMemo(() => duplicateSuspects(drafts), [dupeKey]);
  const progressDetail = phase === "identifying" && job?.total_items
    ? ` (${job.current}/${job.total_items})`
    : phase === "optimizing" && job?.total_photos
      ? ` (${Math.min(job.current || 0, job.total_photos)}/${job.total_photos} photos)`
      : phase === "grouping" && job?.total_photos
        ? ` (${job.total_photos} photos)` : "";

  return (
    <div className="flex flex-col gap-4">
      {busy && (
        <div className="flex flex-col gap-3">
          <AIStatusCard messages={[
            // A batch the server picked back up after a restart keeps the same
            // job id, so this view just carries on polling. Say so, or the
            // count appearing to jump reads as a glitch.
            ...(job?.resumed ? ["Picking your batch back up where it stopped…"] : []),
            ...phaseMessages(phase, job?.remove_bg).map((m) => m + progressDetail),
          ]} />
          <BrandProgress
            className="px-1"
            value={pct}
            caption={items.length
              ? `${items.length} item${items.length === 1 ? "" : "s"} drafted so far`
              : null}
          />
        </div>
      )}
      {job?.done && (
        <Card className={cn("py-4", job.error ? "border-warning/40" : "border-success/30")}>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm font-semibold text-ink flex items-center gap-2 flex-1 min-w-0">
              {job.error
                ? <><AlertTriangle size={17} className="text-warning" aria-hidden /> {job.error}</>
                : <>
                    <CheckCircle2 size={17} className="text-success" aria-hidden />
                    <span title="Also saved in Drafts — review below or come back anytime.">
                      {items.length} item{items.length === 1 ? "" : "s"} queued as drafts
                    </span>
                  </>}
            </p>
            {/* The guided path: step through each draft in the full editor —
                preview, tweak, publish — and the post-publish screen's "Next
                Draft" keeps the assembly line moving through the batch.
                With no drafts to step through (a batch that failed before it
                identified anything) this is the only way off the screen — the
                queue's own toolbar renders only alongside drafts. */}
            {drafts.length > 0 ? (
              <Button variant="primary" onClick={() => openItem(drafts[0])}
                title="Review each draft in the full editor and publish as you go.">
                Preview &amp; list <ArrowRight aria-hidden />
              </Button>
            ) : (
              <Button variant="secondary" onClick={onExit}>
                <PenLine aria-hidden /> Start another batch
              </Button>
            )}
          </div>
        </Card>
      )}

      {/* Background removal was asked for but couldn't run: the photos were
          deliberately kept unchanged, so say why instead of leaving the user
          to conclude the feature is broken. */}
      {job?.done && job.bg_error && (
        <Card className="py-3.5 border-warning/40 bg-warning-soft">
          <p className="text-sm text-ink flex items-start gap-2">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            <span title="The photos were saved unchanged.">
              <strong>Backgrounds weren't removed</strong> on {job.bg_failed || "some"}{" "}
              photo{job.bg_failed === 1 ? "" : "s"} —{" "}
              {String(job.bg_error).trim().replace(/[.!?]*$/, "")}. Your photos
              were saved unchanged.
            </span>
          </p>
        </Card>
      )}

      {job?.done && (() => {
        if (!dupes.length) return null;
        const [a, b] = dupes[0];
        return (
          <Card className="py-3.5 border-warning/40 bg-warning-soft">
            <p className="text-sm text-ink flex items-start gap-2">
              <Combine size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <span title="If they're the same item, tick one of them and hit Merge into one — the dialog asks which draft it merges with before anything is written.">
                <strong>Possible duplicate{dupes.length > 1 ? "s" : ""}:</strong>{" "}
                "{(a.listing?.title || a.title || "").slice(0, 40)}…" &amp;{" "}
                "{(b.listing?.title || b.title || "").slice(0, 40)}…"
                {dupes.length > 1 ? ` (+${dupes.length - 1} more)` : ""} — tick one
                and hit <strong>Merge into one</strong>.
              </span>
            </p>
          </Card>
        );
      })()}

      {job?.done && blocked.length > 0 && (
        <Card className="py-3.5 border-warning/40 bg-warning-soft">
          <p className="text-sm text-ink flex items-start gap-2">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            {/* Each card names its OWN blocking fields — this banner only
                says how many are affected, so it can't contradict them. */}
            <span title="Each blocked card lists the fields eBay is refusing it over. Fill them in on the card or in the full editor.">
              <strong>{blocked.length}</strong> of {drafts.length} draft{drafts.length === 1 ? "" : "s"}{" "}
              can&apos;t reach eBay yet — each one is marked{" "}
              <strong className="text-warning">Blocked</strong> below, with the fields that are holding it.
            </span>
          </p>
        </Card>
      )}

      {drafts.length > 0 && (
        <MarketTargetChips selected={bulkTargets} toggle={toggleBulkTarget}
          otherConnected={otherConnected} />
      )}

      {/* The batch toolbar. Every selection-driven button STAYS PUT and greys
          out when nothing is ticked, rather than appearing on the first tick:
          a control that pops into existence shifts the row under the cursor,
          and one that is simply grey says what ticking a box is even for. */}
      {drafts.length > 0 && (
        <div className="flex flex-wrap items-center gap-2.5">
          <Button variant="primary" onClick={publishAll}
            disabled={!!bulkProgress} loading={!!bulkProgress}
            title="Publish every draft in this batch — no ticking required.">
            <Rocket aria-hidden />
            {bulkProgress
              ? `Publishing ${Math.min(bulkProgress.done + 1, bulkProgress.total)} of ${bulkProgress.total}…`
              : `Publish all (${drafts.length})`}
          </Button>
          <Button variant="secondary" onClick={publishSelected}
            disabled={!selectedDrafts || !!bulkProgress}
            title={selectedDrafts
              ? `Publish the ${selectedDrafts} ticked draft${selectedDrafts === 1 ? "" : "s"}.`
              : "Tick the drafts you want to publish."}>
            <Rocket aria-hidden /> Publish selected ({selectedDrafts})
          </Button>
          <Button variant="secondary" onClick={mergeSelected}
            disabled={!selectedDrafts}
            title={selectedDrafts
              ? "Same item split into duplicates? Pick what it merges with, which draft is the master, and whose entries win."
              : "Tick a draft to merge it with another."}>
            <Combine aria-hidden /> Merge into one
          </Button>
          {/* Duplicates you'd rather drop than merge, and anything the batch
              shouldn't have drafted. */}
          <Button variant="danger" onClick={deleteSelected}
            disabled={!selectedCount}
            title={selectedCount
              ? `Permanently delete the ${selectedCount} ticked draft${selectedCount === 1 ? "" : "s"}.`
              : "Tick the drafts you want to delete."}>
            <Trash2 aria-hidden /> Delete selected ({selectedCount})
          </Button>
          <Button variant="ghost" onClick={onExit}>
            <PenLine aria-hidden /> Start another batch
          </Button>
        </div>
      )}

      {merge.drafts.length > 0 && (
        <MergeListingsDialog
          key={merge.key}
          open={merge.open}
          drafts={merge.drafts}
          candidates={merge.candidates}
          onClose={() => setMerge((m) => ({ ...m, open: false }))}
          onMerged={onMerged}
        />
      )}

      {/* Preview cards stream in one by one, as each item is drafted. */}
      <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">
        <AnimatePresence>
          {items.map((it) => (
            <BulkItemCard
              key={it.session_id}
              item={it}
              checked={!!checked[it.session_id]}
              onCheck={(v) => setChecked((c) => ({ ...c, [it.session_id]: v }))}
              onChange={(l) => updateItem(it.session_id, l)}
              onOpen={() => openItem(it)}
              onPublish={() => confirmPublishOne(it)}
              publishing={!!publishing[it.session_id]}
              onDelete={() => deleteOne(it)}
              deleting={!!deleting[it.session_id]}
              onDeletePhoto={(name) => deletePhoto(it, name)}
              targets={effectiveTargets}
            />
          ))}
        </AnimatePresence>
      </div>
    </div>
  );
}
