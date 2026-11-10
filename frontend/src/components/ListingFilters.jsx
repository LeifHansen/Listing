import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  Bookmark, BookmarkPlus, SlidersHorizontal, Trash2, X,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { Field, Input, Toggle } from "@/components/ui/fields";
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
 *   1. The Filters button (badged with how many dimensions are narrowing),
 *      Save view, the saved views — and, at the right end, whatever the
 *      caller passes as `trailing` (the Manage tab puts its where-it-lives
 *      control there, so every narrowing control is on one line).
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

function ChipRow({ label, children }) {
  return (
    <div className="flex flex-col gap-1.5 min-w-0">
      <span className="text-[13px] font-semibold text-ink">{label}</span>
      <div className="flex flex-wrap items-center gap-1.5">{children}</div>
    </div>
  );
}

export function ListingFilters({ shown, total, hasListings = true, trailing = null }) {
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
  if (!hasListings && empty && !views.length) return trailing
    ? <div className="flex justify-end">{trailing}</div> : null;

  // How many more views can be saved — said on the button that would fail,
  // as its tooltip, rather than as a sentence on the row.
  const full = views.length >= MAX_SAVED_VIEWS;

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={count ? "soft" : "ghost"}
          size="sm"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
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
            disabled={full}
            title={full ? `${MAX_SAVED_VIEWS} views saved — delete one to make room.` : undefined}>
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
            pressing one lands on the same list it was saved from. A neutral
            pressed state — blue is the lifecycle tab's — and the delete only
            on the pill that is showing: a row of bookmarks each with its own
            bin read as a row of things to be careful of. */}
        {views.map((v) => {
          const showing = v.id === showingId;
          return (
            <span key={v.id} className={cn(
              "inline-flex items-center rounded-full border transition-colors duration-150",
              showing
                ? "bg-bg-sunken border-line-strong"
                : "bg-card border-line hover:border-line-strong",
            )}>
              <button
                type="button"
                onClick={() => applyListingView(v)}
                aria-pressed={showing}
                title={`Show “${v.name}”`}
                className={cn(
                  "inline-flex items-center gap-1.5 h-8 pl-3 rounded-full",
                  "text-[13px] font-semibold cursor-pointer max-w-52",
                  showing ? "pr-1.5 rounded-r-none text-ink" : "pr-3 text-ink-secondary hover:text-ink",
                )}
              >
                <Bookmark size={13} aria-hidden className="shrink-0" />
                <span className="truncate">{v.name}</span>
              </button>
              {showing && (
                <button
                  type="button"
                  onClick={() => askDelete(v)}
                  aria-label={`Delete the view “${v.name}”`}
                  title={`Delete “${v.name}”`}
                  className="grid place-items-center size-8 pr-1 rounded-r-full cursor-pointer text-ink-faint hover:text-error"
                >
                  <Trash2 size={13} aria-hidden />
                </button>
              )}
            </span>
          );
        })}

        {trailing && <div className="ml-auto">{trailing}</div>}
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
              <ChipRow label="Format">
                {FORMAT_CHOICES.map((f) => (
                  <ChipToggle key={f} on={filters.format.includes(f)}
                    onClick={() => toggleIn("format", f)}>
                    {FORMAT_CHIP_LABELS[f]}
                  </ChipToggle>
                ))}
              </ChipRow>

              <ChipRow label="Condition">
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
                <Field label="Category" hint="contains">
                  <Input value={filters.category} placeholder="Shirts"
                    className="h-10 text-sm"
                    onChange={(e) => set({ category: e.target.value })} />
                </Field>
                <Field label="Price from"
                  help="A sold listing is measured at what it went for; an unpriced draft is left out of a range.">
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

              <ChipRow label="Listed">
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
                note="Missing a price, a category or photos."
              />

              <div className="flex justify-end pt-1">
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
