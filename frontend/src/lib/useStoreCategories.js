import { useEffect } from "react";
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
  useEffect(() => {
    if (!ebay.connected || storeCategoriesData) return;
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
