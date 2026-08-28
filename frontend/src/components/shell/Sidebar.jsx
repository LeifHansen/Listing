import { useState } from "react";
import { motion } from "framer-motion";
import {
  LayoutDashboard, PlusCircle, Store, Settings,
  Moon, Sun, PanelLeftClose, PanelLeftOpen, LogOut, LogIn,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useApp } from "@/store";
import { BrandMark, BRAND_LOGO } from "@/components/BrandMark";

// Sell IS the pipeline: the upload box, the drafts strip, and the listings
// manager share one screen, so the old Drafts / Listing Manager entries are
// gone and the drafts-waiting badge rides the Sell tab instead. The mobile
// bottom bar shows the first word of each label.
const NAV = [
  { id: "dashboard", label: "Home", icon: LayoutDashboard },
  { id: "new", label: "Sell", icon: PlusCircle },
  { id: "shop", label: "Shop", icon: Store },
  { id: "settings", label: "Settings", icon: Settings },
];

const byId = (id) => NAV.find((n) => n.id === id);

export const APP_VERSION = "v2.0";

function Brand({ collapsed }) {
  const [imgOk, setImgOk] = useState(true);
  if (collapsed) {
    return (
      <div className="flex items-center justify-center h-12">
        <BrandMark />
      </div>
    );
  }
  return (
    <div className="flex items-center px-2 h-14">
      {imgOk ? (
        <img
          src={BRAND_LOGO}
          alt="Thryft Shop"
          onError={() => setImgOk(false)}
          className="h-14 w-auto object-contain"
        />
      ) : (
        <span className="font-display font-bold text-[19px] text-ink whitespace-nowrap">
          Thryft <span className="text-blue">Shop</span>
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

  // Drafts live at the top of the Sell screen, so its badge is the
  // drafts-waiting count.
  const counts = {
    new: listingsState.items.filter(
      (i) => i.status === "draft" || i.status === "dry_run").length,
  };

  return (
    <motion.aside
      animate={{ width: collapsed ? 76 : 248 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className={cn(
        "hidden md:flex flex-col sticky self-start m-4 mr-0 shrink-0",
        "top-[max(1rem,env(safe-area-inset-top))]",  // clear the notch in the native shell (iPad)
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
            <span className="grid place-items-center size-8 rounded-full bg-green-soft text-green font-display font-bold text-xs uppercase shrink-0">
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

// BottomNav — the sidebar's mobile form: thumb-sized targets around the FAB.
export function BottomNav() {
  const { view, setView, startNew } = useApp();
  // Reference by id (not index) so reordering NAV never scrambles the bar.
  const items = ["dashboard", "shop", "new", "settings"].map(byId);
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
              "flex flex-col items-center justify-center gap-0.5 min-w-12 min-h-11 rounded-button",
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
