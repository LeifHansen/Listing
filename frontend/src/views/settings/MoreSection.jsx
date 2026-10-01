import { useState } from "react";
import { BookOpen, Link2, LogOut, Moon, ShieldCheck, Truck, UserRound } from "lucide-react";
import { postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Toggle } from "@/components/ui/fields";
import { useToast } from "@/components/ui/Toaster";
import { Disclosure } from "./Disclosure";
import { ProfileCard } from "./ProfileCard";
import { EasyPostCard } from "./EasyPostCard";
import { MarketplaceConnections } from "./MarketplaceConnections";
import { ExpertKnowledge } from "./ExpertKnowledge";

// The rows of the "More" section, by id, so a deep link can open one.
export const MORE_ROWS = ["profile", "easypost", "marketplaces", "teach-ai",
                          "appearance", "account"];

/**
 * Everything a seller touches at setup and rarely after, folded into one
 * row each. Keeping these closed is what lets the eBay essentials above
 * read in a single screen; a deep link (`openSettings("easypost")`) opens
 * the row it names.
 */
export function MoreSection({ openRow }) {
  const { easypost, marketplaces, user } = useApp();
  const others = (marketplaces || []).filter((m) => m.key !== "ebay");
  const connected = others.filter((m) => m.connected).map((m) => m.label);
  return (
    <div className="flex flex-col -my-2">
      <Disclosure
        id="profile" icon={UserRound} title="Profile"
        summary={user?.display_name ? `Signed in as ${user.display_name}` : "Display name and eBay sync"}
        defaultOpen={openRow === "profile"} expand={openRow === "profile"}
      >
        <ProfileCard />
      </Disclosure>
      <Disclosure
        id="easypost" icon={Truck} title="Shipping labels"
        summary={easypost?.connected
          ? `EasyPost connected${easypost.test ? " (test mode)" : ""}`
          : "Buy USPS and UPS labels through your own EasyPost account"}
        defaultOpen={openRow === "easypost"} expand={openRow === "easypost"}
      >
        <EasyPostCard />
      </Disclosure>
      {others.length > 0 && (
        <Disclosure
          id="marketplaces" icon={Link2} title="Cross-posting marketplaces"
          summary={connected.length
            ? `Connected: ${connected.join(", ")}`
            : "Post a listing to several marketplaces at once"}
          defaultOpen={openRow === "marketplaces"} expand={openRow === "marketplaces"}
        >
          <MarketplaceConnections />
        </Disclosure>
      )}
      <Disclosure
        id="teach-ai" icon={BookOpen} title="Teach the AI"
        summary="Reference pages the AI reads when it drafts a listing"
        defaultOpen={openRow === "teach-ai"} expand={openRow === "teach-ai"}
      >
        <ExpertKnowledge />
      </Disclosure>
      <Disclosure
        id="appearance" icon={Moon} title="Appearance"
        summary="Light or dark"
        defaultOpen={openRow === "appearance"} expand={openRow === "appearance"}
      >
        <AppearanceRow />
      </Disclosure>
      <Disclosure
        id="account" icon={ShieldCheck} title="Sign-in and security"
        summary="Sign out of this device, or every device"
        defaultOpen={openRow === "account"} expand={openRow === "account"}
      >
        <AccountRow />
      </Disclosure>
    </div>
  );
}

// Dark mode used to live only in the desktop sidebar, which the phone shell
// never shows. Here it reaches every device.
function AppearanceRow() {
  const { dark, toggleDark } = useApp();
  return (
    <Toggle
      checked={Boolean(dark)}
      onChange={() => toggleDark()}
      label="Dark mode"
      note="Remembered on this device."
    />
  );
}

function AccountRow() {
  const { logout } = useApp();
  const { toast, confirm } = useToast();
  const [signingOutAll, setSigningOutAll] = useState(false);
  return (
    <div className="flex flex-col gap-4 max-w-lg">
      <div>
        <Button variant="secondary" onClick={() => logout()}>
          <LogOut aria-hidden /> Log out
        </Button>
      </div>
      {/* Signing out of a browser does not cancel the token it was using:
          the session token is self-contained and good for 30 days, so a
          borrowed phone, a machine left signed in, or a token out of a
          backup keeps working. This is the control that ends them. */}
      <div className="pt-4 border-t border-line">
        <p className="text-sm text-ink-secondary">
          Signed in somewhere you shouldn’t be — a shared computer, a phone you
          no longer have? This ends every signed-in device, including this one.
        </p>
        <Button
          variant="secondary" className="mt-2.5" loading={signingOutAll}
          onClick={async () => {
            if (!(await confirm({
              title: "Sign out everywhere?",
              message: "Every device signed in to this account is signed out, "
                + "including this one. Nothing else changes — your listings, "
                + "photos and eBay connection stay exactly as they are.",
              confirmLabel: "Sign out everywhere",
            }))) return;
            setSigningOutAll(true);
            try {
              const r = await postJson("/api/auth/logout-everywhere", {});
              toast(r.message, { kind: "success" });
              // Locally too. The server has already cancelled this token, so
              // leaving the app looking signed in would just fail the next
              // request with no explanation.
              logout();
            } catch (e) {
              // Never a shrug: if the revocation did not commit, the other
              // sessions are still live and the seller has to know that.
              toast(`Couldn't sign out everywhere: ${e.message}`, { kind: "error" });
            } finally {
              setSigningOutAll(false);
            }
          }}
        >
          <LogOut aria-hidden /> Sign out everywhere
        </Button>
      </div>
    </div>
  );
}
