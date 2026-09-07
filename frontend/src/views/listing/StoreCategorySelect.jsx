import { useEffect } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/store";
import { Field, Select } from "@/components/ui/fields";

/* Where this listing sits in the seller's OWN eBay Store.
 *
 * Not eBay's category — that says what the item is, and every listing needs
 * one. This is the shelf in the store's left-hand nav ("Vintage Tees",
 * "Beanie Babies"), invented by the seller, numbered per account, and only a
 * seller with a Store subscription has any at all. A listing without one is
 * filed at the store's top level, which is where all of them used to land.
 *
 * The draft arrives with a shelf already matched from its own words (see
 * backend/services/store_category), so this is usually a confirmation rather
 * than a decision — and always a correction the seller can make in one tap.
 *
 * Renders NOTHING when the account has no store or the list came back empty:
 * a dropdown with one option is a question with no answer. When the lookup
 * could not run at all, that is a different thing from an empty store and it
 * says so rather than hiding — the same distinction lib/priceLookup draws.
 */

export function useStoreCategories() {
  const { ebay, storeCategoriesData, setStoreCategoriesData } = useApp();
  useEffect(() => {
    if (!ebay.connected || storeCategoriesData) return;
    api("/api/ebay/store-categories")
      .then(setStoreCategoriesData)
      .catch(() => setStoreCategoriesData({ store: false, checked: false, categories: [] }));
  }, [ebay.connected, storeCategoriesData, setStoreCategoriesData]);
  return {
    connected: ebay.connected,
    categories: storeCategoriesData?.categories || [],
    hasStore: !!storeCategoriesData?.store,
    // undefined while the first request is still out — which is not the same
    // as "we asked and could not reach eBay".
    checked: storeCategoriesData ? storeCategoriesData.checked !== false : undefined,
  };
}

/* `value` is the listing's store_category_id ("" = the store's top level).
 * `onChange` receives (id, name) — the name rides along so the listing can
 * say where it is filed without the tree.
 *
 * Renders its own Field, label and all, because the whole row has to vanish
 * together: a label with nothing under it is how a seller without a store
 * would learn there is a setting here that they cannot have. */
export function StoreCategorySelect({ value, onChange, name = "", ...rest }) {
  const { connected, categories, checked } = useStoreCategories();
  if (!connected) return null;
  if (checked === false) {
    return (
      <p className="text-sm text-ink-secondary">
        We couldn’t read your eBay Store categories just now — this listing
        will go to the top level of your store.
      </p>
    );
  }
  if (!categories.length) return null;

  const current = value || "";
  // A shelf this account doesn't have: another eBay account's id, or one the
  // seller deleted on eBay since the draft was written. Never swallowed —
  // it gets its own option so the select shows what the listing holds.
  const orphaned = current && !categories.some((c) => String(c.id) === String(current));

  return (
    <Field
      label="Store category"
      hint="(your eBay Store)"
      help={"Where this listing sits in your own store's menu — separate from "
        + "the eBay category above, which is what decides the fields eBay "
        + "asks for. We match it from the draft; change it here, or leave it "
        + "at the top level of your store."}
    >
      <Select
        aria-label="Store category"
        value={current}
        onChange={(e) => {
          const id = e.target.value;
          const hit = categories.find((c) => String(c.id) === id);
          onChange(id, hit ? hit.name : "");
        }}
        {...rest}
      >
        <option value="">Top level of your store</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.path || c.name}
          </option>
        ))}
        {orphaned && (
          <option value={current}>
            {name ? `${name} — not in your store any more` : "Not in your store any more"}
          </option>
        )}
      </Select>
    </Field>
  );
}
