/* The AI consent names who the photos actually go to.
 *
 * Apple's guideline 5.1.2(i) is the reason the dialog exists: explicit
 * permission before personal data reaches a third-party AI, which means naming
 * that third party. The dialog said Anthropic, always — while the identify pass
 * sends the photos to Google's Gemini whenever the server has a Google key.
 */
import { describe, expect, it } from "vitest";
import { aiRecipients } from "@/components/AiConsentDialog";

const names = (health) => aiRecipients(health).map((r) => r.name);

describe("who the consent names", () => {
  it("names Google when the server identifies on Gemini", () => {
    expect(names({ google_ai_configured: true, anthropic_configured: false }))
      .toEqual(["Google"]);
  });

  it("names both when both are configured", () => {
    expect(names({ google_ai_configured: true, anthropic_configured: true }))
      .toEqual(["Google", "Anthropic"]);
  });

  it("names Anthropic on a Claude-only server", () => {
    expect(names({ google_ai_configured: false, anthropic_configured: true }))
      .toEqual(["Anthropic"]);
  });

  it("never names nobody, even before the health read lands", () => {
    expect(names(undefined)).toEqual(["Anthropic"]);
    expect(names({})).toEqual(["Anthropic"]);
  });
});
