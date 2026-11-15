import { lazy, Suspense, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/ui/Card";
import { Skeleton } from "@/components/ui/Skeleton";

// The panels are fetched the first time their tab opens, not with the app.
// Only a superadmin ever sees this screen, so shipping all eight to every
// seller's phone on every load was most of a chunk-size warning for nobody.
// The header and the tab strip stay in the main bundle, so the screen is
// never blank while a panel loads.
const panel = (load, name) => lazy(() => load().then((m) => ({ default: m[name] })));
const AdminOverview = panel(() => import("@/views/admin/AdminOverview"), "AdminOverview");
const AdminUsers = panel(() => import("@/views/admin/AdminUsers"), "AdminUsers");
const AdminBilling = panel(() => import("@/views/admin/AdminBilling"), "AdminBilling");
const AdminListings = panel(() => import("@/views/admin/AdminListings"), "AdminListings");
const AdminCompliance = panel(() => import("@/views/admin/AdminCompliance"), "AdminCompliance");
const AdminSystem = panel(() => import("@/views/admin/AdminSystem"), "AdminSystem");
const AdminAudit = panel(() => import("@/views/admin/AdminAudit"), "AdminAudit");
const AdminErrors = panel(() => import("@/views/admin/AdminErrors"), "AdminErrors");

// The operator console. Tabs, not routes — the app has no router, and the
// pill strip is the same pattern the listings manager uses. Every tab
// fetches its own data from /api/admin/* (view-local, like the dashboard's
// insights); the server re-checks the superadmin role on each call.
const TABS = [
  { id: "overview", label: "Overview", panel: AdminOverview },
  { id: "users", label: "Users", panel: AdminUsers },
  { id: "billing", label: "Billing", panel: AdminBilling },
  { id: "listings", label: "Listings", panel: AdminListings },
  { id: "compliance", label: "Compliance", panel: AdminCompliance },
  { id: "system", label: "System", panel: AdminSystem },
  { id: "errors", label: "Errors", panel: AdminErrors },
  { id: "audit", label: "Audit", panel: AdminAudit },
];

export function AdminView() {
  const [tabId, setTabId] = useState("overview");
  const tab = TABS.find((t) => t.id === tabId) || TABS[0];
  const Panel = tab.panel;

  return (
    <div className="flex flex-col gap-5">
      <SectionHeader
        icon={ShieldCheck}
        title="Admin"
        hint="The operator console. Every action taken here is written to the audit log."
      />

      <div className="flex items-center gap-2 overflow-x-auto pb-1" role="tablist" aria-label="Admin sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tabId === t.id}
            onClick={() => setTabId(t.id)}
            className={cn(
              "shrink-0 inline-flex items-center gap-1.5 h-9 px-3.5 rounded-full text-[13px]",
              "font-semibold cursor-pointer transition-colors duration-150 border",
              tabId === t.id
                ? "bg-blue text-on-accent border-blue"
                : "bg-card text-ink-secondary border-line hover:text-ink hover:border-line-strong",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      <Suspense fallback={<Skeleton className="h-40 w-full" />}>
        <Panel />
      </Suspense>
    </div>
  );
}
