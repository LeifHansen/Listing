import { SiteLink } from "@/components/ui/SiteLink";

// The app has no address bar, so these are the only way to reach the policies
// from inside it — which Apple requires and which is just good manners.
export function LegalLinks() {
  const link = "text-ink-secondary hover:text-ink underline underline-offset-2";
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-secondary px-1">
      <SiteLink path="/privacy-policy" className={link}>Privacy policy</SiteLink>
      <SiteLink path="/terms" className={link}>Terms of service</SiteLink>
      <SiteLink path="/about" className={link}>About</SiteLink>
      <a className={link} href="mailto:leifhansen1990@gmail.com">Support</a>
    </p>
  );
}
