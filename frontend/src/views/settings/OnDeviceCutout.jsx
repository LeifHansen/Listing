import { useState } from "react";
import { Download, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { REASONS } from "@/lib/bgRemoval/errors";
import { prepareLocal } from "@/lib/bgRemoval/service";
import { useLocalCutout } from "@/lib/bgRemoval/useLocalCutout";

// What this phone or browser can do about backgrounds by itself: free, and
// nothing is sent anywhere for it, so there is nothing to switch on. Shown
// above the cloud switch, which is now the fallback rather than the feature.
// Nothing at all where the server has not switched an engine on for this
// platform -- the card then reads exactly as it always has.
const ENGINE = {
  "apple-vision": "Apple’s on-device Vision",
  mlkit: "Google’s on-device ML Kit",
  "birefnet-web": "a model that runs in this browser",
};

export function OnDeviceCutout() {
  const local = useLocalCutout();
  const [busy, setBusy] = useState(false);
  if (local.status === "off" || local.status === "unknown") return null;

  const engine = ENGINE[local.provider] || "this device";
  let line;
  let action = null;
  if (local.status === "checking") {
    line = "Checking what this device can do…";
  } else if (local.status === "ready") {
    line = `Ready. Backgrounds come off right here, using ${engine} — free, and your photos aren’t sent anywhere for it.`;
  } else if (local.status === "downloading") {
    const pct = local.progress != null ? ` ${Math.round(local.progress * 100)}%` : "";
    line = `Downloading the background remover…${pct}`;
  } else if (local.status === "needs-download") {
    line = "The background remover downloads once to this device, then works offline and free.";
    action = (
      <Button variant="secondary" loading={busy}
        onClick={async () => { setBusy(true); try { await prepareLocal(); } finally { setBusy(false); } }}>
        <Download aria-hidden /> Download it now
      </Button>
    );
  } else {
    line = `${REASONS[local.reason] || REASONS.unsupported} The cloud option below still works.`;
  }
  return (
    <div className="flex flex-col gap-2" data-testid="on-device-cutout">
      <p className="font-semibold text-ink flex items-center gap-2">
        <Smartphone size={16} className="text-ink-secondary" aria-hidden /> On this device
      </p>
      <p className="text-sm text-ink-secondary">{line}</p>
      {action && <div>{action}</div>}
    </div>
  );
}
