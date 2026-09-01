import { useState } from "react";
import { motion } from "framer-motion";
import {
  FilePen, Rocket, PenLine, CheckSquare, Trash2, X, Truck, AlertTriangle,
} from "lucide-react";
import { patchJson, pollJob, postJson } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ListingCard } from "@/components/ListingCard";
import { ViewToggle } from "@/components/ui/ViewToggle";
import { CategoryQuickPick } from "./CategoryQuickPick";
import { ShippingPolicySelect } from "./ShippingPolicySelect";
import {
  MarketTargetChips, publishListing, usePublishTargets, publishTally,
  UNCONFIRMED_PUBLISH,
} from "./publishShared";
import { blockerLabels, ebayBlockers } from "./blockers";

/* The drafts experience on the merged Sell screen: every draft one click
   from Publish or Review & List, plus select-mode bulk publish/delete.
   Renders nothing when there are no (matching) drafts — the upload box
   directly above is the empty-state CTA. */

const isDraft = (item) => item.status === "draft" || item.status === "dry_run";

// Shipping policy right on a draft's card — the same one control the editor
// and the bulk queue use (see ShippingPolicySelect). Saves on change.
function DraftShipping({ item, className }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [value, setValue] = useState(item.listing?.fulfillment_policy_id || "");
  const save = async (id) => {
    const previous = value;
    setValue(id);
    try {
      // PATCH, not a full save. This used to spread `item.listing` — the copy
      // loaded whenever /api/listings last ran — so choosing a shipping policy
      // from a card also wrote back a title someone had since fixed in the
      // editor, or anything a background sync had pulled in. Sending one field
      // means there is no stale copy to send.
      await patchJson(`/api/listings/${item.id}`, {
        fulfillment_policy_id: id,
      });
      // Still refreshed: publishItem re-saves this card's in-memory
      // item.listing on the way to publishing, so without this the policy just
      // chosen was overwritten with the stale one at the moment it mattered,
      // and the listing went live with the wrong shipping.
      await loadListings({ quiet: true });
    } catch (e) {
      // Put the dropdown back. Leaving it on the new value showed a policy
      // that was never saved, long after the toast had gone.
      setValue(previous);
      toast(`Couldn't save the shipping policy: ${e.message}`, { kind: "error" });
    }
  };
  return (
    <div className={cn("flex items-center gap-1.5", className || "mt-1.5")}
      title="Shipping policy for this draft">
      <Truck size={14} className="shrink-0 text-ink-faint" aria-hidden />
      <ShippingPolicySelect
        className="h-9 text-[13px]"
        value={value}
        onChange={save}
      />
    </div>
  );
}

// Category display + quick fix right on the draft card — a miscategorized
// item is the AI misfire that costs most once it's published, so the pick
// has to be visible (and fixable) without opening the full editor.
function DraftCategory({ item }) {
  const { loadListings } = useApp();
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const save = async (patch) => {
    setSaving(true);
    try {
      // Same reason as DraftShipping above: the patch names the two category
      // fields and leaves everything else as stored.
      await patchJson(`/api/listings/${item.id}`, patch);
      // Refresh the cache so the card (and its Publish gate) sees the change.
      await loadListings({ quiet: true });
    } catch (e) {
      toast(`Couldn't save the category: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };
  return <CategoryQuickPick listing={item.listing} onPick={save} saving={saving} />;
}

// A multi-marketplace publish response as one toast line: "eBay ✓ · Etsy ✗".
function resultSummary(res) {
  if (!res.multi) return null;
  return Object.entries(res.results || {})
    .map(([key, r]) => `${key === "ebay" ? "eBay" : key.charAt(0).toUpperCase() + key.slice(1)} ${r.published ? "✓" : r.ok ? "—" : "✗"}`)
    .join(" · ");
}

export function DraftsStrip({ search = "" }) {
  const {
    listingsState, openListing, loadListings, patchListing, deleteListing,
    bulkDeleteListings,
    metricsById, skippedDraftIds, toggleSkipDraft,
    listingsLayout, setListingsLayout,
  } = useApp();
  const { confirm, toast } = useToast();
  const { selected, toggle, otherConnected, effectiveTargets } = usePublishTargets();
  // Drafts follow the same grid/list preference as the listings manager
  // below — one Sell screen, one layout.
  const list = listingsLayout === "list";

  const [selecting, setSelecting] = useState(false);
  const [sel, setSel] = useState({});
  const [publishing, setPublishing] = useState({});   // id -> bool
  const [startingOver, setStartingOver] = useState(null);
  // Bulk publish runs one listing at a time (eBay's API is per-item), which on
  // a big selection is a long wait — so the button counts it off out loud.
  const [bulkProgress, setBulkProgress] = useState(null); // { done, total }

  const q = search.trim().toLowerCase();
  const drafts = listingsState.items
    .filter(isDraft)
    .filter((i) => !q
      || (i.listing?.title || i.title || "").toLowerCase().includes(q)
      || (i.listing?.brand || "").toLowerCase().includes(q)
      || (i.listing?.description || "").toLowerCase().includes(q))
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  if (!drafts.length) return null;

  // Both bulk actions work on the drafts you can actually see: narrowing the
  // search after selecting must not publish or delete something off-screen.
  const selectedDrafts = drafts.filter((d) => sel[d.id]);
  // A draft eBay would refuse can't be published from here — the per-card
  // Publish button is disabled and says which field is holding it (see
  // blockers.js, the same rules the editor and the bulk queue use). Bulk
  // publish holds the same line: those drafts are left selected and counted,
  // not fired off to fail one at a time.
  const readyToPublish = selectedDrafts.filter(
    (d) => ebayBlockers(d.listing || {}, { targets: effectiveTargets }).length === 0);
  const allSelected = drafts.length > 0 && selectedDrafts.length === drafts.length;
  const exitSelect = () => { setSelecting(false); setSel({}); };
  const toggleAll = () => setSel(allSelected
    ? {}
    : Object.fromEntries(drafts.map((i) => [i.id, true])));

  // Where a publish lands, in words — for the confirm dialog.
  const targetNames = effectiveTargets && effectiveTargets.length > 1
    ? effectiveTargets
        .map((k) => k === "ebay" ? "eBay" : k.charAt(0).toUpperCase() + k.slice(1))
        .join(" and ")
    : "your eBay store";

  // Publish one saved draft. Returns whether it went live; the caller owns the
  // toast and the listings refresh, so a bulk run does one of each at the end
  // rather than one per item.
  const publishItem = async (item) => {
    setPublishing((p) => ({ ...p, [item.id]: true }));
    try {
      const res = await publishListing(item.id, item.listing || {}, effectiveTargets);
      // Move the card out of Drafts on the spot — the refresh below confirms
      // it, but a live listing must never linger under Drafts waiting for one.
      if (res.published) patchListing(item.id, { status: "published" });
      // The same three-way read the batch queue uses. An outcome the server
      // could not establish stays a draft here (it may not be one on eBay,
      // which is exactly why the seller is sent to look) but it is never
      // counted or worded as a refusal.
      const tally = publishTally(
        res, "Publish blocked — open the draft to see what to fix.");
      return { published: tally.published, res, reason: tally.reason,
               unconfirmed: tally.unconfirmed,
               ...(tally.unconfirmed ? { error: UNCONFIRMED_PUBLISH } : {}) };
    } catch (e) {
      // publishListing has already asked the server what became of a publish
      // whose answer was lost; still flagged here means it could not tell.
      // Not a refusal, and not something to retry blind — see BulkMode.
      if (e?.unknownOutcome) {
        return { published: false, unconfirmed: true, error: UNCONFIRMED_PUBLISH };
      }
      return { published: false, error: e.message };
    } finally {
      setPublishing((p) => ({ ...p, [item.id]: false }));
    }
  };

  const publishOne = async (item) => {
    // Publishing two drafts asks first; publishing one used to post a real,
    // fee-incurring listing on a single tap from the card — the more
    // dangerous action had the weaker gate. Same question, same wording.
    const name = item.listing?.title || item.title || "this draft";
    if (!(await confirm({
      title: "Publish this draft live?",
      message: `"${name}" goes straight to ${targetNames}.`,
      confirmLabel: "Publish live",
    }))) return false;
    const { published, res, error, reason, unconfirmed } = await publishItem(item);
    if (error) {
      // "Publish error" is the wrong headline for a publish that may have
      // worked — the sentence already says what to do, and calling it an
      // error is what makes someone press the button again.
      toast(unconfirmed ? error : `Publish error: ${error}`,
        { kind: unconfirmed ? "warning" : "error" });
      // The listings refresh still runs: if it DID land, the card should
      // leave Drafts on its own rather than sit there inviting a retry.
      if (unconfirmed) await loadListings({ quiet: true });
      return false;
    }
    const summary = resultSummary(res);
    if (published) {
      const partial = res.multi
        && Object.values(res.results || {}).some((r) => !r.ok);
      toast(partial
        ? `${summary} — open the listing to fix the rest.`
        : (res.message || "Published! It's live now."),
        { kind: partial ? "warning" : "success" });
    } else {
      // The reason publishItem worked out, not res.message — see
      // publishShared: eBay's catch-all for an account-level hold blames the
      // title. (An unanswered publish never reaches here; it left through the
      // `error` branch above with its own sentence.)
      toast(reason, { kind: "warning" });
    }
    await loadListings({ quiet: true });
    return published;
  };

  const publishSelected = async () => {
    if (!selectedDrafts.length) {
      toast("Nothing selected to publish.", { kind: "warning" });
      return;
    }
    const notReady = selectedDrafts.length - readyToPublish.length;
    if (!readyToPublish.length) {
      toast(`${notReady === 1 ? "That draft has" : `All ${notReady} selected drafts have`} fields eBay won't accept without — open them to see which.`,
        { kind: "warning" });
      return;
    }
    if (!(await confirm({
      title: `Publish ${readyToPublish.length} draft${readyToPublish.length === 1 ? "" : "s"} live?`,
      message: `Each goes straight to ${targetNames}.`
        + (notReady
          ? ` ${notReady} of the ${selectedDrafts.length} selected ${notReady === 1 ? "is" : "are"} still blocked by a field eBay requires, and will stay ${notReady === 1 ? "a draft" : "drafts"}.`
          : ""),
      confirmLabel: "Publish live",
    }))) return;
    let ok = 0, failed = 0, unconfirmed = 0;
    const reasons = [];
    setBulkProgress({ done: 0, total: readyToPublish.length });
    try {
      for (const item of readyToPublish) {
        const out = await publishItem(item);
        if (out.published) ok++;
        // A publish nobody got an answer to is its own outcome. Counting it
        // as a refusal tells the seller to open a draft and fix it, when the
        // listing may well be live on eBay already and the only safe next
        // step is to look.
        else if (out.unconfirmed) unconfirmed++;
        else {
          failed++;
          // WHY, not just how many. publishItem hands back the response and
          // this discarded it, so a run where every listing was refused for
          // one account-level hold reported "5 need attention — open them to
          // fix" and sent the seller to inspect five listings that were never
          // the problem. That is the shape of the failures in production.
          reasons.push(out.error || out.reason);
        }
        setBulkProgress((p) => ({ ...p, done: ok + failed + unconfirmed }));
      }
    } finally {
      setBulkProgress(null);
    }
    await loadListings({ quiet: true });
    exitSelect();
    const shared = reasons.length && reasons.every((r) => r === reasons[0])
      ? reasons[0] : null;
    toast(`Published ${ok} listing${ok === 1 ? "" : "s"}.`
      + (failed
          ? (shared ? ` All ${failed} were refused: ${shared}`
                    : ` ${failed} need attention — open them to fix.`)
          : "")
      + (unconfirmed
          ? ` ${unconfirmed} didn't answer in time and may already be live — `
            + `check your eBay store before publishing ${unconfirmed === 1 ? "it" : "them"} again.`
          : "")
      + (notReady ? ` ${notReady} skipped — blocked by a field eBay requires.` : ""),
      { kind: failed || unconfirmed || notReady ? "warning" : "success" });
  };

  const deleteSelected = async () => {
    if (!selectedDrafts.length) return;
    if (!(await confirm({
      title: `Delete ${selectedDrafts.length} draft${selectedDrafts.length === 1 ? "" : "s"}?`,
      message: "They'll be permanently removed, photos included. This can't be undone.",
      confirmLabel: "Delete all selected",
      danger: true,
    }))) return;
    if (await bulkDeleteListings(selectedDrafts.map((d) => d.id))) exitSelect();
  };

  const askDelete = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (await confirm({
      title: "Delete this listing?",
      message: `"${name}" will be permanently removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    })) deleteListing(item.id);
  };

  // Start over on a draft: throw away the drafted copy and re-run the AI over
  // the listing's own photos.
  const startOver = async (item) => {
    const name = item.listing?.title || item.title || "this draft";
    if (!(await confirm({
      title: "Start this listing over?",
      message: `The AI will look at "${name}"'s photos again and rewrite the `
        + "title, description, and item specifics. Your photos are kept; any "
        + "edits you made to the text are replaced.",
      confirmLabel: "Start over",
    }))) return;
    setStartingOver(item.id);
    try {
      const { job_id } = await postJson(`/api/identify-async/${item.id}`, {});
      await pollJob(job_id);
      await loadListings({ quiet: true });
      toast("Rewritten from the photos.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't start over: ${e.message}`, { kind: "error" });
    } finally {
      setStartingOver(null);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <SectionHeader
        icon={FilePen}
        title={`Drafts (${drafts.length})`}
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <MarketTargetChips selected={selected} toggle={toggle}
              otherConnected={otherConnected} />
            <ViewToggle value={listingsLayout} onChange={setListingsLayout} />
            {!selecting && (
              <Button variant="ghost" size="sm" onClick={() => setSelecting(true)}>
                <CheckSquare aria-hidden /> Select
              </Button>
            )}
          </div>
        }
      />

      {/* Select mode gets its own bar rather than a row of buttons wedged into
          the header: on a phone the bulk actions used to wrap behind the title,
          which made "publish everything I just drafted" look impossible. */}
      {selecting && (
        <div className="sticky top-2 z-20 flex flex-wrap items-center gap-2 rounded-card
          border border-blue/35 bg-blue-soft/90 backdrop-blur px-3 py-2.5 shadow-card">
          <label className="flex items-center gap-2 text-[13px] font-semibold text-ink cursor-pointer select-none mr-1">
            <input
              type="checkbox"
              checked={allSelected}
              ref={(el) => { if (el) el.indeterminate = selectedDrafts.length > 0 && !allSelected; }}
              onChange={toggleAll}
              className="size-4 accent-(--brand-blue) cursor-pointer"
            />
            Select all
            <span className="text-ink-secondary font-medium tabular-nums">
              ({selectedDrafts.length} of {drafts.length})
            </span>
          </label>
          <div className="flex flex-wrap items-center gap-2 ml-auto">
            <Button variant="primary" size="sm" onClick={publishSelected}
              disabled={!selectedDrafts.length || !!bulkProgress}
              loading={!!bulkProgress}
              title={selectedDrafts.length && !readyToPublish.length
                ? "Every selected draft is blocked by a field eBay requires — open them to finish"
                : undefined}>
              <Rocket aria-hidden />
              {bulkProgress
                ? `Publishing ${Math.min(bulkProgress.done + 1, bulkProgress.total)} of ${bulkProgress.total}…`
                : `Publish selected (${selectedDrafts.length})`}
            </Button>
            <Button variant="danger" size="sm" onClick={deleteSelected}
              disabled={!selectedDrafts.length || !!bulkProgress}>
              <Trash2 aria-hidden /> Delete selected ({selectedDrafts.length})
            </Button>
            <Button variant="ghost" size="sm" onClick={exitSelect} disabled={!!bulkProgress}>
              <X aria-hidden /> Cancel
            </Button>
          </div>
        </div>
      )}
      <div className={cn(list
        ? "flex flex-col gap-3"
        : "grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4")}>
        {drafts.map((item, i) => {
          const blockers = ebayBlockers(item.listing || {}, { targets: effectiveTargets });
          return (
            <motion.div
              key={item.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
            >
              {/* Delete lives in the labeled row below, not as another tiny
                  icon in the card's corner cluster. */}
              <ListingCard item={item} layout={listingsLayout} onOpen={openListing}
                onStartOver={startOver}
                startingOver={startingOver === item.id}
                onSkip={() => toggleSkipDraft(item.id)}
                skipped={skippedDraftIds.has(item.id)}
                metrics={metricsById[item.id]}
                selectable={selecting}
                selected={!!sel[item.id]}
                onSelect={() => setSel((s) => ({ ...s, [item.id]: !s[item.id] }))} />
              {!selecting && (
                <div className={cn(list
                  ? "mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1.5 pl-1"
                  : "contents")}>
                  <div className={cn("flex items-center gap-1.5", !list && "mt-1.5")}>
                    <Button variant="secondary" size="sm" className={cn(!list && "flex-1")}
                      onClick={() => publishOne(item)}
                      loading={!!publishing[item.id]}
                      disabled={blockers.length > 0}
                      title={blockers.length
                        ? `eBay won't take this yet — ${blockerLabels(blockers)}. Open Review & List to finish.`
                        : undefined}>
                      <Rocket aria-hidden /> Publish
                    </Button>
                    <Button variant="ghost" size="sm" className={cn(!list && "flex-1")}
                      onClick={() => openListing(item.id)}>
                      <PenLine aria-hidden /> Review &amp; List
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => askDelete(item)}
                      aria-label="Delete this draft" title="Delete this draft"
                      className="shrink-0 text-ink-faint hover:text-error">
                      <Trash2 aria-hidden />
                    </Button>
                  </div>
                  {/* In a list row this drops to the end of the wrapped line
                      (order-last); stacked cards keep it under the buttons,
                      where it reads as the reason Publish is disabled. */}
                  {blockers.length > 0 && (
                    <p className={cn(
                      "flex items-start gap-1.5 text-[12px] font-semibold text-warning",
                      list ? "w-full order-last" : "mt-1.5")}
                      title={blockers.map((b) => `${b.label}: ${b.why}`).join("\n")}>
                      <AlertTriangle size={13} className="shrink-0 mt-px" aria-hidden />
                      <span className="min-w-0">
                        Keeping this off eBay: {blockerLabels(blockers)}
                      </span>
                    </p>
                  )}
                  {/* Category and shipping stay reachable in both layouts —
                      a wrong category is the AI misfire that costs most, and
                      hiding it behind a layout switch would bury it. */}
                  <div className={cn(list && "min-w-0 w-full sm:w-56")}>
                    <DraftCategory item={item} />
                  </div>
                  <DraftShipping item={item} className={cn(list ? "min-w-0" : undefined)} />
                </div>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
