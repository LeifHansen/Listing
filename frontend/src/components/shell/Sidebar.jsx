import { useState } from "react";
import { motion } from "framer-motion";
import {
  LayoutDashboard, PlusCircle, Store, Package, FileText, Tags, Settings,
  Moon, Sun, PanelLeftClose, PanelLeftOpen, Zap, LogOut, LogIn,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "new", label: "New Listing", icon: PlusCircle },
  { id: "shop", label: "Shop Mode", icon: Store },
  { id: "inventory", label: "Inventory", icon: Package },
  { id: "drafts", label: "Drafts", icon: FileText },
  { id: "listings", label: "Listings", icon: Tags },
  { id: "settings", label: "Settings", icon: Settings },
];

export const APP_VERSION = "v2.0";

function Brand({ collapsed }) {
  return (
    <div className="flex items-center gap-2.5 px-2 h-12">
      <span className="grid place-items-center size-9 rounded-[13px] bg-blue text-on-accent shrink-0 shadow-card">
        <Zap size={19} strokeWidth={2.4} aria-hidden />
      </span>
      {!collapsed && (
        <span className="font-extrabold text-[19px] tracking-tight text-ink">
          Quick<span className="text-blue">Flip</span>
        </span>
      )}
    </div>
  );
}

function NavItem({ item, active, collapsed, badge, onClick }) {
  const Icon = item.icon;
  return (
    <button
      type="button"
      onClick={onClick}
      title={collapsed ? item.label : undefined}
      aria-current={active ? "page" : undefined}
      className={cn(
        "relative flex items-center gap-3 w-full min-h-11 px-3 rounded-button",
        "text-sm font-semibold transition-colors duration-150 cursor-pointer",
        active ? "text-blue" : "text-ink-secondary hover:text-ink hover:bg-bg-sunken",
        collapsed && "justify-center px-0",
      )}
    >
      {active && (
        <motion.span
          layoutId="sidebar-active"
          transition={{ duration: 0.22, ease: "easeOut" }}
          className="absolute inset-0 rounded-button bg-blue-soft"
          aria-hidden
        />
      )}
      <Icon size={19} strokeWidth={2} className="relative shrink-0" aria-hidden />
      {!collapsed && <span className="relative flex-1 text-left">{item.label}</span>}
      {!collapsed && badge != null && badge > 0 && (
        <span className="relative rounded-full bg-blue text-on-accent text-[11px] font-bold px-1.5 min-w-5 h-5 grid place-items-center">
          {badge}
        </span>
      )}
    </button>
  );
}

// Sidebar — rounded, floating, detached from the screen edges. Hidden on
// mobile (BottomNav takes over there).
export function Sidebar() {
  const { view, setView, startNew, dark, toggleDark, user, openAuth, logout, listingsState } = useApp();
  const [collapsed, setCollapsed] = useState(false);

  const counts = {
    inventory: listingsState.items.filter((i) => i.status === "unlisted").length,
    drafts: listingsState.items.filter((i) => i.status === "draft" || i.status === "dry_run").length,
  };

  return (
    <motion.aside
      animate={{ width: collapsed ? 76 : 248 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className={cn(
        "hidden md:flex flex-col sticky top-4 self-start m-4 mr-0 shrink-0",
        "max-h-[calc(100dvh-2rem)] bg-card border border-line rounded-card shadow-float p-3",
      )}
    >
      <Brand collapsed={collapsed} />

      <nav className="flex flex-col gap-1 mt-4 flex-1 overflow-y-auto" aria-label="Main">
        {NAV.map((item) => (
          <NavItem
            key={item.id}
            item={item}
            collapsed={collapsed}
            active={view === item.id}
            badge={counts[item.id]}
            onClick={() => (item.id === "new" ? startNew() : setView(item.id))}
          />
        ))}
      </nav>

      <div className="border-t border-line pt-3 mt-3 flex flex-col gap-1">
        {user ? (
          <button
            type="button"
            onClick={logout}
            title={`Log out ${user.email}`}
            className={cn(
              "flex items-center gap-3 min-h-11 px-2 rounded-button text-sm",
              "hover:bg-bg-sunken transition-colors duration-150 cursor-pointer",
              collapsed && "justify-center px-0",
            )}
          >
            <span className="grid place-items-center size-8 rounded-full bg-green-soft text-green font-bold text-xs uppercase shrink-0">
              {(user.display_name || user.email).slice(0, 2)}
            </span>
            {!collapsed && (
              <span className="flex-1 min-w-0 text-left">
                <span className="block truncate font-semibold text-ink text-[13px]">
                  {user.display_name || user.email}
                </span>
                <span className="text-xs text-ink-faint inline-flex items-center gap-1">
                  <LogOut size={11} aria-hidden /> Log out
                </span>
              </span>
            )}
          </button>
        ) : (
          <button
            type="button"
            onClick={() => openAuth()}
            className={cn(
              "flex items-center gap-3 min-h-11 px-3 rounded-button text-sm font-semibold",
              "text-blue hover:bg-blue-soft transition-colors duration-150 cursor-pointer",
              collapsed && "justify-center px-0",
            )}
          >
            <LogIn size={19} aria-hidden />
            {!collapsed && "Log in"}
          </button>
        )}

        <div className={cn("flex items-center gap-1", collapsed && "flex-col")}>
          <button
            type="button"
            onClick={toggleDark}
            aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
            className="grid place-items-center size-11 rounded-button text-ink-secondary hover:bg-bg-sunken hover:text-ink transition-colors duration-150 cursor-pointer"
          >
            {dark ? <Sun size={18} aria-hidden /> : <Moon size={18} aria-hidden />}
          </button>
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="grid place-items-center size-11 rounded-button text-ink-secondary hover:bg-bg-sunken hover:text-ink transition-colors duration-150 cursor-pointer"
          >
            {collapsed ? <PanelLeftOpen size={18} aria-hidden /> : <PanelLeftClose size={18} aria-hidden />}
          </button>
          {!collapsed && <span className="ml-auto pr-2 text-[11px] text-ink-faint font-medium">{APP_VERSION}</span>}
        </div>
      </div>
    </motion.aside>
  );
}

// BottomNav — the sidebar's mobile form: five thumb-sized targets.
export function BottomNav() {
  const { view, setView, startNew } = useApp();
  const items = [
    NAV[0], // dashboard
    NAV[2], // shop
    NAV[1], // new listing (center)
    NAV[5], // listings
    NAV[6], // settings
  ];
  return (
    <nav
      aria-label="Main"
      className={cn(
        "md:hidden fixed bottom-3 left-3 right-3 z-40 bg-card border border-line",
        "rounded-card shadow-float px-2 py-1.5 flex items-center justify-around",
        "pb-[max(0.375rem,env(safe-area-inset-bottom))]",
      )}
    >
      {items.map((item) => {
        const Icon = item.icon;
        const active = view === item.id;
        const isNew = item.id === "new";
        return (
          <button
            key={item.id}
            type="button"
            aria-label={item.label}
            aria-current={active ? "page" : undefined}
            onClick={() => (isNew ? startNew() : setView(item.id))}
            className={cn(
              "flex flex-col items-center justify-center gap-0.5 min-w-14 min-h-11 rounded-button",
              "text-[10px] font-semibold transition-colors duration-150 cursor-pointer",
              isNew
                ? "text-on-accent bg-blue rounded-full size-12 -mt-5 shadow-float shrink-0"
                : active ? "text-blue" : "text-ink-secondary",
            )}
          >
            <Icon size={isNew ? 22 : 19} strokeWidth={2.2} aria-hidden />
            {!isNew && item.label.split(" ")[0]}
          </button>
        );
      })}
    </nav>
  );
}
