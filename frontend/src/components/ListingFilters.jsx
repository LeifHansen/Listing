import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Bookmark, BookmarkPlus, SlidersHorizontal, Trash2, X,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { Field, InfoTip, Input, Toggle } from "@/components/ui/fields";
import { CONDITIONS, conditionLabel } from "@/lib/conditions";
import { FORMAT_CHIP_LABELS } from "@/lib/listingFormat";
import {
  AGE_CHOICES, FORMAT_CHOICES, MAX_SAVED_VIEWS, PHOTO_CHOICES, VIEW_NAME_MAX,
  activeFilterCount, clearFilter, filterChips, isEmptyFilters, matchingViewId,
} from "@/lib/listingFilters";
import { cn } from "@/lib/utils";

/* The filter bar over the listings grid, and the saved views beside it.
 *
 * Every decision this makes about WHICH listings survive lives in
 * lib/listingFilters — this file is the controls and nothing else, so the
 * grid, its count and its empty state cannot end up disagreeing about what
 * is showing.
 *
 * Three rows, each of which earns its space or isn't drawn:
 *
 *   1. The saved views, the Filters button (badged with how many dimensions
 *      are narrowing) and Save view.
 *   2. The panel, when it's open.
 *   3. The chips for what's on, with "Showing N of M" beside them.
 *
 * Row 3 is the one that has to exist. A filtered grid and an unfiltered one
 * look identical — fewer cards is also what an outage, a failed sync and a
 * deleted batch look like — so a cut list always says, on screen, that it was
 * cut and by what. The tab badges above deliberately keep counting the whole
 * tab: a tab badge is a claim about the tab, not about the filter, and moving
 * it would leave no number anywhere saying how big the store really is.
 */

/* One toggleable value in a multi-select row. `aria-pressed` rather than a
   checkbox because these behave as a set of switches with one label between
   them, and the pressed ones are the filter. */
function ChipToggle({ on, onClick, children, title }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      title={title}
      className={cn(
        "shrink-0 inline-flex items-center gap-1 h-8 px-3 rounded-full text-[13px]",
        "font-semibold cursor-pointer transition-colors duration-150 border",
        on
          ? "bg-blue text-on-accent border-blue"
          : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
      )}
    >
      {children}
    </button>
  );
}

function ChipRow({ label, help, children }) {
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <span className="text-[13px] font-semibold text-ink flex items-center gap-1.5">
        {label}
        {help && <InfoTip text={help} />}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
    </div>
  );
}

export function ListingFilters({ shown, total, hasListings = true }) {
  const {
    listingFilters: filters, setListingFilters, clearListingFilters,
    listingsTab, savedViews, saveListingView, deleteListingView,
    applyListingView, user,
  } = useApp();
  const { confirm } = useToast();

  const [open, setOpen] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const nameBox = useRef(null);

  const count = activeFilterCount(filters);
  const chips = filterChips(filters);
  const empty = isEmptyFilters(filters);
  // Which saved view, if any, the screen is showing right now — so its pill
  // reads as pressed rather than as something still to be applied.
  const showingId = useMemo(
    () => matchingViewId(savedViews.items, listingsTab, filters),
    [savedViews.items, listingsTab, filters]);

  // A filter cleared from under an open name box leaves nothing worth saving,
  // so the box goes with it. Derived rather than an effect that puts `naming`
  // back: there is one source of truth for "is there a question to name", and
  // a second copy of it in state is a second copy that can be wrong.
  const showNameBox = naming && !empty;

  // The name box is the only thing the Save press was for; land in it.
  useEffect(() => { if (showNameBox) nameBox.current?.focus(); }, [showNameBox]);

  const set = (patch) => setListingFilters((cur) => ({ ...cur, ...patch }));
  const toggleIn = (key, value) => setListingFilters((cur) => ({
    ...cur,
    [key]: cur[key].includes(value)
      ? cur[key].filter((v) => v !== value)
      : [...cur[key], value],
  }));

  const submitName = async (e) => {
    e.preventDefault();
    setSaving(true);
    // The store toasts both refusals and the success — this only has to put
    // the box away when the view actually landed.
    const res = await saveListingView(name);
    setSaving(false);
    if (res?.ok) {
      setNaming(false);
      setName("");
    }
  };

  const askDelete = async (view) => {
    if (await confirm({
      title: `Delete “${view.name}”?`,
      message: "The view is removed from every device. Your listings aren't "
        + "touched — a view is only a saved set of filters.",
      confirmLabel: "Delete view",
      danger: true,
    })) deleteListingView(view.id);
  };

  const views = savedViews.items;

  // Nothing here belongs to a signed-out visitor: the views are account data
  // and the grid below is the sign-in prompt. Nor to an empty store — there
  // is nothing to narrow — unless a filter or a saved view is already there,
  // in which case the controls are exactly what is needed to get back out.
  if (!user) return null;
  if (!hasListings && empty && !views.length) return null;

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={count ? "soft" : "ghost"}
          size="sm"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          title="Narrow the list below — format, condition, price, brand, category, photos or age."
        >
          <SlidersHorizontal aria-hidden /> Filters
          {count > 0 && (
            <span className={cn(
              "font-display tabular-nums text-[11px] font-bold rounded-full",
              "bg-blue text-on-accent px-1.5 min-w-5 h-5 grid place-items-center",
            )}>
              {count}
            </span>
          )}
        </Button>

        {/* Offered only once there is a question to name. Saving a bare tab
            would make a pill that does nothing when pressed. */}
        {!empty && !showNameBox && (
          <Button variant="ghost" size="sm" onClick={() => setNaming(true)}
            title="Keep these filters under a name — it's on every device you sign in from.">
            <BookmarkPlus aria-hidden /> Save view
          </Button>
        )}

        {showNameBox && (
          <form onSubmit={submitName} className="flex items-center gap-2">
            <Input
              ref={nameBox}
              value={name}
              maxLength={VIEW_NAME_MAX}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name this view"
              aria-label="Name this view"
              className="h-9 w-48 text-sm"
            />
            <Button type="submit" variant="primary" size="sm" loading={saving}>
              Save
            </Button>
            <Button variant="ghost" size="sm"
              onClick={() => { setNaming(false); setName(""); }}>
              Cancel
            </Button>
          </form>
        )}

        {/* The saved strip. Each pill applies its own tab AND its filters, so
            pressing one lands on the same list it was saved from. */}
        {views.map((v) => (
          <span key={v.id} className={cn(
            "inline-flex items-center rounded-full border transition-colors duration-150",
            v.id === showingId
              ? "bg-blue text-on-accent border-blue"
              : "bg-card border-line hover:border-line-strong",
          )}>
            <button
              type="button"
              onClick={() => applyListingView(v)}
              aria-pressed={v.id === showingId}
              title={`Show “${v.name}”`}
              className={cn(
                "inline-flex items-center gap-1.5 h-8 pl-3 pr-1.5 rounded-l-full",
                "text-[13px] font-semibold cursor-pointer max-w-52",
                v.id === showingId ? "text-on-accent" : "text-ink-secondary hover:text-ink",
              )}
            >
              <Bookmark size={13} aria-hidden className="shrink-0" />
              <span className="truncate">{v.name}</span>
            </button>
            <button
              type="button"
              onClick={() => askDelete(v)}
              aria-label={`Delete the view “${v.name}”`}
              title={`Delete “${v.name}”`}
              className={cn(
                "grid place-items-center size-8 pr-1 rounded-r-full cursor-pointer",
                v.id === showingId
                  ? "text-on-accent/70 hover:text-on-accent"
                  : "text-ink-faint hover:text-error",
              )}
            >
              <Trash2 size={13} aria-hidden />
            </button>
          </span>
        ))}

        {/* Said once, where the press would fail, rather than as a surprise
            after typing a name. */}
        {views.length >= MAX_SAVED_VIEWS && (
          <span className="text-xs text-ink-faint">
            {MAX_SAVED_VIEWS} views saved — delete one to make room.
          </span>
        )}
      </div>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="rounded-card border border-line bg-card p-4 flex flex-col gap-4">
              <ChipRow label="Format"
                help="How the listing sells. A listing saved before this app stored a format counts as Buy It Now, which is what it publishes as.">
                {FORMAT_CHOICES.map((f) => (
                  <ChipToggle key={f} on={filters.format.includes(f)}
                    onClick={() => toggleIn("format", f)}>
                    {FORMAT_CHIP_LABELS[f]}
                  </ChipToggle>
                ))}
              </ChipRow>

              <ChipRow label="Condition"
                help="eBay's own grades. Not every category offers every one — a listing is matched on the grade it carries.">
                {CONDITIONS.map((c) => (
                  <ChipToggle key={c} on={filters.condition.includes(c)}
                    onClick={() => toggleIn("condition", c)}>
                    {conditionLabel(c)}
                  </ChipToggle>
                ))}
              </ChipRow>

              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
                <Field label="Brand" hint="contains">
                  <Input value={filters.brand} placeholder="Nike"
                    className="h-10 text-sm"
                    onChange={(e) => set({ brand: e.target.value })} />
                </Field>
                <Field label="Category" hint="contains"
                  help="Matches eBay's category path and your own eBay Store shelf name.">
                  <Input value={filters.category} placeholder="Shirts"
                    className="h-10 text-sm"
                    onChange={(e) => set({ category: e.target.value })} />
                </Field>
                <Field label="Price from"
                  help="What a live listing ASKS, and what a sold one WENT FOR. An auction is measured at its starting bid, or its Buy It Now where it has one. Listings nobody has priced yet are left out of a range — an unpriced draft isn't 'under $20'.">
                  <Input value={filters.priceMin} inputMode="decimal" placeholder="0"
                    className="h-10 text-sm"
                    onChange={(e) => set({ priceMin: e.target.value })} />
                </Field>
                <Field label="Price to">
                  <Input value={filters.priceMax} inputMode="decimal" placeholder="Any"
                    className="h-10 text-sm"
                    onChange={(e) => set({ priceMax: e.target.value })} />
                </Field>
              </div>

              <ChipRow label="Photos">
                {PHOTO_CHOICES.map(([id, label]) => (
                  <ChipToggle key={id} on={filters.photos === id}
                    onClick={() => set({ photos: id })}>
                    {label}
                  </ChipToggle>
                ))}
              </ChipRow>

              <ChipRow label="Listed"
                help="When the listing was created here. A record that doesn't say when is left out of a window rather than assumed recent.">
                {AGE_CHOICES.map(([id, label]) => (
                  <ChipToggle key={id || "any"} on={filters.within === id}
                    onClick={() => set({ within: id })}>
                    {label}
                  </ChipToggle>
                ))}
              </ChipRow>

              <Toggle
                checked={filters.needsWork}
                onChange={(on) => set({ needsWork: on })}
                label="Only listings that still need work"
                help="Missing a price, a category or photos — the three blanks that stop a draft being published. Sold and ended listings are finished business and never match."
              />

              <div className="flex items-center justify-between gap-3 pt-1">
                <span className="text-xs text-ink-faint">
                  Filters apply to the listings loaded on this screen.
                </span>
                <Button variant="ghost" size="sm" onClick={clearListingFilters}
                  disabled={empty}>
                  Clear all
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-[12px]">
          {chips.map((c) => (
            <span key={c.key} className={cn(
              "inline-flex items-center gap-1 rounded-full bg-blue-soft text-blue",
              "pl-2.5 pr-1 py-0.5 font-display text-xs font-semibold max-w-64",
            )}>
              <span className="truncate">{c.label}</span>
              <button
                type="button"
                onClick={() => setListingFilters(clearFilter(filters, c.key))}
                aria-label={`Clear filter: ${c.label}`}
                className={cn(
                  "grid place-items-center size-4 rounded-full cursor-pointer",
                  "text-blue/70 hover:text-blue",
                )}
              >
                <X size={12} aria-hidden />
              </button>
            </span>
          ))}
          <button type="button" onClick={clearListingFilters}
            className="text-ink-faint hover:text-ink underline underline-offset-2 cursor-pointer">
            Clear all
          </button>
          {/* The count the tab badge above cannot give: that badge is the
              whole tab, this is what survived. Without it a filtered grid is
              indistinguishable from a store that lost something. */}
          {total != null && (
            <span className="text-ink-faint tabular-nums">
              · Showing {shown} of {total}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
