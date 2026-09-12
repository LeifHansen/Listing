import { useEffect, useState } from "react";
import { Search, Plus, Link2, CheckCircle2, Coins } from "lucide-react";
import { cn } from "@/lib/utils";
import { startConnect } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { NotificationsBell } from "@/components/NotificationsBell";
import { MessagesInbox } from "@/components/MessagesInbox";
import { useToast } from "@/components/ui/Toaster";

// TopBar — search, eBay connection state, Quick Add. No clutter.
export function TopBar({ onSearch, onManageEbay }) {
  const {
    user, openAuth, ebay, startNew, setView, session, tokens, setTokensOpen,
  } = useApp();
  const { toast } = useToast();
  const [q, setQ] = useState("");

  // Debounced, the same 300ms AdminUsers already uses for its own box.
  // `search` is Main's state and is handed to whichever screen is mounted,
  // and both screens that read it re-filter and re-sort the seller's WHOLE
  // store on every render (drafts and listings, plus the eBay-blocker check
  // for every draft card). Calling onSearch per keystroke made typing
  // "vintage" seven of those passes on the app's busiest screen, on a phone.
  // The box itself stays uncontrolled-feeling — `q` updates immediately, so
  // the characters appear as fast as they are typed; only the filtering
  // waits for a pause.
  useEffect(() => {
    const t = setTimeout(() => onSearch(q), 300);
    return () => clearTimeout(t);
  }, [q, onSearch]);

  const connectEbay = () => {
    if (!user) { openAuth(); return; }
    if (ebay.connected) { onManageEbay(); return; }
    if (!ebay.oauth_ready) {
      toast("eBay isn't configured on the server yet (needs EBAY_CLIENT_ID / SECRET / RUNAME).", { kind: "warning" });
      return;
    }
    startConnect("/api/ebay/connect")
      .catch((e) => toast(`Couldn't open the eBay connect screen: ${e.message}`,
        { kind: "error" }));
  };

  return (
    <header className="flex items-center gap-3 py-4">
      <div className="relative flex-1 max-w-md">
        <Search
          size={17}
          aria-hidden
          className="absolute left-4 top-1/2 -translate-y-1/2 text-ink-faint pointer-events-none"
        />
        <input
          type="search"
          value={q}
          placeholder="Search your listings…"
          aria-label="Search your listings"
          onChange={(e) => setQ(e.target.value)}
          // Searching filters the merged Sell screen — but never close an
          // open editor out from under the user; the filter applies once
          // they close it themselves.
          onFocus={() => { if (!session) setView("new"); }}
          className={cn(
            "w-full h-11 pl-11 pr-4 bg-card border border-line rounded-full text-[15px]",
            "placeholder:text-ink-faint shadow-card transition-all duration-150",
            "hover:border-line-strong focus:border-blue focus:outline-none focus:ring-2 focus:ring-blue/25",
          )}
        />
      </div>

      <div className="ml-auto flex items-center gap-2">
        <MessagesInbox />
        <NotificationsBell />
        {tokens.enabled && (
          <Button
            variant="soft"
            size="md"
            onClick={() => setTokensOpen(true)}
            aria-label={`AI tokens: ${user ? (tokens.total ?? 0) : "log in to see balance"}`}
            className={cn(
              // Running low reads as a warning before it becomes a wall.
              user && (tokens.total ?? 0) <= 5 && "bg-warning-soft text-warning",
            )}
          >
            <Coins aria-hidden />
            {user ? (tokens.total ?? 0) : "Tokens"}
          </Button>
        )}
        <Button
          variant={ebay.connected ? "soft" : "secondary"}
          size="md"
          onClick={connectEbay}
          className={cn("hidden sm:inline-flex", ebay.connected && "bg-green-soft text-green")}
        >
          {ebay.connected ? <CheckCircle2 aria-hidden /> : <Link2 aria-hidden />}
          {ebay.connected ? (ebay.username ? `eBay: ${ebay.username}` : "eBay connected") : "Connect eBay"}
        </Button>
        <Button variant="primary" size="md" onClick={startNew}>
          <Plus aria-hidden /> <span className="hidden sm:inline">New Listing</span>
          <span className="sm:hidden">New</span>
        </Button>
      </div>
    </header>
  );
}
