import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";
import { SectionHeader } from "@/components/ui/Card";
import { AdminOverview } from "@/views/admin/AdminOverview";
import { AdminUsers } from "@/views/admin/AdminUsers";
import { AdminBilling } from "@/views/admin/AdminBilling";
import { AdminListings } from "@/views/admin/AdminListings";
import { AdminCompliance } from "@/views/admin/AdminCompliance";
import { AdminSystem } from "@/views/admin/AdminSystem";
import { AdminAudit } from "@/views/admin/AdminAudit";
import { AdminErrors } from "@/views/admin/AdminErrors";

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

      <Panel />
    </div>
  );
}
