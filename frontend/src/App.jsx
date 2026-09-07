import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { AlertTriangle } from "lucide-react";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Sidebar, BottomNav } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { AuthDialog } from "@/components/AuthDialog";
import { BulkBanner } from "@/components/BulkBanner";
import { TokensDialog } from "@/components/TokensDialog";
import { AiConsentDialog } from "@/components/AiConsentDialog";
import { ShippingDialog } from "@/components/ShippingDialog";
import { Dashboard } from "@/views/Dashboard";
import { NewListing } from "@/views/NewListing";
import { ShopMode } from "@/views/ShopMode";
import { MessagesView } from "@/views/MessagesView";
import { SettingsView } from "@/views/SettingsView";
import { AdminView } from "@/views/AdminView";

// Every screen the nav can reach. A `view` that is not in here renders the
// dashboard rather than nothing: the main area used to be a chain of
// `{view === "x" && <X/>}` with no final else, so any value nobody had
// thought of — a role revoked mid-session, a state written by an older
// build — produced a page with the nav bar on it and NOTHING underneath.
// That is indistinguishable from a crash to the person looking at it, and
// unlike a crash it never reports itself.
const VIEWS = {
  dashboard: Dashboard,
  // Sell IS the pipeline now: upload box, drafts strip, and the listings
  // manager live on one screen (openListings lands here).
  new: NewListing,
  shop: ShopMode,
  messages: MessagesView,
  settings: SettingsView,
  // "ebay" was a separate account mirror; it's part of Settings now, so old
  // links and bookmarks land there instead of a blank page.
  ebay: SettingsView,
  admin: AdminView,
};

/** The screen a `view` renders — Home for anything unrecognised.
 *
 * Exported so the fallback can be asserted on: it is a branch that only runs
 * once something else has already gone wrong, which is exactly the kind that
 * rots unnoticed. Returns the ELEMENT rather than the component so the
 * lookup stays out of a component body, where the React lint (rightly, in
 * general) refuses a capitalised local rendered as a tag.
 */
export function screenFor(view, isSuperadmin, props = {}) {
  // A role revoked mid-session: the nav entry is already gone and the server
  // 404s the data anyway, so fall back to Home rather than to a screen that
  // cannot load.
  const Component = (view === "admin" && !isSuperadmin
    ? Dashboard : VIEWS[view]) || Dashboard;
  return <Component {...props} />;
}

function Main() {
  const { view, setView, health, activeBulk, clearBulk, isSuperadmin } = useApp();
  const [search, setSearch] = useState("");
  // Tapping a nav item means "take me to the top of that screen". Without
  // this the browser keeps the scroll offset across the swap, so leaving a
  // long listings page for the short dashboard lands you in the empty space
  // past the end of it — with the bottom nav still floating there, because
  // it is fixed. Blank, and nothing on screen to say why.
  useEffect(() => {
    try {
      window.scrollTo({ top: 0, behavior: "auto" });
    } catch {
      window.scrollTo(0, 0);
    }
  }, [view]);

  return (
    <div className="mx-auto flex max-w-[1600px] min-h-dvh">
      <Sidebar />
      {/* Native shell (Capacitor, contentInset "never") draws under the iPhone
          status bar/notch — the safe-area inset keeps the TopBar clear of it.
          On the plain web it's 0 and changes nothing. */}
      <div className="flex-1 min-w-0 px-4 sm:px-6 pb-28 md:pb-10 pt-[env(safe-area-inset-top)]">
        <TopBar onSearch={setSearch} onManageEbay={() => setView("settings")} />

        {activeBulk && view !== "new" && (
          <BulkBanner
            done={!!activeBulk.done}
            onReview={() => setView("new")}
            onDismiss={clearBulk}
          />
        )}

        {health._loaded && !health.anthropic_configured && (
          <div className="mb-4 rounded-card bg-warning-soft border border-warning/30 p-4 text-sm text-ink flex gap-2.5">
            <AlertTriangle size={17} className="text-warning shrink-0 mt-0.5" aria-hidden />
            <span>
              The AI isn't configured on the server yet (missing ANTHROPIC_API_KEY) —
              photo identification and refine won't work until it's set.
            </span>
          </div>
        )}

        {/* No AnimatePresence, and no exit animation, deliberately.
            `mode="wait"` holds the INCOMING screen unmounted until the
            outgoing one finishes animating away — so anything that stops
            that exit from completing (the webview backgrounded mid-tap, a
            second tap while the first is still running, a frame loop the OS
            suspended) leaves the main area with nothing in it at all, and
            leaves it that way. There is no amount of waiting that fixes it
            and nothing on screen that explains it: the nav bar is still
            there, because it lives outside this element, so the app looks
            alive and empty.

            A screen is worth more than the 180ms it fades in over. Keyed by
            `view`, this still remounts and fades in on every change — it
            just mounts FIRST and animates second, which is the order that
            cannot strand the seller on a blank page. */}
        <motion.main
          key={view}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18, ease: "easeOut" }}
        >
          {screenFor(view, isSuperadmin, { search })}
        </motion.main>
      </div>
      <BottomNav />
      <AuthDialog />
      <TokensDialog />
      <AiConsentDialog />
      <ShippingDialog />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppProvider>
        <Main />
      </AppProvider>
    </ToastProvider>
  );
}
