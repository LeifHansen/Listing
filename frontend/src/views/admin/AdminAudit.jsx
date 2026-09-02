import { useState } from "react";
import { ScrollText } from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toaster";
import { actionLabel, actionTone, relTime, mergeRows } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";

// The append-only trail every admin mutation writes before it runs. Rows
// are facts, not controls — there is deliberately nothing to click here.
export function AdminAudit() {
  const { toast } = useToast();
  const [extra, setExtra] = useState({ key: null, rows: [], cursor: undefined });

  const base = "/api/admin/audit";
  const state = useAdminRead(base);

  const more = extra.key === base ? extra : { rows: [], cursor: undefined };
  const rows = state.kind === "ready"
    ? mergeRows(state.data.entries || [], more.rows) : [];
  const cursor = more.cursor !== undefined ? more.cursor : state.data?.next_cursor;

  const loadMore = async () => {
    try {
      const r = await api(`${base}?before=${encodeURIComponent(cursor)}`);
      setExtra((p) => {
        const prev = p.key === base ? p : { rows: [] };
        return { key: base, rows: mergeRows(prev.rows, r.entries || []),
                 cursor: r.next_cursor };
      });
    } catch {
      toast("Couldn’t load more of the audit log just now.", { kind: "error" });
    }
  };

  if (state.kind === "loading") {
    return (
      <Card className="p-6 space-y-3">
        <Skeleton className="h-5 w-2/3" />
        <Skeleton className="h-5 w-1/2" />
      </Card>
    );
  }

  if (state.kind === "unavailable") {
    return (
      <Card>
        <p className="text-sm text-ink-secondary">
          We couldn’t read the audit log just now — this doesn’t mean it’s
          empty. Try again in a moment.
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {rows.length === 0 ? (
        <Card className="p-0">
          <EmptyState
            title="No admin actions yet"
            message="Every console action — grants, sign-outs, disables, queue runs — is written here before it happens."
          />
        </Card>
      ) : (
        <Card className="p-0 divide-y divide-line overflow-hidden">
          {rows.map((e) => {
            const detail = e.data && Object.keys(e.data).length > 0
              ? Object.entries(e.data).map(([k, v]) => `${k}: ${v}`).join(" · ")
              : "";
            return (
              <div key={e.id} className="flex items-center gap-3 px-4 py-2.5">
                <TagPill tone={actionTone(e.action)}>{actionLabel(e.action)}</TagPill>
                <span className="flex-1 min-w-0 text-[13px] text-ink-secondary truncate">
                  <span className="font-semibold text-ink">{e.actor_email || e.actor_id}</span>
                  {e.target_id && (
                    <span className="font-mono text-xs"> → {e.target_type} {e.target_id}</span>
                  )}
                  {detail && <span className="text-ink-faint"> · {detail}</span>}
                </span>
                {e.ip && (
                  <span className="hidden sm:block text-xs text-ink-faint font-mono shrink-0">
                    {e.ip}
                  </span>
                )}
                <span className="text-xs text-ink-faint shrink-0 tabular-nums">
                  {relTime(e.created_at)}
                </span>
              </div>
            );
          })}
        </Card>
      )}

      {cursor && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={loadMore}>
            <ScrollText size={15} aria-hidden /> Load older entries
          </Button>
        </div>
      )}
    </div>
  );
}
