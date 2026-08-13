import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle, Loader2 } from "lucide-react";
import { AppProvider, useApp } from "@/store";
import { ToastProvider } from "@/components/ui/Toaster";
import { Sidebar, BottomNav } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { AuthDialog } from "@/components/AuthDialog";
import { TokensDialog } from "@/components/TokensDialog";
import { Dashboard } from "@/views/Dashboard";
import { NewListing } from "@/views/NewListing";
import { ShopMode } from "@/views/ShopMode";
import { ListingsView } from "@/views/ListingsView";
import { SettingsView } from "@/views/SettingsView";

function Main() {
  const { view, setView, health, activeBulk } = useApp();
  const [search, setSearch] = useState("");

  return (
    <div className="mx-auto flex max-w-[1600px] min-h-dvh">
      <Sidebar />
      {/* Native shell (Capacitor, contentInset "never") draws under the iPhone
          status bar/notch — the safe-area inset keeps the TopBar clear of it.
          On the plain web it's 0 and changes nothing. */}
      <div className="flex-1 min-w-0 px-4 sm:px-6 pb-28 md:pb-10 pt-[env(safe-area-inset-top)]">
        <TopBar onSearch={setSearch} onManageEbay={() => setView("settings")} />

        {activeBulk && view !== "new" && (
          <button
            type="button"
            onClick={() => setView("new")}
            className="mb-4 w-full flex items-center gap-3 rounded-card bg-blue-soft border border-blue/30 p-4 text-left text-sm text-ink hover:border-blue/50 transition-colors cursor-pointer"
          >
            <Loader2 size={17} className="text-blue shrink-0 animate-spin" aria-hidden />
            <span className="flex-1 min-w-0">
              <strong className="font-semibold">A bulk batch is processing.</strong>{" "}
              Finished items save to Drafts automatically — tap to watch or review it.
            </span>
            <span className="font-semibold text-blue shrink-0">Review →</span>
          </button>
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

        <AnimatePresence mode="wait">
          <motion.main
            key={view}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.18, ease: "easeOut" }}
          >
            {view === "dashboard" && <Dashboard />}
            {/* Sell IS the pipeline now: upload box, drafts strip, and the
                listings manager live on one screen (openListings lands here). */}
            {view === "new" && <NewListing search={search} />}
            {view === "shop" && <ShopMode />}
            {/* "ebay" was a separate account mirror; it's part of Settings now,
                so old links/bookmarks land there instead of a blank page. */}
            {(view === "settings" || view === "ebay") && <SettingsView />}
          </motion.main>
        </AnimatePresence>
      </div>
      <BottomNav />
      <AuthDialog />
      <TokensDialog />
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
