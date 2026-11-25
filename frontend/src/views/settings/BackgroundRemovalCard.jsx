import { useState } from "react";
import { Power, PowerOff } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { TagPill } from "@/components/ui/badges";
import { useToast } from "@/components/ui/Toaster";
import { useLocalCutout } from "@/lib/bgRemoval/useLocalCutout";
import { OnDeviceCutout } from "./OnDeviceCutout";

const PHOTOROOM_URL = "https://www.photoroom.com/api";

// Background removal. First what THIS DEVICE can do by itself (free, nothing
// sent anywhere -- OnDeviceCutout), then the cloud: Photoroom, on the app's
// own account. Photoroom has no way for an app to create an account for each
// seller, so instead there is nothing to sign up for: a seller switches the
// cloud on here, once, and switching it on is where they agree to the photos
// they choose being sent to Photoroom. Until then nothing of theirs is ever
// sent there; the device still cuts for them.
export function BackgroundRemovalCard() {
  const { user, bgRemoval, loadBgRemovalStatus, tokens } = useApp();
  const local = useLocalCutout();
  const { toast, confirm } = useToast();
  const [busy, setBusy] = useState(false);
  const onDevice = local.status !== "off" && local.status !== "unknown";
  if (!user && bgRemoval.needs_activation && !onDevice) return null;

  if (!bgRemoval.available) {
    return onDevice ? <OnDeviceCutout /> : (
      <p className="text-sm text-ink-secondary">
        Background removal isn’t available on this server right now.
      </p>
    );
  }
  // Signed out with an engine on the device: what the device does needs no
  // account, the cloud switch does.
  if (!user && bgRemoval.needs_activation) return <OnDeviceCutout />;

  const cost = tokens?.costs?.image_ai;
  const costLine = tokens?.enabled && cost
    ? ` Each photo costs ${cost} token${cost === 1 ? "" : "s"}, and a photo that can’t be cut out is refunded.`
    : "";

  const activate = async () => {
    if (!(await confirm({
      title: "Turn on background removal?",
      message: "When you choose to remove a photo’s background, that photo is "
        + "sent to Photoroom, our background-removal provider, which cuts the "
        + "item out and sends it back. Only the photos you choose are sent, "
        + "and nothing about your account is shared with Photoroom."
        + costLine,
      confirmLabel: "Turn on",
    }))) return;
    setBusy(true);
    try {
      await postJson("/api/bg-removal/activate", {});
      await loadBgRemovalStatus();
      toast("Background removal is on — tick “Remove background” when you upload.",
        { kind: "success" });
    } catch (e) {
      toast(`Couldn't turn on background removal: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const deactivate = async () => {
    if (!(await confirm({
      title: "Turn off background removal?",
      message: "No more photos are sent to Photoroom. Photos already cut out "
        + "stay as they are.",
      confirmLabel: "Turn off",
      danger: true,
    }))) return;
    setBusy(true);
    try {
      await postJson("/api/bg-removal/deactivate", {});
      await loadBgRemovalStatus();
      toast("Background removal is off.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't turn it off: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const poweredBy = (
    <p className="text-xs text-ink-faint">
      Powered by{" "}
      <a href={PHOTOROOM_URL} target="_blank" rel="noreferrer"
        className="text-blue font-semibold hover:underline">Photoroom</a>.
    </p>
  );

  if (bgRemoval.active) {
    return (
      <div className="flex flex-col gap-3">
        <OnDeviceCutout />
        {onDevice && <p className="font-semibold text-ink">In the cloud</p>}
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="font-semibold text-ink flex items-center gap-2">
              On
              {bgRemoval.sandbox && (
                <TagPill tone="yellow" title="The server is using Photoroom's test key, which watermarks every cutout">
                  Test mode
                </TagPill>
              )}
            </p>
            {bgRemoval.limit > 0 && (
              <p className="text-sm text-ink-secondary" data-testid="bg-usage">
                {bgRemoval.used} of {bgRemoval.limit} photos this month
                {bgRemoval.resets_on ? ` — resets ${bgRemoval.resets_on}` : ""}.
              </p>
            )}
          </div>
          {bgRemoval.needs_activation && (
            <Button variant="danger" onClick={deactivate} loading={busy}>
              <PowerOff aria-hidden /> Turn off
            </Button>
          )}
        </div>
        {poweredBy}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <OnDeviceCutout />
      {onDevice && (
        <p className="font-semibold text-ink -mb-2">In the cloud</p>
      )}
      <p className="text-sm text-ink-secondary">
        Cut the background out of your photos and put the item on clean white —
        the look eBay recommends. Turn it on once here; then tick “Remove
        background” on any upload, or use Remove BG in the photo editor.
        There’s no separate account to create.{costLine}
      </p>
      <div>
        <Button variant="primary" onClick={activate} loading={busy}>
          <Power aria-hidden /> Turn on background removal
        </Button>
      </div>
      {poweredBy}
    </div>
  );
}
