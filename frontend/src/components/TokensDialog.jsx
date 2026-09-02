import { useEffect, useState } from "react";
import { Coins, Sparkles, Check, History } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useApp, postJson } from "@/store";
import { isNative, openExternal } from "@/lib/platform";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toaster";

// What each AI feature costs, in plain seller language. Keys match the
// server's cost table; anything the server adds later just won't be listed.
const COST_LABELS = [
  ["identify", "AI listing draft (photos → full listing)"],
  ["refine", "AI refine instruction"],
  ["specifics", "Autofill item specifics"],
  ["shelf_scan", "Shop Mode shelf scan"],
  ["image_ai", "AI photo tool (per photo)"],
];

// Ledger `kind` in seller language. "reversal" is what a refund or a lost
// chargeback looks like from this side, and it has to be legible: a balance
// that dropped with no explanation is worse than the refund itself.
const ENTRY_LABELS = {
  purchase: "Bought tokens",
  grant: "Tokens added",
  spend: "AI used",
  refund: "Refunded (AI failed)",
  reversal: "Purchase refunded",
};

const FEATURE_LABELS = {
  identify: "listing draft",
  refine: "AI refine",
  specifics: "item specifics",
  shelf_scan: "shelf scan",
  image_ai: "photo tool",
};

function fmtUsd(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

function fmtDate(iso) {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch (e) {
    return "";
  }
}

// TokensDialog — balance, what things cost, and the packs. Opens from the
// TopBar chip, or automatically when an AI call runs out of tokens.
export function TokensDialog() {
  const { tokens, tokensOpen, setTokensOpen, loadTokens, user, openAuth } = useApp();
  const { toast } = useToast();
  const [buying, setBuying] = useState(null); // pack id being checked out
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState(null); // null = not loaded yet

  // Refresh the balance every time the dialog opens — the chip can be stale
  // after a run of AI actions.
  useEffect(() => { if (tokensOpen) loadTokens(); }, [tokensOpen, loadTokens]);

  // Two things have to be put back to their starting position the moment the
  // dialog or the section is toggled, and both are done DURING RENDER by
  // comparing against the previous values (React's documented "adjust state
  // when something changes" pattern) instead of in an effect. An effect would
  // only reset AFTER a frame carrying the old content had been committed, and
  // in the ledger's case that frame shows a previous load's entries as if they
  // were current — exactly the confusion this section exists to prevent.
  //   * Closing the dialog collapses the section, so it opens as a buying
  //     screen next time.
  //   * Anything that restarts the ledger fetch below — opening the section,
  //     reopening the dialog, a different user — puts the ledger back to
  //     "Loading…". These are the same three values the fetch effect depends
  //     on, compared by identity the way React compares dependencies, so the
  //     reset and the re-fetch stay in step. Clearing when the section is
  //     closed is invisible (nothing is rendered) and harmless.
  const [prevOpenState, setPrevOpenState] = useState({ tokensOpen, historyOpen, user });
  if (prevOpenState.tokensOpen !== tokensOpen
    || prevOpenState.historyOpen !== historyOpen
    || prevOpenState.user !== user) {
    if (prevOpenState.tokensOpen !== tokensOpen && !tokensOpen) setHistoryOpen(false);
    setPrevOpenState({ tokensOpen, historyOpen, user });
    setHistory(null);
  }

  // Fetched only when the section is opened, and re-fetched each time so it
  // reflects AI spent since the dialog was last looked at. The reset to the
  // loading state happens during render above, not here.
  useEffect(() => {
    if (!historyOpen || !user) return undefined;
    let alive = true;
    api("/api/tokens/history")
      .then((r) => { if (alive) setHistory({ entries: r.entries || [] }); })
      .catch((e) => {
        // Shown as it arrived: `api()` hands back a sentence written for the
        // seller, and prefixing it produced "Couldn't load your activity: We
        // couldn't load your activity just now."
        if (alive) setHistory({ error: e.message || "Couldn’t load your activity just now. Try again in a moment." });
      });
    return () => { alive = false; };
  }, [historyOpen, user, tokensOpen]);

  const buy = async (packId) => {
    if (!user) { setTokensOpen(false); openAuth(); return; }
    setBuying(packId);
    try {
      const res = await postJson("/api/tokens/checkout", { pack_id: packId });
      if (isNative()) {
        // App Store rule (guideline 3.1.1): a non-Apple checkout must NOT
        // complete inside the app — but a link out to the system browser is
        // allowed on the US storefront. Never fall back to in-webview
        // navigation here; a visible error beats a rejected binary.
        const opened = await openExternal(res.url);
        if (opened) {
          toast("Finishing your purchase in the browser — your tokens appear "
            + "here automatically once it completes.", { ttl: 6000 });
          setTokensOpen(false);
        } else {
          toast("Couldn't open the browser. Visit the Thryft Shop website to "
            + "buy tokens — they arrive on this account either way.",
          { kind: "warning", ttl: 8000 });
        }
        setBuying(null);
        return;
      }
      // Off to Stripe Checkout. assign() is the method form of setting
      // location.href — same navigation, same history entry — and it does not
      // read as a write to a value owned outside the component.
      window.location.assign(res.url);
    } catch (e) {
      toast(`Couldn't start the purchase: ${e.message}`, { kind: "error" });
      setBuying(null);
    }
  };

  const free = tokens.free_remaining ?? tokens.free_quota ?? 0;
  const purchased = tokens.purchased ?? 0;
  const bestValue = tokens.packs?.length
    ? tokens.packs.reduce((a, b) => (a.usd_cents / a.tokens <= b.usd_cents / b.tokens ? a : b)).id
    : null;

  return (
    <Dialog
      open={tokensOpen}
      onClose={() => setTokensOpen(false)}
      title="AI tokens"
      wide
    >
      <div className="space-y-5">
        {/* Balance */}
        <div className="flex items-center gap-4 rounded-card bg-bg-sunken p-4">
          <span className="grid place-items-center size-12 rounded-full bg-blue-soft text-blue shrink-0">
            <Coins size={22} aria-hidden />
          </span>
          <div className="flex-1 min-w-0">
            {user ? (
              <>
                <div className="font-display text-2xl font-bold text-ink leading-tight">
                  {(tokens.total ?? free + purchased)}{" "}
                  <span className="text-sm font-semibold text-ink-secondary">tokens</span>
                </div>
                <div className="text-[13px] text-ink-secondary">
                  {free} free this month · {purchased} purchased.{" "}
                  Free tokens reset on {tokens.resets_on || "the 1st"}; purchased ones never expire.
                </div>
              </>
            ) : (
              <div className="text-sm text-ink-secondary">
                <button type="button" className="font-semibold text-blue cursor-pointer" onClick={() => { setTokensOpen(false); openAuth(); }}>
                  Log in
                </button>{" "}
                to see your balance — every account gets {tokens.free_quota ?? 50} free tokens a month.
              </div>
            )}
          </div>
        </div>

        {/* What things cost */}
        <div>
          <h3 className="text-sm font-bold text-ink mb-2 flex items-center gap-1.5">
            <Sparkles size={15} className="text-blue" aria-hidden /> What AI features cost
          </h3>
          <ul className="text-[13px] text-ink-secondary space-y-1">
            {COST_LABELS.filter(([k]) => tokens.costs?.[k] != null).map(([k, label]) => (
              <li key={k} className="flex items-center justify-between gap-3">
                <span>{label}</span>
                <span className="font-semibold text-ink whitespace-nowrap">
                  {tokens.costs[k]} {tokens.costs[k] === 1 ? "token" : "tokens"}
                </span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-faint mt-2">
            Failed AI calls are refunded automatically — you only pay for AI that worked.
          </p>
        </div>

        {/* Packs */}
        {tokens.stripe ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {(tokens.packs || []).map((p) => (
              <div
                key={p.id}
                className={cn(
                  "relative rounded-card border p-4 flex flex-col gap-1",
                  p.id === bestValue ? "border-blue bg-blue-soft/40" : "border-line",
                )}
              >
                {p.id === bestValue && (
                  <span className="absolute -top-2.5 left-3 rounded-full bg-blue text-on-accent font-display text-[10px] font-bold px-2 py-0.5 inline-flex items-center gap-1">
                    <Check size={10} aria-hidden /> Best value
                  </span>
                )}
                <div className="font-display text-sm font-bold text-ink">{p.label}</div>
                <div className="font-display text-xl font-bold text-ink">{p.tokens}</div>
                <div className="text-xs text-ink-faint mb-2">
                  tokens · {fmtUsd(Math.round(p.usd_cents / p.tokens))}/token
                </div>
                <Button
                  variant={p.id === bestValue ? "primary" : "secondary"}
                  size="sm"
                  className="mt-auto"
                  loading={buying === p.id}
                  disabled={buying != null}
                  onClick={() => buy(p.id)}
                >
                  {fmtUsd(p.usd_cents)}
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[13px] text-ink-secondary rounded-card border border-line p-3">
            Purchases aren't set up on this server yet — your free monthly tokens
            still apply.
          </p>
        )}

        {/* Where the tokens went. Once people are paying, "my balance dropped
            and I don't know why" is the single most common support question,
            and the only honest answer is the ledger itself. Collapsed by
            default so the dialog stays a buying screen. */}
        {user && (
          <div className="border-t border-line pt-3">
            <button
              type="button"
              onClick={() => setHistoryOpen((o) => !o)}
              aria-expanded={historyOpen}
              className="text-[13px] font-semibold text-ink-secondary hover:text-ink cursor-pointer inline-flex items-center gap-1.5"
            >
              <History size={14} aria-hidden />
              {historyOpen ? "Hide activity" : "Recent activity"}
            </button>
            {historyOpen && (
              <div className="mt-2.5">
                {history == null && (
                  <p className="text-[13px] text-ink-faint">Loading…</p>
                )}
                {history?.error && (
                  <p className="text-[13px] text-ink-secondary">{history.error}</p>
                )}
                {history?.entries?.length === 0 && (
                  <p className="text-[13px] text-ink-faint">
                    Nothing yet — your AI activity will show up here.
                  </p>
                )}
                {history?.entries?.length > 0 && (
                  <ul className="text-[13px] divide-y divide-line max-h-56 overflow-y-auto">
                    {history.entries.map((e, i) => (
                      <li key={i} className="flex items-baseline justify-between gap-3 py-1.5">
                        <span className="min-w-0 text-ink-secondary truncate">
                          {ENTRY_LABELS[e.kind] || e.kind}
                          {e.feature ? ` · ${FEATURE_LABELS[e.feature] || e.feature}` : ""}
                          {e.note ? ` · ${e.note}` : ""}
                        </span>
                        <span className="shrink-0 flex items-baseline gap-2">
                          <span className={cn("font-bold tabular-nums",
                            e.tokens > 0 ? "text-green" : "text-ink")}>
                            {e.tokens > 0 ? `+${e.tokens}` : e.tokens}
                          </span>
                          <span className="text-ink-faint tabular-nums text-[12px]">
                            {fmtDate(e.created_at)}
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}
