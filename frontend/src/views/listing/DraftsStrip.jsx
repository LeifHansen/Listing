import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  FilePen, Rocket, PenLine, CheckSquare, Trash2, X, Truck,
} from "lucide-react";
import { api, pollJob, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Select } from "@/components/ui/fields";
import { ListingCard } from "@/components/ListingCard";
import {
  MarketTargetChips, missingRequired, publishListing, usePublishTargets,
} from "./publishShared";

/* The drafts experience on the merged Sell screen: every draft one click
   from Publish or Review & List, plus select-mode bulk publish/delete.
   Renders nothing when there are no (matching) drafts — the upload box
   directly above is the empty-state CTA. */

const isDraft = (item) => item.status === "draft" || item.status === "dry_run";

// Shipping service picker right on a draft's card — the same eBay fulfillment
// policies the editor and bulk queue offer, account default preselected.
// Saves the moment it's changed.
function DraftShipping({ item }) {
  const { ebay, policiesData, setPoliciesData } = useApp();
  const { toast } = useToast();
  const [value, setValue] = useState(item.listing?.fulfillment_policy_id || "");
  useEffect(() => {
    if (!ebay.connected || policiesData) return;
    api("/api/ebay/policies").then(setPoliciesData).catch(() => {});
  }, [ebay.connected, policiesData, setPoliciesData]);
  if (!ebay.connected) return null;
  const policies = policiesData?.policies?.fulfillment || [];
  if (!policies.length) return null;
  const accountDefault = policiesData?.selected?.fulfillment_policy_id || "";
  const save = async (id) => {
    setValue(id);
    try {
      await postJson(`/api/save/${item.id}`, {
        ...(item.listing || {}), fulfillment_policy_id: id,
      });
    } catch (e) {
      toast(`Couldn't save the shipping service: ${e.message}`, { kind: "error" });
    }
  };
  return (
    <div className="mt-1.5 flex items-center gap-1.5" title="Shipping service for this draft">
      <Truck size={14} className="shrink-0 text-ink-faint" aria-hidden />
      <Select
        aria-label="Shipping service"
        className="h-9 text-[13px]"
        value={value || accountDefault}
        onChange={(e) => save(e.target.value)}
      >
        {policies.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}{p.summary ? ` · ${p.summary}` : ""}
          </option>
        ))}
      </Select>
    </div>
  );
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
    listingsState, openListing, loadListings, deleteListing, bulkDeleteListings,
    metricsById, skippedDraftIds, toggleSkipDraft,
  } = useApp();
  const { confirm, toast } = useToast();
  const { selected, toggle, otherConnected, effectiveTargets } = usePublishTargets();

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
  // eBay refuses a listing that's missing title/price/weight/category, so the
  // per-card Publish button is disabled for those. Bulk publish holds the same
  // line: they're left selected and named, not fired off to fail one by one.
  const readyToPublish = selectedDrafts.filter(
    (d) => missingRequired(d.listing || {}).length === 0);
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
      return { published: !!res.published, res };
    } catch (e) {
      return { published: false, error: e.message };
    } finally {
      setPublishing((p) => ({ ...p, [item.id]: false }));
    }
  };

  const publishOne = async (item) => {
    const { published, res, error } = await publishItem(item);
    if (error) {
      toast(`Publish error: ${error}`, { kind: "error" });
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
      toast(res.message || "Publish blocked — open the draft to see what to fix.",
        { kind: "warning" });
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
      toast(`${notReady === 1 ? "That draft is" : `All ${notReady} selected drafts are`} missing required info — open them to fill in what eBay needs.`,
        { kind: "warning" });
      return;
    }
    if (!(await confirm({
      title: `Publish ${readyToPublish.length} draft${readyToPublish.length === 1 ? "" : "s"} live?`,
      message: `Each goes straight to ${targetNames}.`
        + (notReady
          ? ` ${notReady} of the ${selectedDrafts.length} selected ${notReady === 1 ? "is" : "are"} missing required info and will stay ${notReady === 1 ? "a draft" : "drafts"}.`
          : ""),
      confirmLabel: "Publish live",
    }))) return;
    let ok = 0, failed = 0;
    setBulkProgress({ done: 0, total: readyToPublish.length });
    try {
      for (const item of readyToPublish) {
        (await publishItem(item)).published ? ok++ : failed++;
        setBulkProgress((p) => ({ ...p, done: ok + failed }));
      }
    } finally {
      setBulkProgress(null);
    }
    await loadListings({ quiet: true });
    exitSelect();
    toast(`Published ${ok} listing${ok === 1 ? "" : "s"}.`
      + (failed ? ` ${failed} need attention — open them to fix.` : "")
      + (notReady ? ` ${notReady} skipped for missing info.` : ""),
      { kind: failed || notReady ? "warning" : "success" });
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
                ? "Every selected draft is missing required info — open them to finish"
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
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {drafts.map((item, i) => {
          const missing = missingRequired(item.listing || {});
          return (
            <motion.div
              key={item.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.22, delay: Math.min(i * 0.03, 0.3) }}
            >
              {/* Delete lives in the labeled row below, not as another tiny
                  icon in the card's corner cluster. */}
              <ListingCard item={item} onOpen={openListing}
                onStartOver={startOver}
                startingOver={startingOver === item.id}
                onSkip={() => toggleSkipDraft(item.id)}
                skipped={skippedDraftIds.has(item.id)}
                metrics={metricsById[item.id]}
                selectable={selecting}
                selected={!!sel[item.id]}
                onSelect={() => setSel((s) => ({ ...s, [item.id]: !s[item.id] }))} />
              {!selecting && (
                <>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <Button variant="secondary" size="sm" className="flex-1"
                      onClick={() => publishOne(item)}
                      loading={!!publishing[item.id]}
                      disabled={missing.length > 0}
                      title={missing.length
                        ? `Missing: ${missing.join(", ")} — open Review & List to finish`
                        : undefined}>
                      <Rocket aria-hidden /> Publish
                    </Button>
                    <Button variant="ghost" size="sm" className="flex-1"
                      onClick={() => openListing(item.id)}>
                      <PenLine aria-hidden /> Review &amp; List
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => askDelete(item)}
                      aria-label="Delete this draft" title="Delete this draft"
                      className="shrink-0 text-ink-faint hover:text-error">
                      <Trash2 aria-hidden />
                    </Button>
                  </div>
                  <DraftShipping item={item} />
                </>
              )}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}
