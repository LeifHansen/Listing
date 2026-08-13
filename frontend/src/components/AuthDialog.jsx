import { useState } from "react";
import { cn } from "@/lib/utils";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/fields";
import { BrandMark } from "@/components/BrandMark";

// Log in / sign up. On success, resumes whatever action prompted the login
// (e.g. Shop-mode "Buy").
export function AuthDialog() {
  const { authOpen, setAuthOpen, setUser, loadEbayStatus, afterLogin } = useApp();
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const close = () => { setAuthOpen(false); setError(""); };

  const submit = async () => {
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await postJson(`/api/auth/${mode === "login" ? "login" : "signup"}`, {
        email: email.trim(), password,
      });
      setUser(res.user);
      setEmail("");
      setPassword("");
      setAuthOpen(false);
      loadEbayStatus();
      // Resume the action that was interrupted by the login prompt.
      const resume = afterLogin.current;
      afterLogin.current = null;
      if (resume) resume();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={authOpen}
      onClose={close}
      title={
        <span className="inline-flex items-center gap-2">
          <BrandMark className="size-8 rounded-[11px]" />
          Thryft <span className="text-blue -ml-1">Shop</span>
        </span>
      }
    >
      <div className="flex gap-1 p-1 bg-bg-sunken rounded-button mb-5" role="tablist" aria-label="Log in or sign up">
        {["login", "signup"].map((m) => (
          <button
            key={m}
            type="button"
            role="tab"
            aria-selected={mode === m}
            onClick={() => { setMode(m); setError(""); }}
            className={cn(
              "flex-1 h-10 rounded-[11px] text-sm font-semibold transition-colors duration-150 cursor-pointer",
              mode === m ? "bg-card text-ink shadow-card" : "text-ink-secondary hover:text-ink",
            )}
          >
            {m === "login" ? "Log in" : "Sign up"}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-4">
        <Field label="Email">
          <Input
            type="email" autoComplete="email" placeholder="you@email.com"
            value={email} onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Password" hint="(min 8 chars)" error={error || undefined}>
          <Input
            type="password"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
        </Field>
        <Button variant="primary" size="lg" className="w-full" onClick={submit} loading={busy}>
          {mode === "login" ? "Log in" : "Create account"}
        </Button>
        {/* Shown in both modes: these links are the only way to reach the
            policies from the native app, which has no address bar. */}
        <p className="text-xs text-ink-secondary text-center text-balance">
          {mode === "signup" ? "By creating an account you agree to our " : "See our "}
          <a className="underline underline-offset-2 hover:text-ink"
             href="/terms" target="_blank" rel="noreferrer">terms</a>
          {" and "}
          <a className="underline underline-offset-2 hover:text-ink"
             href="/privacy-policy" target="_blank" rel="noreferrer">privacy policy</a>.
          {mode === "signup" && " You can delete your account any time from Settings."}
        </p>
      </div>
    </Dialog>
  );
}
