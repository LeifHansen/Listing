import { useState } from "react";
import {
  Users, Activity, Package, BadgeDollarSign, Sparkles, CreditCard,
  Trash2, Undo2, TrendingUp,
} from "lucide-react";
import { cn, formatMoney } from "@/lib/utils";
import { Card, SectionHeader } from "@/components/ui/Card";
import { StatCard } from "@/components/ui/StatCard";
import { Sparkline, MiniBars } from "@/components/ui/Sparkline";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { ADMIN_RANGES, tileValue, tileSub, salesSubline } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";

function RangePicker({ value, onChange }) {
  return (
    <select
      value={value}
      aria-label="Time window for the platform numbers"
      onChange={(e) => onChange(e.target.value)}
      className={cn(
        "appearance-none rounded-full border border-line bg-bg-sunken",
        "px-2.5 py-1 pr-6 text-[11px] font-bold text-ink-secondary cursor-pointer",
        "bg-[length:9px] bg-[right_8px_center] bg-no-repeat",
        "focus:outline-none focus:ring-2 focus:ring-blue/40",
      )}
      style={{
        backgroundImage:
          "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%2394a3b8' stroke-width='1.6' stroke-linecap='round'/%3E%3C/svg%3E\")",
      }}
    >
      {ADMIN_RANGES.map((r) => (
        <option key={r.id} value={r.id}>{r.label}</option>
      ))}
    </select>
  );
}

// A backlog count is red the moment it is nonzero: each unit is a promise
// already made to somebody (an erasure, their money). null means the count
// itself could not be taken, which is a dash, never a reassuring zero.
function backlogTone(n) {
  if (n == null) return "yellow";
  return n > 0 ? "red" : "green";
}

export function AdminOverview() {
  const [rangeId, setRangeId] = useState("30");
  const range = ADMIN_RANGES.find((r) => r.id === rangeId) || ADMIN_RANGES[1];
  const state = useAdminRead(`/api/admin/overview?days=${range.days}`);

  if (state.kind === "ready" && state.data?.available === false) {
    return (
      <Card>
        <EmptyState
          title="No database configured"
          message="Accounts, listings and billing all live in the database — set DATABASE_URL and the platform numbers appear here."
        />
      </Card>
    );
  }

  const sales = state.kind === "ready" ? state.data.sales : null;
  const soldValue = sales
    ? (sales.currency
      ? formatMoney(sales.value, sales.currency) || sales.count
      : sales.count)
    : null;
  const spend = state.kind === "ready"
    ? state.data.tokens?.by_kind?.spend : null;
  const purchases = state.kind === "ready"
    ? state.data.tokens?.by_kind?.purchase : null;
  const backlog = state.kind === "ready"
    ? state.data.deletion_backlog || {} : {};
  const refunds = state.kind === "ready" ? state.data.owed_refunds : null;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard
          icon={Users} label="Accounts" tone="blue"
          value={tileValue(state, (d) => d.users.total)}
          sub={tileSub(state, (d) => `${d.users.signups} joined in the last ${range.label}`)}
        />
        <StatCard
          icon={Activity} label="Active accounts" tone="green"
          value={tileValue(state, (d) => d.users.active)}
          sub={tileSub(state, `listed or spent in the last ${range.label}`)}
        />
        <StatCard
          icon={Package} label="Live listings" tone="blue"
          value={tileValue(state, (d) => (d.listings.by_status.live || 0)
            + (d.listings.by_status.published || 0))}
          sub={tileSub(state, (d) => `${d.listings.total} records in every state`)}
        />
        <StatCard
          icon={BadgeDollarSign} label="Sold" tone="green"
          value={state.kind === "ready" ? (soldValue ?? "—") : "—"}
          sub={tileSub(state, (d) => salesSubline(d.sales))}
          action={<RangePicker value={rangeId} onChange={setRangeId} />}
        />
        <StatCard
          icon={Sparkles} label="AI tokens spent" tone="yellow"
          value={tileValue(state, () => Math.abs(spend?.tokens || 0))}
          sub={tileSub(state, () => `${spend?.count || 0} charges in the last ${range.label}`)}
        />
        <StatCard
          icon={CreditCard} label="Tokens purchased" tone="green"
          value={tileValue(state, () => purchases?.tokens || 0)}
          sub={tileSub(state, () => `${purchases?.count || 0} purchases in the last ${range.label}`)}
        />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard
          icon={Trash2} label="Deletion notices owed"
          tone={state.kind === "ready" ? backlogTone(backlog.deletion_notices) : "yellow"}
          value={tileValue(state, () => backlog.deletion_notices)}
          sub={tileSub(state, "eBay account deletions not yet carried out")}
        />
        <StatCard
          icon={Trash2} label="Photo purges owed"
          tone={state.kind === "ready" ? backlogTone(backlog.media_purges) : "yellow"}
          value={tileValue(state, () => backlog.media_purges)}
          sub={tileSub(state, "photos of deleted accounts still in storage")}
        />
        <StatCard
          icon={Undo2} label="Refunds owed"
          tone={state.kind === "ready" ? backlogTone(refunds) : "yellow"}
          value={tileValue(state, () => refunds)}
          sub={tileSub(state, "token refunds not yet settled")}
        />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        <Card>
          <SectionHeader icon={TrendingUp} title="Signups"
            hint="One point per day across the selected window." />
          {state.kind === "loading" && <Skeleton className="h-9 w-full" />}
          {state.kind === "unavailable" && (
            <p className="text-sm text-ink-secondary">
              We couldn’t check just now — this doesn’t mean nobody joined.
            </p>
          )}
          {state.kind === "ready" && (
            <div className="text-blue">
              <Sparkline
                points={state.data.users.signup_series.map((p) => p.count)} />
            </div>
          )}
        </Card>
        <Card>
          <SectionHeader icon={Sparkles} title="AI feature usage"
            hint="Token spend by feature across the selected window." />
          {state.kind === "loading" && <Skeleton className="h-9 w-full" />}
          {state.kind === "unavailable" && (
            <p className="text-sm text-ink-secondary">
              We couldn’t check just now.
            </p>
          )}
          {state.kind === "ready" && (
            state.data.tokens.features.length === 0 ? (
              <p className="text-sm text-ink-secondary">
                No AI spend in the last {range.label}.
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="text-warning">
                  <MiniBars
                    values={state.data.tokens.features.map((f) => f.tokens)} />
                </div>
                <ul className="text-[13px] text-ink-secondary flex flex-col gap-1">
                  {state.data.tokens.features.slice(0, 8).map((f) => (
                    <li key={f.feature || "unknown"}
                      className="flex justify-between gap-3 tabular-nums">
                      <span className="truncate">{f.feature || "unknown"}</span>
                      <span>{f.tokens} tokens · {f.count}×</span>
                    </li>
                  ))}
                </ul>
              </div>
            )
          )}
        </Card>
      </div>
    </div>
  );
}
