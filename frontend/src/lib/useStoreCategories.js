import { useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { useApp } from "@/store";

/* The seller's own eBay Store shelves, fetched once per session and shared
 * through the app context (store.storeCategoriesData).
 *
 * Read by the editor's Store category picker and by the Manage tab's store
 * category filter and "Move to store category…" action — three readers of one
 * GET, so it lives here rather than in any one of them.
 *
 * `checked` is undefined while the first request is still out, false when it
 * could not reach eBay, and true otherwise: "we couldn't ask" is not "you have
 * no store", and the pickers say different things for each. */
export function useStoreCategories() {
  const { ebay, storeCategoriesData, setStoreCategoriesData } = useApp();
  // A failed read is not an answer to keep for the session -- the pickers
  // tell the seller to try again in a moment. Each reader that mounts onto
  // one asks once more (reopening the panel is the retry); one already
  // showing the failure does not ask again, which would be a loop.
  const asked = useRef(false);
  useEffect(() => {
    if (!ebay.connected) return;
    if (storeCategoriesData
        && (storeCategoriesData.checked !== false || asked.current)) return;
    asked.current = true;
    api("/api/ebay/store-categories")
      .then(setStoreCategoriesData)
      .catch(() => setStoreCategoriesData({ store: false, checked: false, categories: [] }));
  }, [ebay.connected, storeCategoriesData, setStoreCategoriesData]);
  return {
    connected: ebay.connected,
    store: !!storeCategoriesData?.store,
    categories: storeCategoriesData?.categories || [],
    checked: storeCategoriesData ? storeCategoriesData.checked !== false : undefined,
  };
}
