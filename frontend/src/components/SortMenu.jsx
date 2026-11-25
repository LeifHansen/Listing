import { ArrowUpDown } from "lucide-react";
import { useApp } from "@/store";
import { Menu } from "@/components/ui/Menu";
import { DEFAULT_SORT, SORTS, sortLabel } from "@/lib/listingSort";

/* The Manage grid's order (lib/listingSort). "Sort: Newest listed" from
 * small screens up, so the order on screen is always named; an icon on a
 * phone, where the row has room for one more control and no more. The
 * button is filled while anything other than the default is on — the same
 * "something is different from usual" signal the Filters button gives. */
export function SortMenu() {
  const { listingsSort, setListingsSort } = useApp();
  const name = sortLabel(listingsSort);
  return (
    <Menu
      label={`Sort: ${name}`}
      size="sm"
      variant={listingsSort === DEFAULT_SORT ? "ghost" : "soft"}
      items={SORTS.map((s) => ({
        key: s.id,
        label: s.label,
        hint: s.hint,
        checked: s.id === listingsSort,
        onSelect: () => setListingsSort(s.id),
      }))}
    >
      <ArrowUpDown aria-hidden />
      <span className="hidden sm:inline">Sort: {name}</span>
    </Menu>
  );
}
