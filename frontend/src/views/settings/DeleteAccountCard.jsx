import { useState } from "react";
import { AlertTriangle, Trash2 } from "lucide-react";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/fields";
import { useToast } from "@/components/ui/Toaster";
import { deleteAccountNotice } from "@/lib/deleteAccount";

// Leaving is as easy as joining: one button here, no email, no support ticket.
// The dialog states plainly what goes and what survives (anything already
// published stays live on the seller's own eBay account — we can delete our
// copy, not their listings).
export function DeleteAccountCard() {
  const { user, clearSignedInState } = useApp();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState(null);
  const [password, setPassword] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");
  // Below the hooks, not between them: a plain call sitting in the middle of
  // a useState run reads like a conditional hook even though it is not.
  const notice = deleteAccountNotice(summary);

  const start = async () => {
    setPassword("");
    setError("");
    setSummary(null);
    setOpen(true);
    try {
      setSummary(await api("/api/account/summary"));
    } catch {
      // `counted: false`, not `{}`. The dialog's warning keys on that flag,
      // and an empty object satisfied neither of its branches -- so a failed
      // summary call silently dropped the one thing this dialog has to say.
      setSummary({ counted: false });
    }
  };

  const remove = async () => {
    setDeleting(true);
    setError("");
    try {
      const res = await postJson("/api/account/delete", { password });
      setOpen(false);
      // Clear every trace of the account from the running app, not just the
      // user — the same teardown a sign-out does, so the native shell's
      // stored token, the listings, the inbox and the eBay connection all
      // go with it rather than lingering until their next fetch 401s.
      clearSignedInState();
      toast(
        `Your account is deleted${res.deleted_listings
          ? ` — ${res.deleted_listings} listing${res.deleted_listings === 1 ? "" : "s"} and ${res.deleted_listings === 1 ? "its" : "their"} photos are gone`
          : ""}. Thanks for giving Thryft Shop a try.`,
        { kind: "success" },
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <>
      <div className="flex flex-col gap-4 max-w-lg">
        <p className="text-sm text-ink-secondary">
          You can close your account whenever you like. Anything you already
          published stays live on eBay under your own seller account — end
          those in eBay first if you want them gone too.
        </p>
        <div>
          <Button variant="danger" onClick={start}>
            <Trash2 aria-hidden /> Delete my account
          </Button>
        </div>
      </div>

      <Dialog open={open} onClose={() => !deleting && setOpen(false)} title="Delete your account?">
        <div className="flex flex-col gap-4">
          <p className="text-sm text-ink-secondary">
            This permanently erases <strong className="text-ink">{user?.email}</strong>
            {notice.listings ? (
              <>, <strong className="text-ink">
                {notice.listings} listing{notice.listings === 1 ? "" : "s"}
              </strong> and every photo on them</>
            ) : (
              <> and everything saved to it</>
            )}
            {notice.ebayConnected ? ", and disconnects your eBay account." : "."}
            {" "}It can&rsquo;t be undone.
          </p>

          {/* Never let a DB hiccup — or a summary call that never landed —
              hide this. See lib/deleteAccount: only a count that was actually
              read may be named, and everything else warns. */}
          {notice.warning && (
            <p className="text-sm rounded-tile border border-warning/30 bg-warning-soft p-3 text-ink">
              <AlertTriangle size={15} className="inline mr-1.5 -mt-0.5" aria-hidden />
              {notice.warning.kind === "unknown" ? (
                <>Any listing you already published stays live on eBay under your
                  own seller account and keeps selling — deleting here only removes
                  Thryft Shop&rsquo;s copy. End them in eBay first if you want them
                  taken down.</>
              ) : (
                <>{notice.warning.count} of your listings
                  {" "}{notice.warning.count === 1 ? "is" : "are"} live on eBay.
                  {" "}{notice.warning.count === 1 ? "It stays" : "They stay"} up
                  and {notice.warning.count === 1 ? "keeps" : "keep"} selling — deleting
                  here only removes Thryft Shop&rsquo;s copy. End
                  {" "}{notice.warning.count === 1 ? "it" : "them"} in eBay first if
                  you want {notice.warning.count === 1 ? "it" : "them"} taken down.</>
              )}
            </p>
          )}

          <Field label="Confirm your password" help="So a stray tap or a borrowed phone can't do this.">
            <Input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); setError(""); }}
              onKeyDown={(e) => { if (e.key === "Enter" && password && !deleting) remove(); }}
            />
          </Field>

          {error && <p className="text-sm text-error">{error}</p>}

          <div className="flex flex-wrap gap-2.5 justify-end">
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={deleting}>
              Keep my account
            </Button>
            <Button variant="danger" onClick={remove} loading={deleting} disabled={!password}>
              Delete permanently
            </Button>
          </div>
        </div>
      </Dialog>
    </>
  );
}
