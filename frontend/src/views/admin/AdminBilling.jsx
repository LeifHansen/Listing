import { useState } from "react";
import { ReceiptText } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toaster";
import { relTime, signedTokens, mergeRows } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";

const KINDS = [
  { id: "", label: "All" },
  { id: "purchase", label: "Purchases" },
  { id: "grant", label: "Grants" },
  { id: "spend", label: "Spends" },
  { id: "refund", label: "Refunds" },
  { id: "reversal", label: "Reversals" },
];

const KIND_TONES = {
  purchase: "green", grant: "green", spend: "blue",
  refund: "yellow", reversal: "red",
};

// The global token ledger — every movement on the platform, newest first,
// with the user id and the idempotency ref the per-user history deliberately
// omits (reconciling a Stripe dispute needs both).
export function AdminBilling() {
  const { toast } = useToast();
  const [kind, setKind] = useState("");
  const [extra, setExtra] = useState({ key: null, rows: [], cursor: undefined });

  const base = `/api/admin/ledger?kind=${encodeURIComponent(kind)}`;
  const state = useAdminRead(base);

  const more = extra.key === base ? extra : { rows: [], cursor: undefined };
  const rows = state.kind === "ready"
    ? mergeRows(state.data.entries || [], more.rows) : [];
  const cursor = more.cursor !== undefined ? more.cursor : state.data?.next_cursor;

  const loadMore = async () => {
    try {
      const r = await api(`${base}&before=${encodeURIComponent(cursor)}`);
      setExtra((p) => {
        const prev = p.key === base ? p : { rows: [] };
        return { key: base, rows: mergeRows(prev.rows, r.entries || []),
                 cursor: r.next_cursor };
      });
    } catch {
      toast("Couldn’t load more of the ledger just now.", { kind: "error" });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 overflow-x-auto pb-1">
        {KINDS.map((k) => (
          <button
            key={k.id}
            type="button"
            aria-pressed={kind === k.id}
            onClick={() => setKind(k.id)}
            className={cn(
              "shrink-0 inline-flex items-center h-8 px-3 rounded-full text-[12px]",
              "font-semibold cursor-pointer transition-colors duration-150 border",
              kind === k.id
                ? "bg-blue text-on-accent border-blue"
                : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
            )}
          >
            {k.label}
          </button>
        ))}
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
            We couldn’t read the ledger just now — this doesn’t mean it’s
            empty. Try again in a moment.
          </p>
        </Card>
      )}

      {state.kind === "ready" && rows.length === 0 && (
        <Card className="p-0">
          <EmptyState
            title="Nothing in the ledger"
            message={kind ? "No entries of that kind yet."
              : "Token movements appear here as accounts spend and buy."}
          />
        </Card>
      )}

      {state.kind === "ready" && rows.length > 0 && (
        <Card className="p-0 divide-y divide-line overflow-hidden">
          {rows.map((e) => (
            <div key={e.id} className="flex items-center gap-3 px-4 py-2.5">
              <TagPill tone={KIND_TONES[e.kind] || "neutral"}>{e.kind}</TagPill>
              <span className="font-display font-bold text-sm tabular-nums w-14 shrink-0 text-right">
                {signedTokens(e.tokens)}
              </span>
              <span className="flex-1 min-w-0 text-[13px] text-ink-secondary truncate">
                {e.feature || e.note || "—"}
              </span>
              <span className="hidden sm:block text-xs text-ink-faint font-mono truncate max-w-40"
                title={e.ref || undefined}>
                {e.user_id}{e.ref ? ` · ${e.ref}` : ""}
              </span>
              <span className="text-xs text-ink-faint shrink-0 tabular-nums">
                {relTime(e.created_at)}
              </span>
            </div>
          ))}
        </Card>
      )}

      {state.kind === "ready" && cursor && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={loadMore}>
            <ReceiptText size={15} aria-hidden /> Load older entries
          </Button>
        </div>
      )}
    </div>
  );
}
