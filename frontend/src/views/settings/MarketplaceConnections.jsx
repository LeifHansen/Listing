import { useState } from "react";
import { AlertTriangle, Clock, Link2, Unlink } from "lucide-react";
import { postJson, startConnect } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Field, Select } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { useToast } from "@/components/ui/Toaster";

// Cross-posting marketplaces — every registered marketplace except eBay
// (which has its own section at the top). One block per marketplace:
// connected → account + Disconnect; the marketplace hasn't cleared this
// seller's shop yet (Etsy's app-tier wall) → a "Pending approval" pill and
// the wait explained, with the Connect button held back rather than walking
// them into the marketplace's own error page; configured but not connected
// → Connect button; coming soon (credentials pending on the marketplace's
// side) → a "Coming soon" pill; not configured on the server → the same
// missing-env explainer the eBay card uses. Renders nothing while eBay is
// the only marketplace registered.
export function MarketplaceConnections() {
  const { marketplaces, loadMarketplaces } = useApp();
  const { toast, confirm } = useToast();
  // Marketplaces you can't act on yet sink below the ones you can, whether
  // the wait is on their credentials or on them approving us for other shops.
  const waiting = (m) => (m.coming_soon || m.access_pending ? 1 : 0);
  const others = marketplaces
    .filter((m) => m.key !== "ebay")
    .slice()
    .sort((a, b) => waiting(a) - waiting(b));
  if (!others.length) return null;

  // Connecting, with one stop on the way out. When the marketplace still
  // restricts who may authorize and nothing on the server knows whether THIS
  // seller is allowed (m.access_unverified — Etsy before Commercial Access,
  // with no roster configured), the refusal happens on the marketplace's own
  // page after we have handed the browser over, and nothing comes back for us
  // to explain. So the explaining happens here, while we still have them.
  // They are let through either way: on a deployment with no roster the
  // person pressing Connect is usually the one account that works, and
  // turning them away would be the worse guess.
  const connect = async (m) => {
    if (m.access_unverified && !(await confirm({
      title: `${m.label} may not let you connect yet`,
      message: m.access_unverified_note
        || `${m.label} only lets certain accounts authorize this app, and we can't tell from here whether yours is one. You can try — if ${m.label} turns you away, it's their restriction, not a problem with your shop.`,
      confirmLabel: `Continue to ${m.label}`,
    }))) return;
    try {
      await startConnect(`/api/${m.key}/connect`);
    } catch (e) {
      toast(`Couldn't open the connect screen: ${e.message}`, { kind: "error" });
    }
  };

  const disconnect = async (m) => {
    if (!(await confirm({
      title: `Disconnect ${m.label}?`,
      message: "Cross-posting there stops until you reconnect. Your saved defaults are kept.",
      confirmLabel: "Disconnect",
      danger: true,
    }))) return;
    try {
      await postJson(`/api/${m.key}/disconnect`, {});
      await loadMarketplaces();
      toast(`${m.label} disconnected.`, { kind: "success" });
    } catch (e) {
      toast(`Couldn't disconnect: ${e.message}`, { kind: "error" });
    }
  };

  return (
    <div className="flex flex-col">
      {others.map((m, i) => (
        <div key={m.key} className={i > 0 ? "mt-6 pt-6 border-t border-line" : ""}>
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="font-semibold text-ink flex items-center gap-2">
                {m.label}
                {!m.connected && (m.coming_soon || m.access_pending) && (
                  <TagPill tone="blue">
                    <Clock size={11} aria-hidden />{" "}
                    {m.access_pending ? "Pending approval" : "Coming soon"}
                  </TagPill>
                )}
              </p>
              {m.connected && m.needs_reconnect ? (
                <p className="text-sm text-ink-secondary">
                  Connected, but {m.label} didn’t tell us which shop — reconnect
                  to finish linking it.
                </p>
              ) : m.connected ? (
                <p className="text-sm text-ink-secondary">
                  Connected{m.username ? (
                    <> as <strong className="text-ink">{m.username}</strong></>
                  ) : null}.
                </p>
              ) : m.access_pending ? (
                <p className="text-sm text-ink-secondary">
                  {m.access_pending_note
                    || `${m.label} hasn’t opened this app up to your shop yet — cross-posting turns on as soon as they do.`}
                </p>
              ) : m.oauth_ready ? (
                <p className="text-sm text-ink-secondary">
                  Not connected yet — link your {m.label} account to cross-post listings.
                </p>
              ) : m.coming_soon ? (
                <p className="text-sm text-ink-secondary">
                  {m.coming_soon_note
                    || `${m.label} access is pending — cross-posting turns on as soon as it's approved.`}
                </p>
              ) : (
                <p className="text-sm text-ink-secondary">
                  Not set up on the server yet.
                </p>
              )}
            </div>
            {m.connected && m.needs_reconnect && m.oauth_ready ? (
              // Half-linked: a token is stored but the shop behind it is
              // not, so reconnecting is the fix and leads. Disconnect sits
              // BESIDE it rather than being replaced by it — getting out is
              // the one thing a stuck connection must always allow.
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={() => connect(m)}>
                  <Link2 aria-hidden /> Reconnect {m.label}
                </Button>
                <Button variant="danger" onClick={() => disconnect(m)}>
                  <Unlink aria-hidden /> Disconnect
                </Button>
              </div>
            ) : m.connected ? (
              <Button variant="danger" onClick={() => disconnect(m)}>
                <Unlink aria-hidden /> Disconnect
              </Button>
            ) : m.access_pending ? (
              <Button variant="secondary" disabled>
                <Clock aria-hidden /> Connect {m.label}
              </Button>
            ) : m.oauth_ready ? (
              <Button variant="primary" onClick={() => connect(m)}>
                <Link2 aria-hidden /> Connect {m.label}
              </Button>
            ) : m.coming_soon ? (
              <Button variant="secondary" disabled>
                <Clock aria-hidden /> Connect {m.label}
              </Button>
            ) : null}
          </div>
          {!m.connected && !m.oauth_ready && !m.coming_soon && (
            <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 flex gap-3 mt-3">
              <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <div className="text-sm min-w-0">
                <p className="font-bold text-ink">
                  “Sign in with {m.label}” isn’t set up on the server
                </p>
                <p className="text-ink-secondary mt-0.5">
                  The Connect button can’t do anything until{" "}
                  {(m.oauth_missing || []).length === 1 ? "this variable is" : "these variables are"} set
                  on the deployment:{" "}
                  {(m.oauth_missing || []).map((name, j) => (
                    <span key={name}>
                      {j > 0 && ", "}
                      <code className="text-ink font-semibold">{name}</code>
                    </span>
                  ))}
                  {" "}(e.g. <code className="text-ink font-semibold">fly secrets set …</code>).
                </p>
              </div>
            </div>
          )}
          {!m.connected && m.access_unverified && (
            <div className="rounded-tile bg-warning-soft border border-warning/30 p-4 flex gap-3 mt-3">
              <AlertTriangle size={18} className="text-warning shrink-0 mt-0.5" aria-hidden />
              <div className="text-sm min-w-0">
                <p className="font-bold text-ink">
                  {m.label} decides whether this connect goes through
                </p>
                <p className="text-ink-secondary mt-0.5">
                  {m.access_unverified_note
                    || `${m.label} only lets certain accounts authorize this app, and we can’t tell from here whether yours is one. Pressing Connect is how you find out — ${m.label} answers on its own page.`}
                </p>
              </div>
            </div>
          )}
          {m.key === "etsy" && m.connected && <EtsyDefaults />}
        </div>
      ))}
    </div>
  );
}

// Etsy publish defaults: which shipping profile, return policy and
// processing profile new Etsy listings use (Etsy requires all three on a
// physical item). Loaded from the seller's shop; saved into the account's
// marketplace settings.
function EtsyDefaults() {
  const { toast } = useToast();
  // The shop's profiles and the saved defaults live in the store (loaded
  // once Etsy is connected), so a default saved here reaches the editor's
  // blockers and the crosspost without a reload.
  const { etsyOptions: data, loadEtsyOptions } = useApp();
  const [saving, setSaving] = useState(false);
  // What the seller has changed here, over what the shop last said: no
  // effect copying one into the other, and a reload after save shows the
  // saved defaults through the same overlay.
  const [edits, setEdits] = useState({});
  const selected = { ...((data && !data.error && data.selected) || {}), ...edits };
  const setSelected = (update) => setEdits((prev) => {
    const next = typeof update === "function" ? update({ ...selected, ...prev }) : update;
    return { ...prev, ...next };
  });

  if (!data) return <div className="ai-shimmer h-16 rounded-tile mt-4" aria-hidden />;
  if (data.error) {
    return (
      <p className="text-[13px] text-ink-secondary mt-3">
        Couldn’t load your Etsy shipping, return and processing options — retry
        from Settings after reconnecting Etsy.
      </p>
    );
  }

  const save = async () => {
    setSaving(true);
    try {
      await postJson("/api/etsy/settings-options", selected);
      await loadEtsyOptions();
      setEdits({});
      toast("Etsy defaults saved — new Etsy listings will use them.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't save: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 max-w-lg mt-4">
      <Field
        label="Shipping profile"
        note={(data.shipping_profiles || []).length
          ? "Etsy requires a shipping profile on every physical listing."
          : "No shipping profiles on your Etsy shop yet — create one on Etsy, then reopen Settings."}
      >
        <Select
          value={selected.shipping_profile_id || ""}
          onChange={(e) => setSelected((s) => ({ ...s, shipping_profile_id: e.target.value }))}
        >
          <option value="">— none —</option>
          {(data.shipping_profiles || []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
      </Field>
      <Field
        label="Return policy"
        note={(data.return_policies || []).length
          ? undefined
          : "No return policies on your Etsy shop yet — Etsy adds one the first time you set returns up in Shop Manager."}
      >
        <Select
          value={selected.return_policy_id || ""}
          onChange={(e) => setSelected((s) => ({ ...s, return_policy_id: e.target.value }))}
        >
          <option value="">— none —</option>
          {(data.return_policies || []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
      </Field>
      <Field
        label="Processing time"
        note={(data.readiness_states || []).length
          ? "Etsy requires a processing profile on every physical listing — how long an order takes you to ship."
          : "No processing profiles on your Etsy shop yet — add one under Shop Manager → Settings → Shipping, then reopen Settings."}
      >
        <Select
          value={selected.readiness_state_id || ""}
          onChange={(e) => setSelected((s) => ({ ...s, readiness_state_id: e.target.value }))}
        >
          <option value="">— none —</option>
          {(data.readiness_states || []).map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </Select>
      </Field>
      <div>
        <Button variant="secondary" onClick={save} loading={saving}>
          Save Etsy defaults
        </Button>
      </div>
    </div>
  );
}
