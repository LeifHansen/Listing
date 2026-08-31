import { useState } from "react";
import { LogOut, Ban, RotateCcw, Gift } from "lucide-react";
import { postJson } from "@/lib/api";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/fields";
import { TagPill, StatusBadge } from "@/components/ui/badges";
import { Skeleton } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/Toaster";
import { relTime, signedTokens } from "@/lib/adminView";
import { useAdminRead } from "@/views/admin/useAdminRead";

// One account, in full, with the three console actions. Every action asks
// first (grant via its explicit form, the rest via confirm) and reports
// what actually happened — the server refuses anything aimed at another
// superadmin, and the buttons say so up front rather than failing late.
export function AdminUserDialog({ userId, onClose, onChanged }) {
  const { toast, confirm } = useToast();
  const state = useAdminRead(`/api/admin/users/${userId}`);
  const user = state.kind === "ready" ? state.data.user : null;

  const [grantAmount, setGrantAmount] = useState("");
  const [grantNote, setGrantNote] = useState("");
  const [busy, setBusy] = useState(null); // "grant" | "signout" | "disable"

  const act = async (name, fn, doneMsg) => {
    setBusy(name);
    try {
      await fn();
      toast(doneMsg, { kind: "success" });
      state.reload();
      onChanged?.();
    } catch (e) {
      toast(e.message || "That didn’t go through — nothing was changed.",
        { kind: "error" });
    } finally {
      setBusy(null);
    }
  };

  const grant = () => {
    const amount = Number.parseInt(grantAmount, 10);
    if (!Number.isFinite(amount) || amount < 1) {
      toast("How many tokens? Enter a whole number.", { kind: "error" });
      return;
    }
    act("grant",
      () => postJson(`/api/admin/users/${userId}/grant-tokens`,
        { tokens: amount, note: grantNote.trim() }),
      `Granted ${amount} tokens.`);
  };

  const signOut = async () => {
    if (!(await confirm({
      title: "Sign this account out everywhere?",
      message: "Every session token it holds stops working immediately. "
        + "They sign back in with their password as usual.",
      confirmLabel: "Sign out everywhere",
    }))) return;
    act("signout",
      () => postJson(`/api/admin/users/${userId}/revoke-sessions`),
      "Signed out everywhere.");
  };

  const setDisabled = async (disabled) => {
    if (disabled && !(await confirm({
      title: "Disable this account?",
      message: "Login is refused and every live session ends now. Their "
        + "listings and data stay put; re-enable restores access.",
      confirmLabel: "Disable account",
      danger: true,
    }))) return;
    act("disable",
      () => postJson(`/api/admin/users/${userId}/disable`, { disabled }),
      disabled ? "Account disabled." : "Account re-enabled.");
  };

  const byStatus = user?.listings_by_status || {};
  const statuses = Object.keys(byStatus).sort();

  return (
    <Dialog open onClose={onClose} title={user ? user.email : "Account"} wide>
      {state.kind === "loading" && (
        <div className="space-y-3">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {state.kind === "unavailable" && (
        <p className="text-sm text-ink-secondary">
          We couldn’t load this account just now. Try again in a moment.
        </p>
      )}

      {user && (
        <div className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-2">
            {user.role === "superadmin" && <TagPill tone="red">superadmin</TagPill>}
            {user.disabled_at
              ? <TagPill tone="red">disabled {relTime(user.disabled_at)}</TagPill>
              : <TagPill tone="green">active</TagPill>}
            <TagPill>joined {relTime(user.created_at)}</TagPill>
            {user.display_name && <TagPill>{user.display_name}</TagPill>}
            {(user.connections || []).map((c) => (
              <TagPill key={c.marketplace} tone="blue">
                {c.marketplace}{c.username ? ` · ${c.username}` : ""}
              </TagPill>
            ))}
          </div>

          <div className="grid sm:grid-cols-2 gap-4">
            <div className="rounded-tile border border-line p-4">
              <h3 className="text-[13px] font-bold text-ink mb-2">Listings</h3>
              {statuses.length === 0 ? (
                <p className="text-sm text-ink-secondary">None yet.</p>
              ) : (
                <ul className="flex flex-wrap gap-1.5">
                  {statuses.map((s) => (
                    <li key={s} className="inline-flex items-center gap-1.5">
                      <StatusBadge status={s} />
                      <span className="text-[13px] font-semibold tabular-nums text-ink-secondary">
                        {byStatus[s]}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="rounded-tile border border-line p-4">
              <h3 className="text-[13px] font-bold text-ink mb-2">AI tokens</h3>
              {user.tokens ? (
                <p className="text-sm text-ink-secondary">
                  <span className="font-semibold text-ink tabular-nums">{user.tokens.purchased}</span> purchased
                  {" · "}
                  <span className="tabular-nums">{user.tokens.free_used}</span> free used
                  {user.tokens.free_period ? ` in ${user.tokens.free_period}` : ""}
                </p>
              ) : (
                <p className="text-sm text-ink-secondary">No balance row yet.</p>
              )}
              {(user.ledger || []).length > 0 && (
                <ul className="mt-2 text-xs text-ink-faint flex flex-col gap-1 max-h-28 overflow-y-auto">
                  {user.ledger.map((e, i) => (
                    <li key={i} className="flex justify-between gap-2 tabular-nums">
                      <span className="truncate">{e.feature || e.kind}</span>
                      <span>{signedTokens(e.tokens)} · {relTime(e.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {user.role === "superadmin" ? (
            <p className="text-[13px] text-ink-secondary rounded-tile bg-bg-sunken p-3">
              Superadmin accounts are managed with
              {" "}<code className="font-semibold">scripts/grant_superadmin.py</code>
              {" "}— the console can’t disable or act on one.
            </p>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="rounded-tile border border-line p-4 flex flex-col gap-3">
                <h3 className="text-[13px] font-bold text-ink">Grant tokens</h3>
                <div className="flex flex-wrap items-end gap-3">
                  <Field label="Tokens" className="w-28">
                    <Input
                      type="number" min="1" step="1"
                      value={grantAmount}
                      onChange={(e) => setGrantAmount(e.target.value)}
                      placeholder="25"
                    />
                  </Field>
                  <Field label="Note" hint="lands in the ledger" className="flex-1 min-w-40">
                    <Input
                      value={grantNote}
                      onChange={(e) => setGrantNote(e.target.value)}
                      placeholder="e.g. goodwill for the failed identify"
                    />
                  </Field>
                  <Button variant="primary" size="md" loading={busy === "grant"}
                    onClick={grant}>
                    <Gift size={16} aria-hidden /> Grant
                  </Button>
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" size="sm" loading={busy === "signout"}
                  onClick={signOut}>
                  <LogOut size={15} aria-hidden /> Sign out everywhere
                </Button>
                {user.disabled_at ? (
                  <Button variant="success" size="sm" loading={busy === "disable"}
                    onClick={() => setDisabled(false)}>
                    <RotateCcw size={15} aria-hidden /> Re-enable account
                  </Button>
                ) : (
                  <Button variant="danger" size="sm" loading={busy === "disable"}
                    onClick={() => setDisabled(true)}>
                    <Ban size={15} aria-hidden /> Disable account
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
