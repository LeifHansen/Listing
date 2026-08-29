import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { SiteLink } from "@/components/ui/SiteLink";

/* One-time consent before the first photo is analyzed by AI.
 *
 * Apple's guideline 5.1.2(i) requires explicit permission BEFORE personal
 * data (photos can show people, homes, addresses) is shared with a
 * third-party AI — disclosure in the privacy policy alone isn't enough.
 * The gate lives in lib/api.js on every photo-AI endpoint; this dialog is
 * its UI. Agreeing is remembered on the device, so it appears exactly once.
 */
export function AiConsentDialog() {
  const [pending, setPending] = useState(null); // {accept, decline} | null

  useEffect(() => {
    const onNeeded = (e) => setPending(e.detail);
    window.addEventListener("ai-consent:needed", onNeeded);
    return () => window.removeEventListener("ai-consent:needed", onNeeded);
  }, []);

  const answer = (ok) => {
    if (!pending) return;
    (ok ? pending.accept : pending.decline)();
    setPending(null);
  };

  return (
    <Dialog open={!!pending} onClose={() => answer(false)} title="How the AI sees your photos">
      <div className="flex flex-col gap-4">
        <p className="text-sm text-ink-secondary leading-relaxed">
          <Sparkles size={15} className="inline mr-1 text-blue" aria-hidden />
          To identify your item and write the listing, your photos are sent to{" "}
          <strong className="text-ink">Anthropic</strong> (the maker of the
          Claude AI) for analysis. They're used only to create your listing —
          never for ads, and never shared beyond the marketplaces you choose
          to publish to. Details are in our{" "}
          <SiteLink path="/privacy-policy"
            className="text-blue underline">privacy policy</SiteLink>.
        </p>
        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => answer(false)}>Not now</Button>
          <Button variant="primary" onClick={() => answer(true)}>
            Agree and continue
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
