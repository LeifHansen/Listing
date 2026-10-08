import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, RefreshCw, Trash2 } from "lucide-react";
import { api, patchJson, postJson } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select, Toggle } from "@/components/ui/fields";
import { TagPill } from "@/components/ui/badges";
import { EmptyState } from "@/components/ui/EmptyState";
import { SiteLink } from "@/components/ui/SiteLink";
import { useToast } from "@/components/ui/Toaster";
import { PanelUnavailable } from "./PanelUnavailable";

// --- Teach the AI: the reference links an expert reads ----------------------
//
// A reference link is how a seller teaches one of the AI's experts without a
// deploy: paste a URL — a catalogue raisonné, a Levi's dating chart, a glass
// mark reference — write a line about what it is for, and the expert reads it
// on every draft it applies to.
//
// The card makes ONE distinction visible, because it is the one that decides
// what the feature can and cannot do: the NOTE is the seller's instruction,
// and the SUMMARY is what the AI read off somebody else's web page and treats
// as evidence. Sellers who understand that write better notes, and sellers who
// do not are the ones who would otherwise expect a linked page to be obeyed.
export function ExpertKnowledge() {
  const { toast } = useToast();
  const [state, setState] = useState(null);     // {references, cap} | {error}
  const [experts, setExperts] = useState([]);
  const [expert, setExpert] = useState("art");
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [retrying, setRetrying] = useState("");   // the id being re-read

  const load = useCallback(() => {
    api("/api/expert-knowledge")
      .then(setState)
      .catch((e) => setState({ error: e.message || "unavailable" }));
  }, []);

  useEffect(() => {
    load();
    api("/api/experts")
      .then((d) => setExperts(d.experts || []))
      .catch(() => setExperts([{ name: "art" }]));
  }, [load]);

  const add = async () => {
    setSaving(true);
    try {
      await postJson("/api/expert-knowledge", { expert, url, note });
      setUrl(""); setNote("");
      toast("Saved — reading the page now. It'll show a summary in a moment.",
            { kind: "success" });
      load();
    } catch (e) {
      toast(`Couldn't save: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  const toggle = async (row) => {
    try {
      await patchJson(`/api/expert-knowledge/${row.id}`, { enabled: !row.enabled });
      load();
    } catch (e) { toast(`Couldn't change that: ${e.message}`, { kind: "error" }); }
  };

  const remove = async (row) => {
    try {
      await api(`/api/expert-knowledge/${row.id}`, { method: "DELETE" });
      load();
    } catch (e) { toast(`Couldn't remove that: ${e.message}`, { kind: "error" }); }
  };

  // Reading a page again. Most of the reasons a reference fails are about the
  // moment rather than the page — the site was busy, it asked for consent, our
  // summariser was down — and the server retries those twice on its own. This
  // is what a seller has after that, and without it the only way to re-read a
  // page was to delete the reference and type it in again.
  const retry = async (row) => {
    setRetrying(row.id);
    try {
      await postJson(`/api/expert-knowledge/${row.id}/refresh`, {});
      toast("Reading the page again…", { kind: "success" });
      // The read happens off the request, so the row is still showing the old
      // error for a moment. Come back for it rather than claiming it's fixed.
      setTimeout(load, 4000);
    } catch (e) {
      toast(`Couldn't try again: ${e.message}`, { kind: "error" });
    } finally {
      setRetrying("");
    }
  };

  if (!state) return <div className="ai-shimmer h-16 rounded-tile mt-4" aria-hidden />;
  if (state.error) {
    return <PanelUnavailable message="Couldn’t load your references." onRetry={load} />;
  }

  const rows = state.references || [];
  const mine = rows.filter((r) => r.editable);

  return (
    <div className="flex flex-col gap-4">
      <p className="text-[13px] text-ink-secondary max-w-prose">
        Save a page you trust — a catalogue, a dating guide, a maker’s mark
        reference — and the AI reads it whenever it drafts that kind of item.
        <strong className="font-medium"> Your note is an instruction the AI
        follows. The page itself is only evidence</strong>, so nothing on it can
        change what a listing is allowed to claim or what it’s priced at.
      </p>

      <div className="flex flex-col gap-3 max-w-lg">
        <Field label="Which expert">
          <Select value={expert} onChange={(e) => setExpert(e.target.value)}>
            {experts.map((x) => (
              <option key={x.name} value={x.name}>{x.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Page address" note="https:// only.">
          <Input value={url} onChange={(e) => setUrl(e.target.value)}
                 placeholder="https://…" inputMode="url" />
        </Field>
        <Field
          label="What it’s for"
          note="In your own words — this is the part the AI treats as your instruction."
        >
          <Input value={note} onChange={(e) => setNote(e.target.value)}
                 placeholder="Use this to date Fenton glass marks." />
        </Field>
        <div>
          <Button onClick={add} disabled={saving || !url.trim() || !note.trim()}>
            {saving ? "Saving…" : "Save reference"}
          </Button>
          {state.cap ? (
            <span className="text-[12px] text-ink-secondary ml-3">
              {mine.length} of {state.cap} saved
            </span>
          ) : null}
        </div>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No references yet"
                    message="Add one above and the AI will start reading it." />
      ) : (
        <ul className="flex flex-col gap-3">
          {rows.map((row) => (
            <li key={row.id}
                className="rounded-tile border border-line p-3 flex flex-col gap-2">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <TagPill>{row.expert}</TagPill>
                    {row.scope === "global" ? (
                      <TagPill>Built in</TagPill>
                    ) : null}
                    {!row.enabled ? <TagPill>Off</TagPill> : null}
                  </div>
                  <p className="text-[13px] mt-1 break-words">{row.note}</p>
                  <SiteLink path={row.url} className="text-[12px] break-all">
                    {row.url}
                  </SiteLink>
                </div>
                {row.editable ? (
                  <div className="flex items-center gap-2 shrink-0">
                    <Toggle checked={!!row.enabled} onChange={() => toggle(row)}
                            label="On" />
                    <Button variant="ghost" onClick={() => remove(row)}
                            aria-label="Remove reference">
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                ) : null}
              </div>

              {/* What the AI actually took from the page, labelled as what it
                  is. A seller who can see the summary can tell whether the
                  link is doing anything — and a reference that silently
                  stopped working is worse than none, so the error shows in the
                  same place. */}
              {row.fetch_error ? (
                <div className="flex flex-col gap-1.5 items-start">
                  <p className="text-[12px] text-amber-700 flex items-start gap-1.5">
                    <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                    <span>{row.fetch_error}</span>
                  </p>
                  {row.editable ? (
                    <Button variant="ghost" className="text-[12px]"
                            disabled={retrying === row.id}
                            onClick={() => retry(row)}>
                      <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
                      {retrying === row.id ? "Reading…" : "Try again"}
                    </Button>
                  ) : null}
                </div>
              ) : row.distillate ? (
                <details className="text-[12px] text-ink-secondary">
                  <summary className="cursor-pointer">
                    What the AI read from this page
                  </summary>
                  <p className="mt-1.5">{row.distillate}</p>
                  <p className="mt-1.5 italic">
                    Summarised from the page. The AI treats this as evidence to
                    weigh against the photos, never as instructions.
                  </p>
                </details>
              ) : (
                <p className="text-[12px] text-ink-secondary">Reading the page…</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
