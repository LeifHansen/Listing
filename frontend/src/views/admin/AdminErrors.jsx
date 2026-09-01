import { useState } from "react";
import { Bug, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/Toaster";
import { relTime, mergeRows } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";

// What is actually broken in production, newest-seen first.
//
// One row per DISTINCT failure with a count, not one per occurrence — so this
// stays readable during an incident, when the alternative is ten thousand
// copies of the same line. Rows are facts; the only control is the disclosure
// triangle, because a traceback is worth reading and worth not showing by
// default.
const SEVERITY_TONE = { high: "red", medium: "yellow", low: "neutral" };

function count(n) {
  const value = Number(n) || 0;
  return value >= 1000 ? `${Math.round(value / 100) / 10}k` : String(value);
}

// The sink's own health. A queue that is dropping rows would otherwise look
// exactly like a quiet day, which is the most dangerous thing a monitor can
// do — the same lesson .github/scripts/check_health.py records.
function SinkNote({ sink }) {
  if (!sink || !sink.dropped) return null;
  return (
    <Card className="p-4">
      <p className="text-sm text-warning">
        {sink.dropped} report{sink.dropped === 1 ? "" : "s"} were dropped
        because the queue was full — this list is incomplete. It usually means
        something is failing far faster than it can be written down.
      </p>
    </Card>
  );
}

function ErrorRow({ row }) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;
  const where = [row.module, row.func].filter(Boolean).join(".");

  return (
    <div className="px-4 py-2.5">
      <div className="flex items-center gap-3">
        <TagPill tone={SEVERITY_TONE[row.severity] || "neutral"}>
          {row.severity}
        </TagPill>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex-1 min-w-0 flex items-center gap-1.5 text-left"
          aria-expanded={open}
        >
          <Chevron size={14} className="shrink-0 text-ink-faint" aria-hidden />
          <span className="min-w-0 truncate text-[13px] text-ink-secondary">
            <span className="font-semibold text-ink">
              {row.exc_type || row.level}
            </span>{" "}
            {row.message}
          </span>
        </button>
        {row.kind === "frontend" && (
          <TagPill tone="blue" className="hidden sm:inline-flex">browser</TagPill>
        )}
        <span className="shrink-0 text-xs text-ink-faint tabular-nums"
              title={`${row.count} occurrence(s)`}>
          ×{count(row.count)}
        </span>
        <span className="shrink-0 text-xs text-ink-faint tabular-nums">
          {relTime(row.last_seen)}
        </span>
      </div>

      {open && (
        <div className="mt-2 ml-[4.5rem] space-y-2 text-xs text-ink-faint">
          <p className="font-mono break-all">
            {[where, row.route, row.method].filter(Boolean).join("  ·  ")}
            {row.lineno ? `  ·  line ${row.lineno}` : ""}
          </p>
          <p>
            First seen {relTime(row.first_seen)}
            {row.build && `  ·  build ${row.build}`}
            {row.reference && `  ·  ref ${row.reference}`}
            {row.fix_pr && `  ·  fix ${row.fix_pr}`}
          </p>
          {row.traceback && (
            <pre className="max-h-64 overflow-auto rounded-lg bg-bg-sunken p-3
                            font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
              {row.traceback}
            </pre>
          )}
          {row.data && row.data.component_stack && (
            <pre className="max-h-40 overflow-auto rounded-lg bg-bg-sunken p-3
                            font-mono text-[11px] whitespace-pre-wrap">
              {row.data.component_stack}
            </pre>
          )}
          <p className="font-mono opacity-60">{row.fingerprint}</p>
        </div>
      )}
    </div>
  );
}

export function AdminErrors() {
  const { toast } = useToast();
  const [extra, setExtra] = useState({ key: null, rows: [], cursor: undefined });

  const base = "/api/admin/errors";
  const state = useAdminRead(base);

  const more = extra.key === base ? extra : { rows: [], cursor: undefined };
  const rows = state.kind === "ready"
    ? mergeRows(state.data.errors || [], more.rows) : [];
  const cursor = more.cursor !== undefined ? more.cursor : state.data?.next_cursor;

  const loadMore = async () => {
    try {
      const r = await api(`${base}?before=${encodeURIComponent(cursor)}`);
      setExtra((p) => {
        const prev = p.key === base ? p : { rows: [] };
        return { key: base, rows: mergeRows(prev.rows, r.errors || []),
                 cursor: r.next_cursor };
      });
    } catch {
      toast("Couldn’t load more of the error log just now.", { kind: "error" });
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

  // Never an empty list: "we couldn't read it" and "nothing is broken" are
  // opposite facts, and rendering the second for the first is how a console
  // reports that production is fine while it is on fire.
  if (state.kind === "unavailable") {
    return (
      <Card>
        <p className="text-sm text-ink-secondary">
          We couldn’t read the error log just now — this doesn’t mean there are
          none. Try again in a moment.
        </p>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <SinkNote sink={state.data.sink} />

      {rows.length === 0 ? (
        <Card className="p-0">
          <EmptyState
            title="Nothing has gone wrong"
            message="Failures are recorded here as they happen — one entry per distinct problem, with a count of how often it has hit."
          />
        </Card>
      ) : (
        <Card className="p-0 divide-y divide-line overflow-hidden">
          {rows.map((row) => <ErrorRow key={row.id} row={row} />)}
        </Card>
      )}

      {cursor && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={loadMore}>
            <Bug size={15} aria-hidden /> Load older problems
          </Button>
        </div>
      )}
    </div>
  );
}
