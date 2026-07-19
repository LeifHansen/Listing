import { motion } from "framer-motion";
import {
  Camera, Upload, PlusCircle, Store, ArrowRight, Rocket, FileText,
  Tags, Timer, Coins,
} from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Card, SectionHeader } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { StatCard } from "@/components/ui/StatCard";
import { ListingCard } from "@/components/ListingCard";
import { ListingCardSkeleton } from "@/components/ui/Skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { BoxIllustration, RobotIllustration } from "@/components/ui/illustrations";
import { formatMoney } from "@/lib/utils";

function greeting() {
  const h = new Date().getHours();
  if (h < 5) return "Burning the midnight oil";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

function isToday(iso) {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate();
}

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05 } },
};
const rise = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.25, ease: "easeOut" } },
};

export function Dashboard() {
  const { user, openAuth, listingsState, startNew, openListing, setView, session, deleteListing } = useApp();
  const { confirm } = useToast();
  const items = listingsState.items;

  const askDelete = async (item) => {
    const name = item.listing?.title || item.title || "this listing";
    if (await confirm({
      title: "Delete this listing?",
      message: `"${name}" will be permanently removed. This can't be undone.`,
      confirmLabel: "Delete",
      danger: true,
    })) deleteListing(item.id);
  };

  const todays = items.filter((i) => isToday(i.created_at));
  const drafts = items.filter((i) => i.status === "draft" || i.status === "dry_run");
  const live = items.filter((i) => i.status === "published" || i.status === "live");
  const inventory = items.filter((i) => i.status === "unlisted");
  const revenue = live.reduce((sum, i) => sum + (Number(i.listing?.price) || 0), 0);
  // Sellers report ~12 min per hand-written listing; QuickFlip takes ~2.
  const minutesSaved = items.length * 10;
  const timeSaved = minutesSaved >= 90
    ? `${(minutesSaved / 60).toFixed(1)} h`
    : `${minutesSaved} min`;

  // An in-memory session resumes directly; otherwise reopen the newest draft.
  const lastOpen = session
    ? { title: session.listing?.title, go: () => setView("new") }
    : (drafts[0] && {
        title: drafts[0].listing?.title || drafts[0].title,
        go: () => openListing(drafts[0].id),
      });

  const recent = [...items]
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
    .slice(0, 4);

  const quickActions = [
    { label: "Take Photos", icon: Camera, onClick: startNew, tone: "bg-blue-soft text-blue" },
    { label: "Upload Images", icon: Upload, onClick: startNew, tone: "bg-green-soft text-green" },
    { label: "Create Listing", icon: PlusCircle, onClick: startNew, tone: "bg-yellow-soft text-warning" },
    { label: "Shop Mode", icon: Store, onClick: () => setView("shop"), tone: "bg-red-soft text-error" },
  ];

  return (
    <motion.div variants={stagger} initial="hidden" animate="show" className="flex flex-col gap-6">
      {/* Hero */}
      <motion.div variants={rise}>
        <Card className="relative overflow-hidden p-7 sm:p-8">
          <div className="flex flex-wrap items-start gap-6 justify-between">
            <div className="min-w-0">
              <h1 className="text-2xl sm:text-[28px] font-bold tracking-tight text-ink">
                {greeting()}{user ? `, ${user.display_name || user.email.split("@")[0]}` : ""} 👋
              </h1>
              <p className="mt-1.5 text-[15px] text-ink-secondary">
                {todays.length > 0
                  ? <>You've created <strong className="text-ink">{todays.length}</strong> listing{todays.length === 1 ? "" : "s"} today.</>
                  : "Ready to flip something today?"}
                {drafts.length > 0 && <> {drafts.length} draft{drafts.length === 1 ? "" : "s"} waiting.</>}
              </p>
              <div className="mt-5 flex flex-wrap items-center gap-2.5">
                {lastOpen ? (
                  <Button variant="primary" size="lg" onClick={lastOpen.go} className="max-w-full">
                    <span className="truncate">
                      Continue "{lastOpen.title || "last listing"}"
                    </span>
                    <ArrowRight aria-hidden className="shrink-0" />
                  </Button>
                ) : (
                  <Button variant="primary" size="lg" onClick={startNew}>
                    <PlusCircle aria-hidden /> Create a listing
                  </Button>
                )}
                {!user && (
                  <Button variant="ghost" size="lg" onClick={() => openAuth()}>
                    Log in to save your work
                  </Button>
                )}
              </div>
            </div>
            <div className="hidden lg:block shrink-0 -my-2">
              <RobotIllustration />
            </div>
          </div>
        </Card>
      </motion.div>

      {/* Quick actions */}
      <motion.div variants={rise} className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {quickActions.map((a) => (
          <motion.button
            key={a.label}
            type="button"
            onClick={a.onClick}
            whileHover={{ y: -2, boxShadow: "var(--shadow-card-hover)" }}
            whileTap={{ scale: 0.98 }}
            transition={{ duration: 0.15 }}
            className="bg-card border border-line rounded-card shadow-card p-5 flex items-center gap-3.5 cursor-pointer text-left"
          >
            <span className={`grid place-items-center size-11 rounded-[14px] shrink-0 ${a.tone}`}>
              <a.icon size={21} strokeWidth={2} aria-hidden />
            </span>
            <span className="font-semibold text-sm text-ink">{a.label}</span>
          </motion.button>
        ))}
      </motion.div>

      {/* Performance */}
      <motion.div variants={rise} className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={Rocket} tone="blue" label="Today's listings" value={todays.length} />
        <StatCard icon={FileText} tone="yellow" label="Drafts" value={drafts.length}
          sub={inventory.length ? `+ ${inventory.length} unlisted find${inventory.length === 1 ? "" : "s"}` : undefined} />
        <StatCard icon={Coins} tone="green" label="Live value"
          value={formatMoney(revenue) || "$0"} sub={`${live.length} live listing${live.length === 1 ? "" : "s"}`} />
        <StatCard icon={Timer} tone="red" label="Time saved" value={items.length ? `~${timeSaved}` : "—"}
          sub="vs. writing listings by hand" />
      </motion.div>

      {/* Recent listings */}
      <motion.div variants={rise}>
        <SectionHeader
          icon={Tags}
          title="Recent listings"
          action={items.length > 0 && (
            <Button variant="ghost" size="sm" onClick={() => setView("listings")}>
              View all <ArrowRight aria-hidden />
            </Button>
          )}
        />
        {listingsState.loading && !listingsState.loaded ? (
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
            {[0, 1, 2, 3].map((i) => <ListingCardSkeleton key={i} />)}
          </div>
        ) : recent.length > 0 ? (
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-4">
            {recent.map((item) => (
              <ListingCard key={item.id} item={item} onOpen={openListing} onDelete={askDelete} />
            ))}
          </div>
        ) : (
          <Card className="p-0">
            <EmptyState
              illustration={BoxIllustration}
              title="No listings yet"
              message={user
                ? "Let's create your first listing — snap a few photos and the AI writes the rest."
                : "Log in to keep your listings, or jump straight in and create one."}
              action={
                <Button variant="primary" size="lg" onClick={startNew}>
                  <PlusCircle aria-hidden /> Create Listing
                </Button>
              }
            />
          </Card>
        )}
      </motion.div>
    </motion.div>
  );
}
