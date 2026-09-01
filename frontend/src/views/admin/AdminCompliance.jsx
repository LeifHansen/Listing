import { useState } from "react";
import { Trash2, PlayCircle, ShieldAlert } from "lucide-react";
import { postJson } from "@/lib/api";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toaster";
import { useAdminRead } from "@/views/admin/useAdminRead";

const NOTICE_TONES = { pending: "yellow", failed: "red" };

// The two obligation queues: eBay account-deletion notices accepted but not
// yet carried out, and photos a deleted account still owns in storage. Both
// are promises already made to somebody, so "Nothing owed" may only render
// when the read SUCCEEDED — an outage says so instead.
export function AdminCompliance() {
  const { toast } = useToast();
  const state = useAdminRead("/api/admin/compliance");
  const [running, setRunning] = useState(false);

  const runNow = async () => {
    setRunning(true);
    try {
      const r = await postJson("/api/admin/compliance/run");
      const d = r.deletions || {};
      toast(
        `Queue ran: ${d.notices ?? 0} notice${d.notices === 1 ? "" : "s"}, `
        + `${d.media ?? 0} photo purge${d.media === 1 ? "" : "s"}, `
        + `${r.refunds_settled ?? 0} refund${r.refunds_settled === 1 ? "" : "s"} settled.`,
        { kind: "success" });
      state.reload();
    } catch (e) {
      toast(e.message || "The queue run didn’t go through.", { kind: "error" });
    } finally {
      setRunning(false);
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
          We couldn’t read the compliance queues just now — this doesn’t mean
          nothing is owed. Try again in a moment.
        </p>
      </Card>
    );
  }

  const d = state.data;
  const notices = d.deletion_notices || [];
  const purges = d.media_purges || [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex justify-end">
        <Button variant="primary" size="sm" loading={running} onClick={runNow}>
          <PlayCircle size={15} aria-hidden /> Run the queues now
        </Button>
      </div>

      <Card>
        <SectionHeader
          icon={ShieldAlert}
          title={`eBay deletion notices · ${d.deletion_backlog}`}
          hint="Acknowledged account-deletion notices not yet carried out. eBay stops resending once acknowledged, so a number that doesn't come back down is the alert."
        />
        {notices.length === 0 ? (
          <p className="text-sm text-ink-secondary">Nothing owed.</p>
        ) : (
          <ul className="divide-y divide-line -mx-6 px-6">
            {notices.map((n) => (
              <li key={n.notification_id} className="flex items-center gap-3 py-2.5">
                <TagPill tone={NOTICE_TONES[n.state] || "neutral"}>{n.state}</TagPill>
                <span className="flex-1 min-w-0 font-mono text-xs text-ink-secondary truncate">
                  {n.notification_id}
                </span>
                <span className="text-xs text-ink-faint tabular-nums shrink-0">
                  {n.attempts} attempt{n.attempts === 1 ? "" : "s"}
                </span>
                {n.last_error && (
                  <span className="hidden sm:block text-xs text-error truncate max-w-56"
                    title={n.last_error}>
                    {n.last_error}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <SectionHeader
          icon={Trash2}
          title={`Photo purges owed · ${d.media_purge_backlog}`}
          hint="Photos whose account is already deleted, still in storage. Rows disappear as purges succeed; a row that keeps failing keeps its place because nothing else remembers these files exist."
        />
        {purges.length === 0 ? (
          <p className="text-sm text-ink-secondary">Nothing owed.</p>
        ) : (
          <ul className="divide-y divide-line -mx-6 px-6">
            {purges.map((p) => (
              <li key={p.listing_id} className="flex items-center gap-3 py-2.5">
                <span className="flex-1 min-w-0 font-mono text-xs text-ink-secondary truncate">
                  {p.listing_id}
                </span>
                <span className="text-xs text-ink-faint tabular-nums shrink-0">
                  {p.attempts} attempt{p.attempts === 1 ? "" : "s"}
                </span>
                {p.last_error && (
                  <span className="hidden sm:block text-xs text-error truncate max-w-56"
                    title={p.last_error}>
                    {p.last_error}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
