import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  BookmarkPlus, ChevronDown, SlidersHorizontal, X,
} from "lucide-react";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import { CategoryChecklist } from "@/components/CategoryChecklist";
import { SortMenu } from "@/components/SortMenu";
import { Field, Input, Toggle } from "@/components/ui/fields";
import { CONDITIONS, conditionLabel } from "@/lib/conditions";
import { FORMAT_CHIP_LABELS } from "@/lib/listingFormat";
import {
  AGE_CHOICES, FORMAT_CHOICES, INTEREST_CHOICES, MAX_SAVED_VIEWS, PHOTO_CHOICES,
  VIEW_NAME_MAX, activeFilterCount, clearFilter, filterChips, isEmptyFilters,
} from "@/lib/listingFilters";
import { optionNamer } from "@/lib/categoryOptions";
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
 *      the two category quick-pickers (from small screens up — on a phone
 *      they live in the panel), the Sort menu, and at the right end whatever
 *      the caller passes as `trailing` (the Manage tab puts its
 *      where-it-lives control there, so every control that shapes the list
 *      is on one line). The row WRAPS rather than scrolling sideways: a
 *      popover opened from a scrolling row is clipped by it.
 *   2. The panel, when it's open.
 *   3. The chips for what's on, "Save view", and "Showing N of M".
 *
 * The saved views themselves sit with the lifecycle tabs (SavedViewTabs):
 * a view is a list to pick, which is what that row is for.
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

/* "Store category ▾" with a count of what is ticked, opening the checklist.
   From small screens up only: on a phone the same list is a section of the
   Filters panel, where it has the width to be read. */
function CategoryPicker({ label, options, selected, onToggle, onClear }) {
  const n = selected.length;
  return (
    <Popover
      label={label}
      className="hidden sm:inline-flex"
      buttonProps={{ variant: n ? "soft" : "ghost", size: "sm" }}
      trigger={(
        <>
          {label}
          {n > 0 && (
            <span className="font-display tabular-nums text-[11px] font-bold rounded-full bg-blue text-on-accent px-1.5 min-w-5 h-5 grid place-items-center">
              {n}
            </span>
          )}
          <ChevronDown aria-hidden />
        </>
      )}
    >
      <CategoryChecklist label={label} options={options} selected={selected}
        onToggle={onToggle} onClear={onClear} />
    </Popover>
  );
}

/* `storeOptions` / `ebayOptions` are the category choices for the list the
   filters sit over (lib/categoryOptions), counted before the filters so the
   menus don't empty as things are ticked. `showStore` is false for a seller
   with no Store shelves and no listing on one — a picker with only "No store
   category" in it is a question with one answer. */
export function ListingFilters({
  shown, total, hasListings = true, trailing = null,
  storeOptions = [], ebayOptions = [], showStore = false,
}) {
  const {
    listingFilters: filters, setListingFilters, clearListingFilters,
    savedViews, saveListingView, user,
  } = useApp();

  const [open, setOpen] = useState(false);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const nameBox = useRef(null);

  const count = activeFilterCount(filters);
  const names = useMemo(() => ({
    storeCategory: optionNamer(storeOptions),
    ebayCategory: optionNamer(ebayOptions),
  }), [storeOptions, ebayOptions]);
  const chips = filterChips(filters, names);
  const empty = isEmptyFilters(filters);

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

  const views = savedViews.items;

  // Nothing here belongs to a signed-out visitor: the views are account data
  // and the grid below is the sign-in prompt. Nor to an empty store — there
  // is nothing to narrow or order — unless a filter is already on, in which
  // case the controls are exactly what is needed to get back out.
  if (!user) return null;
  if (!hasListings && empty) return trailing
    ? <div className="flex justify-end">{trailing}</div> : null;

  // How many more views can be saved — said on the button that would fail,
  // as its tooltip, rather than as a sentence on the row.
  const full = views.length >= MAX_SAVED_VIEWS;
  const hasEbayCategories = ebayOptions.length > 1;

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

        {showStore && (
          <CategoryPicker label="Store category" options={storeOptions}
            selected={filters.storeCategory}
            onToggle={(id) => toggleIn("storeCategory", id)}
            onClear={() => set({ storeCategory: [] })} />
        )}
        {hasEbayCategories && (
          <CategoryPicker label="eBay category" options={ebayOptions}
            selected={filters.ebayCategory}
            onToggle={(id) => toggleIn("ebayCategory", id)}
            onClear={() => set({ ebayCategory: [] })} />
        )}

        <div className="ml-auto flex items-center gap-2">
          <SortMenu />
          {trailing}
        </div>
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
              {(showStore || hasEbayCategories) && (
                <div className="grid sm:grid-cols-2 gap-4">
                  {showStore && (
                    <ChipRow label="Store category">
                      <div className="w-full">
                        <CategoryChecklist label="Store category" options={storeOptions}
                          selected={filters.storeCategory} limit={12}
                          onToggle={(id) => toggleIn("storeCategory", id)}
                          onClear={() => set({ storeCategory: [] })} />
                      </div>
                    </ChipRow>
                  )}
                  {hasEbayCategories && (
                    <ChipRow label="eBay category">
                      <div className="w-full">
                        <CategoryChecklist label="eBay category" options={ebayOptions}
                          selected={filters.ebayCategory} limit={12}
                          onToggle={(id) => toggleIn("ebayCategory", id)}
                          onClear={() => set({ ebayCategory: [] })} />
                      </div>
                    </ChipRow>
                  )}
                </div>
              )}

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
                <Field label="Category name" hint="contains"
                  help="Matches the words in either category — eBay's or your Store's.">
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

              <ChipRow label="Buyer interest">
                {INTEREST_CHOICES.map(([id, label]) => (
                  <ChipToggle key={id || "any"} on={filters.interest === id}
                    onClick={() => set({ interest: id })}
                    title={id === "watchers"
                      ? "Listings eBay says somebody is watching — the ones a Send offers reaches."
                      : undefined}>
                    {label}
                  </ChipToggle>
                ))}
              </ChipRow>

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

          {/* Offered only once there is a question to name — here, beside the
              chips that ARE the question. Saving a bare tab would make a pill
              that does nothing when pressed. */}
          {!showNameBox && (
            <Button variant="ghost" size="sm" className="ml-auto h-7"
              onClick={() => setNaming(true)}
              disabled={full}
              title={full ? `${MAX_SAVED_VIEWS} views saved — delete one to make room.` : undefined}>
              <BookmarkPlus aria-hidden /> Save view
            </Button>
          )}
        </div>
      )}

      {showNameBox && (
        <form onSubmit={submitName} className="flex flex-wrap items-center gap-2">
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
    </div>
  );
}
