import { CheckCircle2, XCircle, ServerCog, AlertTriangle, Database } from "lucide-react";
import { Card, SectionHeader } from "@/components/ui/Card";
import { TagPill } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { useAdminRead } from "@/views/admin/useAdminRead";

// Yes/no chip for one capability. `missing` names exactly which env vars a
// "no" is about — four credentials once sat deployed for a week while a bare
// `false` hid that two more were expected.
function Ready({ label, ok, missing }) {
  return (
    <li className="flex items-center gap-2 py-1.5">
      {ok ? (
        <CheckCircle2 size={15} className="text-green shrink-0" aria-hidden />
      ) : (
        <XCircle size={15} className="text-error shrink-0" aria-hidden />
      )}
      <span className="text-[13px] font-semibold text-ink">{label}</span>
      {!ok && missing?.length > 0 && (
        <span className="text-xs text-ink-faint truncate" title={missing.join(", ")}>
          missing: {missing.join(", ")}
        </span>
      )}
    </li>
  );
}

// _diagnostics(), rendered. The same payload the curl-only
// /api/admin/diagnostics endpoint serves — this tab is its face.
export function AdminSystem() {
  const state = useAdminRead("/api/admin/system");

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
          We couldn’t load the system diagnostics just now. Try again in a
          moment — or use <code>GET /api/admin/diagnostics</code> with the
          admin token, which works even while the database is down.
        </p>
      </Card>
    );
  }

  const d = state.data;
  const db = d.db || {};

  return (
    <div className="flex flex-col gap-4">
      {(d.config_warnings || []).length > 0 && (
        <Card className="border-warning/30 bg-warning-soft">
          <SectionHeader icon={AlertTriangle} title="Configuration warnings"
            hint="Misconfigurations that look exactly like 'not configured yet' — a secret under a near-miss name, or a flag set to something that isn't on." />
          <ul className="text-sm text-ink flex flex-col gap-1.5">
            {d.config_warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </Card>
      )}

      <Card>
        <SectionHeader icon={Database} title="Deployment" />
        <div className="flex flex-wrap gap-2">
          <TagPill tone="blue">build {d.build}</TagPill>
          <TagPill tone={db.connected ? "green" : db.configured ? "red" : "neutral"}>
            database {db.configured ? (db.connected ? "connected" : "unreachable") : "not configured"}
          </TagPill>
          <TagPill tone={d.storage === "r2" ? "green" : "yellow"}>
            photos on {d.storage === "r2" ? `R2 · ${d.objstore_bucket}` : "local disk"}
          </TagPill>
          <TagPill tone={d.disk_free_mb > 400 ? "green" : "red"}>
            {d.disk_free_mb} MB disk free
          </TagPill>
          {d.tokens_enabled ? (
            <TagPill tone={d.stripe_live_mode ? "green" : "yellow"}>
              billing on · Stripe {d.stripe_live_mode ? "live" : "test"} mode
            </TagPill>
          ) : (
            <TagPill>billing off</TagPill>
          )}
          <TagPill tone="neutral">eBay env: {d.ebay_env}</TagPill>
        </div>
        {d.objstore_error && (
          <p className="mt-3 text-xs text-error">R2: {d.objstore_error}</p>
        )}
      </Card>

      <Card>
        <SectionHeader icon={ServerCog} title="Integrations" />
        <ul className="grid sm:grid-cols-2 gap-x-6">
          <Ready label="Anthropic (AI drafts)" ok={d.anthropic_configured} />
          <Ready label="eBay API" ok={d.ebay_configured} missing={d.ebay_missing} />
          <Ready label="eBay OAuth" ok={d.ebay_oauth_ready} />
          <Ready label="eBay deletion endpoint" ok={d.ebay_deletion_endpoint_ready} />
          <Ready label="Taxonomy lookups" ok={d.taxonomy_configured} />
          <Ready label="Object storage (R2)" ok={d.objstore_configured} missing={d.objstore_missing} />
          <Ready label="Billing (Stripe)" ok={d.tokens_enabled} missing={d.tokens_missing} />
        </ul>
      </Card>
    </div>
  );
}
