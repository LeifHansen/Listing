import { useEffect, useState } from "react";
import { LogIn, Settings as SettingsIcon } from "lucide-react";
import { useApp } from "@/store";
import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/EmptyState";
import { AccountIllustration } from "@/components/ui/illustrations";
import { SettingsContext } from "./context";
import { useSettingsData } from "./useSettingsData";
import { JumpNav } from "./JumpNav";
import { SettingsSection } from "./SettingsSection";
import { EbayAccountSection } from "./EbayAccountSection";
import { ShippingSection } from "./ShippingSection";
import { ReturnsPaymentSection } from "./ReturnsPaymentSection";
import { SellingDefaultsSection } from "./SellingDefaultsSection";
import { MoreSection, MORE_ROWS } from "./MoreSection";
import { DeleteAccountCard } from "./DeleteAccountCard";
import { LegalLinks } from "./LegalLinks";

// The groups, in the order eBay's own Seller Hub teaches: the account,
// then the policies every listing publishes under, then what this app adds
// on top. The jump nav lists them; deep links scroll to them.
const SECTIONS = [
  { id: "ebay-account", label: "eBay account" },
  { id: "shipping", label: "Shipping" },
  { id: "returns-payment", label: "Returns & payment" },
  { id: "selling-defaults", label: "Selling defaults" },
  { id: "more", label: "More" },
  { id: "danger", label: "Danger zone" },
];

/**
 * Settings — the eBay account, the defaults applied to every publish, and
 * (folded away) everything else.
 *
 * Every control saves on its own: switches and selects the moment they
 * change, typed fields with a Save on their card. There is no page-wide
 * Save, because there were two systems behind it (this app's defaults and
 * the seller's eBay account) and one button could not say honestly which
 * half had committed.
 */
export function SettingsView() {
  const { user, openAuth, settingsSection, clearSettingsSection } = useApp();
  const settings = useSettingsData();
  // Which "More" row a deep link asked for. Captured before first paint so
  // the row renders open rather than opening after the scroll, and followed
  // when a link arrives while the page is already up (the documented
  // "adjust state when a prop changes" pattern, not an effect).
  const [openRow, setOpenRow] = useState(
    () => (MORE_ROWS.includes(settingsSection) ? settingsSection : null));
  const [prevSection, setPrevSection] = useState(settingsSection);
  if (settingsSection !== prevSection) {
    setPrevSection(settingsSection);
    if (MORE_ROWS.includes(settingsSection)) setOpenRow(settingsSection);
  }

  // A deep link lands on its section. After App.jsx's own scroll-to-top for
  // the view change, and only once — the request is cleared so the next
  // plain visit lands at the top again. Not before the account has loaded:
  // until then the page is the "Log in first" card and the sections do not
  // exist, so a link consumed at that moment would scroll to nothing and be
  // gone by the time they render. `user` in the deps re-runs it when they do.
  useEffect(() => {
    if (!settingsSection || !user) return undefined;
    const raf = requestAnimationFrame(() => {
      const el = document.getElementById(`settings-${settingsSection}`);
      if (el) {
        try {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch {
          el.scrollIntoView();
        }
      }
      clearSettingsSection();
    });
    return () => cancelAnimationFrame(raf);
  }, [settingsSection, user, clearSettingsSection]);

  if (!user) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader icon={SettingsIcon} title="Settings"
          subtitle="Your eBay connection and the defaults applied to every publish." />
        <Card className="p-0">
          <EmptyState
            illustration={AccountIllustration}
            title="Log in first"
            message="Your eBay connection and listing defaults live on your account."
            action={
              <Button variant="primary" size="lg" onClick={() => openAuth()}>
                <LogIn aria-hidden /> Log in
              </Button>
            }
          />
        </Card>
        {/* Logged out too: in the native app there's no address bar, so this
            is the only route to the policies. */}
        <LegalLinks />
      </div>
    );
  }

  return (
    <SettingsContext.Provider value={settings}>
      <div className="flex flex-col gap-5">
        <PageHeader icon={SettingsIcon} title="Settings"
          subtitle="Your eBay connection and the defaults applied to every publish. Changes save as you make them." />
        <JumpNav sections={SECTIONS} />

        <div className="flex flex-col gap-8 mt-1">
          <SettingsSection
            id="ebay-account" title="eBay account"
            description="Where every listing publishes, and what eBay says about the account."
          >
            <EbayAccountSection />
          </SettingsSection>

          <SettingsSection
            id="shipping" title="Shipping"
            description="Where items ship from, which policy they publish under, and how fast you promise to send them."
          >
            <ShippingSection />
          </SettingsSection>

          <SettingsSection
            id="returns-payment" title="Returns & payment"
            description="The policies buyers see on every listing."
          >
            <ReturnsPaymentSection />
          </SettingsSection>

          <SettingsSection
            id="selling-defaults" title="Selling defaults"
            description="Applied to every new listing you publish. Offers, promotion, pricing, condition and quantity."
          >
            <SellingDefaultsSection />
          </SettingsSection>

          <SettingsSection
            id="more" title="More"
            description="Profile, shipping labels, cross-posting, AI references, appearance and sign-in."
          >
            <MoreSection openRow={openRow} />
          </SettingsSection>

          <SettingsSection
            id="danger" title="Danger zone"
            description="Deleting here removes this app’s copy only — listings already on eBay stay live."
          >
            <DeleteAccountCard />
          </SettingsSection>
        </div>

        <LegalLinks />
      </div>
    </SettingsContext.Provider>
  );
}
