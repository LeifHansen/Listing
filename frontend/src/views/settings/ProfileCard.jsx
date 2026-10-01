import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/fields";
import { useToast } from "@/components/ui/Toaster";

// Profile: display name (shown in greetings) + one-tap sync from eBay.
export function ProfileCard() {
  const { user, setUser, ebay } = useApp();
  const { toast } = useToast();
  const displayName = user?.display_name || "";
  const [name, setName] = useState(displayName);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState(false);

  // The field is a draft the user types into, re-seeded whenever the stored
  // display name changes underneath it — a save, an eBay sync, or signing in as
  // someone else. This is React's documented "adjust state when a prop changes"
  // pattern: compare against the previous value during render, so the input
  // never paints a frame of the old name the way an effect would.
  const [prevDisplayName, setPrevDisplayName] = useState(displayName);
  if (displayName !== prevDisplayName) {
    setPrevDisplayName(displayName);
    setName(displayName);
  }

  const save = async () => {
    setSaving(true);
    try {
      const res = await postJson("/api/profile", { display_name: name.trim() });
      setUser((u) => ({ ...u, display_name: res.user.display_name }));
      toast("Profile saved.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't save profile: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const sync = async () => {
    setSyncing(true);
    try {
      const p = await api("/api/profile/sync-ebay", { method: "POST" });
      setUser((u) => ({ ...u, display_name: p.user.display_name }));
      setName(p.user.display_name || "");
      toast("Synced from eBay — username, policies, and location pulled in.", { kind: "success" });
    } catch (e) {
      toast(`Sync failed: ${e.message}`, { kind: "error" });
    } finally {
      setSyncing(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 max-w-lg">
      <Field label="Display name" note={`How Thryft Shop greets you. Signed in as ${user?.email || ""}.`}>
        <Input
          maxLength={80}
          placeholder="e.g. your shop name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <div className="flex flex-wrap gap-2.5">
        <Button variant="primary" onClick={save} loading={saving}>Save profile</Button>
        {ebay.connected && (
          <Button variant="secondary" onClick={sync} loading={syncing}>
            <RefreshCw aria-hidden /> Sync from eBay
          </Button>
        )}
      </div>
    </div>
  );
}
