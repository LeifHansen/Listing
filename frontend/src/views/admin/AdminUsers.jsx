import { useEffect, useState } from "react";
import { Search, Users } from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toaster";
import { relTime, mergeRows } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";
import { AdminUserDialog } from "@/views/admin/AdminUserDialog";

function initials(u) {
  return (u.display_name || u.email || "??").slice(0, 2);
}

export function AdminUsers() {
  const { toast } = useToast();
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState(null);
  // Pages past the first, keyed by the query they belong to so a new search
  // never inherits the old search's rows.
  const [extra, setExtra] = useState({ key: null, rows: [], rollups: {}, cursor: undefined });

  useEffect(() => {
    const t = setTimeout(() => setQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  const base = `/api/admin/users?q=${encodeURIComponent(q)}`;
  const state = useAdminRead(base);

  const more = extra.key === base ? extra : { rows: [], rollups: {}, cursor: undefined };
  const rows = state.kind === "ready"
    ? mergeRows(state.data.users || [], more.rows) : [];
  const rollups = state.kind === "ready"
    ? { ...(state.data.rollups || {}), ...more.rollups } : {};
  const cursor = more.cursor !== undefined ? more.cursor : state.data?.next_cursor;
  const total = state.kind === "ready" ? state.data.total : null;

  const loadMore = async () => {
    try {
      const r = await api(`${base}&before=${encodeURIComponent(cursor)}`);
      setExtra((p) => {
        const prev = p.key === base ? p : { rows: [], rollups: {} };
        return {
          key: base,
          rows: mergeRows(prev.rows, r.users || []),
          rollups: { ...prev.rollups, ...(r.rollups || {}) },
          cursor: r.next_cursor,
        };
      });
    } catch {
      toast("Couldn’t load more accounts just now.", { kind: "error" });
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="relative max-w-md">
        <Search size={16} aria-hidden
          className="absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none" />
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Search by email, name, or account id"
          aria-label="Search accounts"
          className="pl-10"
        />
      </div>

      {state.kind === "ready" && total != null && (
        <p className="text-[13px] text-ink-faint -mt-1">
          {total} account{total === 1 ? "" : "s"} on the platform
        </p>
      )}

      {state.kind === "loading" && (
        <Card className="p-6 space-y-3">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-5 w-1/2" />
          <Skeleton className="h-5 w-3/5" />
        </Card>
      )}

      {state.kind === "unavailable" && (
        <Card>
          <p className="text-sm text-ink-secondary">
            We couldn’t load the accounts just now — this doesn’t mean there
            aren’t any. Try again in a moment.
          </p>
        </Card>
      )}

      {state.kind === "ready" && rows.length === 0 && (
        <Card className="p-0">
          <EmptyState
            title={q ? "No matching accounts" : "No accounts yet"}
            message={q ? "Nothing matches that search." :
              "Accounts appear here as people sign up."}
          />
        </Card>
      )}

      {state.kind === "ready" && rows.length > 0 && (
        <Card className="p-0 divide-y divide-line overflow-hidden">
          {rows.map((u) => {
            const roll = rollups[u.id] || {};
            const facts = [
              `${roll.listings ?? 0} listing${roll.listings === 1 ? "" : "s"}`,
              roll.tokens ? `${roll.tokens.purchased} tokens` : null,
              roll.connections?.length
                ? roll.connections.map((c) => c.marketplace).join(" · ")
                : null,
            ].filter(Boolean).join(" · ");
            return (
              <button
                key={u.id}
                type="button"
                onClick={() => setSelected(u.id)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-bg-sunken transition-colors duration-150 cursor-pointer"
              >
                <span className="grid place-items-center size-9 rounded-full bg-blue-soft text-blue font-display font-bold text-xs uppercase shrink-0">
                  {initials(u)}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="truncate font-semibold text-ink text-sm">{u.email}</span>
                    {u.role === "superadmin" && <TagPill tone="red">superadmin</TagPill>}
                    {u.disabled_at && <TagPill tone="red">disabled</TagPill>}
                  </span>
                  <span className="block text-xs text-ink-faint truncate">{facts}</span>
                </span>
                <span className="text-xs text-ink-faint shrink-0 tabular-nums">
                  joined {relTime(u.created_at)}
                </span>
              </button>
            );
          })}
        </Card>
      )}

      {state.kind === "ready" && cursor && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={loadMore}>
            <Users size={15} aria-hidden /> Load more accounts
          </Button>
        </div>
      )}

      {selected && (
        <AdminUserDialog
          userId={selected}
          onClose={() => setSelected(null)}
          onChanged={state.reload}
        />
      )}
    </div>
  );
}
