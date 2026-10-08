import { useState } from "react";
import { Link2, Unlink } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { useToast } from "@/components/ui/Toaster";

// Shipping labels — the seller's own EasyPost account. Not a marketplace
// (nothing is listed there), so it does not belong in the cross-posting
// roster; it ships the eBay orders. The key is verified with one EasyPost
// read before it is stored, stored encrypted, and only ever shown back as
// its last four characters.
export function EasyPostCard() {
  const { user, easypost, loadEasypostStatus } = useApp();
  const { toast, confirm } = useToast();
  const [apiKey, setApiKey] = useState("");
  const [connecting, setConnecting] = useState(false);
  if (!user) return null;

  const connect = async () => {
    const key = apiKey.trim();
    if (!key) { toast("Paste your EasyPost API key first.", { kind: "warning" }); return; }
    setConnecting(true);
    try {
      const res = await postJson("/api/easypost/connect", { api_key: key });
      setApiKey("");
      await loadEasypostStatus();
      toast(res.test
        ? "EasyPost connected in test mode — labels are free samples until you switch to a production key."
        : "EasyPost connected — sold items can be shipped from the app now.",
      { kind: "success" });
    } catch (e) {
      toast(`Couldn't connect EasyPost: ${e.message}`, { kind: "error" });
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = async () => {
    if (!(await confirm({
      title: "Disconnect EasyPost?",
      message: "Buying labels from the app stops until you connect a key again. Labels already bought keep their tracking.",
      confirmLabel: "Disconnect",
      danger: true,
    }))) return;
    try {
      await postJson("/api/easypost/disconnect", {});
      await loadEasypostStatus();
      toast("EasyPost disconnected.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't disconnect: ${e.message}`, { kind: "error" });
    }
  };

  if (easypost.connected) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-ink flex items-center gap-2">
            Connected
            {easypost.test && (
              <TagPill tone="yellow" title="Test keys buy free sample labels that carriers won't accept">
                Test mode
              </TagPill>
            )}
          </p>
          <p className="text-sm text-ink-secondary">
            Key ending in <span className="font-mono text-ink">…{easypost.key_hint}</span>.
            {easypost.test
              ? " Swap in your production key (EZAK…) when you're ready to buy real postage."
              : " Sold items can be shipped from the Listings page and from sold notifications."}
          </p>
        </div>
        <Button variant="danger" onClick={disconnect}>
          <Unlink aria-hidden /> Disconnect
        </Button>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-ink-secondary">
        Buy discounted USPS and UPS labels for sold items — postage is charged to
        your own EasyPost account. Create a free account at{" "}
        <a href="https://www.easypost.com" target="_blank" rel="noreferrer"
          className="text-blue font-semibold hover:underline">easypost.com</a>,
        add a payment method, then copy an API key from Account Settings → API
        Keys. A test key (EZTK…) buys free sample labels for trying this out; a
        production key (EZAK…) buys real postage.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-3 items-end">
        <Field label="EasyPost API key">
          <Input type="password" autoComplete="off" spellCheck={false}
            placeholder="EZAK…" value={apiKey}
            onChange={(e) => setApiKey(e.target.value)} />
        </Field>
        <Button variant="primary" onClick={connect} loading={connecting}>
          <Link2 aria-hidden /> Connect EasyPost
        </Button>
      </div>
    </div>
  );
}
