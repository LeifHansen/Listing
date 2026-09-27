import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { SiteLink } from "@/components/ui/SiteLink";
import { useApp } from "@/store";

/* Who the photos actually go to, in the words the consent has to use. Read
 * off the server's own capability flags rather than written down here: the
 * identify pass runs on Google's Gemini whenever GOOGLE_API_KEY is set (see
 * config.identify_provider), and a consent that names a recipient the photos
 * do not go to -- or leaves out one they do -- is not the permission
 * guideline 5.1.2(i) asks for. Exported for the test. */
export function aiRecipients(health) {
  const out = [];
  if (health?.google_ai_configured) {
    out.push({ name: "Google", what: "the maker of the Gemini AI" });
  }
  // Anthropic unless the server says it has only Google: the Claude passes
  // (research, refine) run whenever its key is set, and a health read that
  // has not landed yet must not produce a consent naming nobody.
  if (health?.anthropic_configured || !out.length) {
    out.push({ name: "Anthropic", what: "the maker of the Claude AI" });
  }
  return out;
}

/* One-time consent before the first photo is analyzed by AI.
 *
 * Apple's guideline 5.1.2(i) requires explicit permission BEFORE personal
 * data (photos can show people, homes, addresses) is shared with a
 * third-party AI — disclosure in the privacy policy alone isn't enough.
 * The gate lives in lib/api.js on every photo-AI endpoint; this dialog is
 * its UI. Agreeing is remembered on the device, so it appears exactly once.
 */
export function AiConsentDialog() {
  const { health } = useApp();
  const [pending, setPending] = useState(null); // {accept, decline} | null
  const recipients = aiRecipients(health);

  useEffect(() => {
    const onNeeded = (e) => {
      // Marked synchronously, inside the dispatch, so lib/aiConsent can tell
      // "the dialog took this" from "nobody was listening". Without it, an
      // ask that reached no one left the upload waiting forever.
      e.detail.shown = true;
      setPending(e.detail);
    };
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
          {recipients.map((r, i) => (
            <span key={r.name}>
              {i > 0 && " and "}
              <strong className="text-ink">{r.name}</strong> ({r.what})
            </span>
          ))}{" "}
          for analysis. They're used only to create your listing —
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
