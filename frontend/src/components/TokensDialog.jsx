import { useEffect, useState } from "react";
import { Coins, Sparkles, Check } from "lucide-react";
import { cn } from "@/lib/utils";
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

function fmtUsd(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}

// TokensDialog — balance, what things cost, and the packs. Opens from the
// TopBar chip, or automatically when an AI call runs out of tokens.
export function TokensDialog() {
  const { tokens, tokensOpen, setTokensOpen, loadTokens, user, openAuth } = useApp();
  const { toast } = useToast();
  const [buying, setBuying] = useState(null); // pack id being checked out

  // Refresh the balance every time the dialog opens — the chip can be stale
  // after a run of AI actions.
  useEffect(() => { if (tokensOpen) loadTokens(); }, [tokensOpen, loadTokens]);

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
            + "here automatically once it completes.", { duration: 6000 });
          setTokensOpen(false);
        } else {
          toast("Couldn't open the browser. Visit the Thryft Shop website to "
            + "buy tokens — they arrive on this account either way.",
          { kind: "warning", duration: 8000 });
        }
        setBuying(null);
        return;
      }
      window.location.href = res.url; // off to Stripe Checkout
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
                <div className="text-2xl font-extrabold text-ink leading-tight">
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
                  <span className="absolute -top-2.5 left-3 rounded-full bg-blue text-on-accent text-[10px] font-bold px-2 py-0.5 inline-flex items-center gap-1">
                    <Check size={10} aria-hidden /> Best value
                  </span>
                )}
                <div className="text-sm font-bold text-ink">{p.label}</div>
                <div className="text-xl font-extrabold text-ink">{p.tokens}</div>
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
      </div>
    </Dialog>
  );
}
