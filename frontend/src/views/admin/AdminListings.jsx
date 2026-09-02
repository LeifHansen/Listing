import { useEffect, useState } from "react";
import { Search, Package } from "lucide-react";
import { api } from "@/lib/api";
import { cn, formatMoney } from "@/lib/utils";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/fields";
import { StatusBadge } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toaster";
import { relTime, mergeRows } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";

const STATUSES = ["", "draft", "live", "published", "sold", "ended", "unlisted"];

// Read-only, whole-platform browse. Rows are summaries (the API never ships
// the full listing blobs in a list); opening one fetches the full record.
export function AdminListings() {
  const { toast } = useToast();
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState(null);
  const [extra, setExtra] = useState({ key: null, rows: [], cursor: undefined });

  useEffect(() => {
    const t = setTimeout(() => setQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  const base = `/api/admin/listings?q=${encodeURIComponent(q)}&status=${encodeURIComponent(status)}`;
  const state = useAdminRead(base);

  const more = extra.key === base ? extra : { rows: [], cursor: undefined };
  const rows = state.kind === "ready"
    ? mergeRows(state.data.listings || [], more.rows) : [];
  const cursor = more.cursor !== undefined ? more.cursor : state.data?.next_cursor;

  const loadMore = async () => {
    try {
      const r = await api(`${base}&before=${encodeURIComponent(cursor)}`);
      setExtra((p) => {
        const prev = p.key === base ? p : { rows: [] };
        return { key: base, rows: mergeRows(prev.rows, r.listings || []),
                 cursor: r.next_cursor };
      });
    } catch {
      toast("Couldn’t load more listings just now.", { kind: "error" });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative max-w-md flex-1 min-w-52">
          <Search size={16} aria-hidden
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Search by title or listing id"
            aria-label="Search listings"
            className="pl-10"
          />
        </div>
        <div className="flex items-center gap-1.5 overflow-x-auto">
          {STATUSES.map((s) => (
            <button
              key={s || "all"}
              type="button"
              aria-pressed={status === s}
              onClick={() => setStatus(s)}
              className={cn(
                "shrink-0 inline-flex items-center h-8 px-3 rounded-full text-[12px]",
                "font-semibold cursor-pointer transition-colors duration-150 border",
                status === s
                  ? "bg-blue text-on-accent border-blue"
                  : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
              )}
            >
              {s || "All"}
            </button>
          ))}
        </div>
      </div>

      {state.kind === "loading" && (
        <Card className="p-6 space-y-3">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-5 w-1/2" />
        </Card>
      )}

      {state.kind === "unavailable" && (
        <Card>
          <p className="text-sm text-ink-secondary">
            We couldn’t load the listings just now — this doesn’t mean there
            aren’t any. Try again in a moment.
          </p>
        </Card>
      )}

      {state.kind === "ready" && rows.length === 0 && (
        <Card className="p-0">
          <EmptyState
            title="No listings found"
            message={q || status ? "Nothing matches those filters."
              : "Listings appear here as sellers create them."}
          />
        </Card>
      )}

      {state.kind === "ready" && rows.length > 0 && (
        <Card className="p-0 divide-y divide-line overflow-hidden">
          {rows.map((l) => (
            <button
              key={l.id}
              type="button"
              onClick={() => setSelected(l.id)}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-bg-sunken transition-colors duration-150 cursor-pointer"
            >
              <span className="flex-1 min-w-0">
                <span className="block truncate font-semibold text-ink text-sm">
                  {l.title || l.id}
                </span>
                <span className="block text-xs text-ink-faint truncate font-mono">
                  {l.id}{l.user_id ? ` · ${l.user_id}` : " · anonymous"}
                </span>
              </span>
              <StatusBadge status={l.status} className="shrink-0" />
              <span className="text-[13px] font-semibold text-ink-secondary tabular-nums shrink-0 w-20 text-right">
                {formatMoney(l.sold_price ?? l.price, l.currency || "USD") || "—"}
              </span>
              <span className="text-xs text-ink-faint shrink-0 tabular-nums">
                {relTime(l.updated_at)}
              </span>
            </button>
          ))}
        </Card>
      )}

      {state.kind === "ready" && cursor && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={loadMore}>
            <Package size={15} aria-hidden /> Load older listings
          </Button>
        </div>
      )}

      {selected && (
        <AdminListingDialog listingId={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function AdminListingDialog({ listingId, onClose }) {
  const state = useAdminRead(`/api/admin/listings/${encodeURIComponent(listingId)}`);
  const rec = state.kind === "ready" ? state.data : null;
  const listing = rec?.listing || {};

  return (
    <Dialog open onClose={onClose} title={rec?.title || listingId} wide>
      {state.kind === "loading" && (
        <div className="space-y-3">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-40 w-full" />
        </div>
      )}
      {state.kind === "unavailable" && (
        <p className="text-sm text-ink-secondary">
          We couldn’t load this listing just now. Try again in a moment.
        </p>
      )}
      {rec && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink-secondary">
            <StatusBadge status={rec.status} />
            <span className="font-mono text-xs">{rec.id}</span>
            <span>owner: <span className="font-mono text-xs">{rec.user_id || "anonymous"}</span></span>
            {listing.ebay_listing_id && <span>eBay #{listing.ebay_listing_id}</span>}
            <span>updated {relTime(rec.updated_at)}</span>
          </div>
          {/* Read-only by design: the console can look, only the seller can
              touch. The raw document is what an operator debugging a sync
              actually needs. */}
          <pre className="rounded-tile bg-bg-sunken border border-line p-3 text-xs leading-relaxed overflow-auto max-h-96">
            {JSON.stringify(listing, null, 2)}
          </pre>
        </div>
      )}
    </Dialog>
  );
}
