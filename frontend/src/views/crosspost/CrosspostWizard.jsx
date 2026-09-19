/* Crosspost to Etsy — the listings a seller ticked in the manager, onto
   their Etsy shop.

   Four steps, and the order is the point. PICK says which of the ticked
   listings can go and why the rest can't. DEFAULTS asks the three things
   Etsy wants that eBay never did, once for the whole batch, because they
   are a policy attestation rather than a field: Etsy takes handmade,
   vintage (20+ years) and craft supplies, and nothing else. REVIEW shows
   what each listing will actually become — the tidied title, the category
   and where it was found, the age read off the item's own details — with
   what Etsy would still refuse it over. RUN sends them one at a time,
   behind a progress bar with a Stop.

   Drafts by default: a listing that goes live on Etsy is charged a listing
   fee, and a seller crossposting a store for the first time should see
   what arrived before paying for two hundred of them. */
import { useCallback, useMemo, useState } from "react";
import {
  AlertTriangle, ArrowRightLeft, CheckCircle2, ExternalLink, Loader2, Sparkles,
} from "lucide-react";
import { api, pollJob, postJson } from "@/lib/api";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Dialog } from "@/components/ui/Dialog";
import { Button } from "@/components/ui/Button";
import { Field, Input, Select } from "@/components/ui/fields";
import { cn } from "@/lib/utils";
import { candidates, tally } from "@/lib/crosspost";
import { ETSY_VINTAGE_YEARS } from "@/views/listing/blockers";
import { WHEN_MADE_OPTIONS, WHO_MADE_OPTIONS } from "@/views/listing/cards";

const STEPS = ["pick", "defaults", "review", "run"];

// What the run reports per listing, in the seller's words.
const OUTCOME = {
  published: { tone: "success", label: "Live on Etsy" },
  draft: { tone: "success", label: "Draft created on Etsy" },
  refused: { tone: "warning", label: "Etsy refused it" },
  unknown: { tone: "warning", label: "Check your Etsy drafts" },
  skipped: { tone: "muted", label: "Skipped" },
  queued: { tone: "muted", label: "Waiting" },
  done: { tone: "success", label: "Sent" },
};

function Row({ children, className }) {
  return (
    <li className={cn("py-3 flex flex-wrap items-start gap-3", className)}>{children}</li>
  );
}

function Chips({ items }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5 mt-1">
      {items.map((b) => (
        <span key={b.key} title={b.why}
          className="inline-flex items-center rounded-full bg-warning-soft border border-warning/40 px-2 py-0.5 text-[12px] font-bold text-warning">
          {b.label}
        </span>
      ))}
    </span>
  );
}

export function CrosspostWizard({ items, onClose }) {
  const { etsyOptions, openListing, invalidateListings } = useApp();
  const { toast } = useToast();
  const etsySettings = etsyOptions && !etsyOptions.error ? etsyOptions : null;
  const selected = (etsySettings && etsySettings.selected) || {};

  const [step, setStep] = useState("pick");
  const [mode, setMode] = useState("draft");
  const [attested, setAttested] = useState(false);
  const [defaults, setDefaults] = useState({
    who_made: "someone_else", when_made: "", is_supply: false,
    shipping_profile_id: "", return_policy_id: "", readiness_state_id: "",
  });
  const [rows, setRows] = useState(null);
  const [aiCalls, setAiCalls] = useState(0);
  const [skippedRows, setSkippedRows] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);   // {current, total, items}
  const [jobId, setJobId] = useState("");

  const picked = useMemo(() => candidates(items, { etsySettings }), [items, etsySettings]);
  const goable = picked.filter((r) => !r.skip);
  const t = tally(picked);

  const setDefault = (key, value) => setDefaults((d) => ({ ...d, [key]: value }));
  const setRow = (id, patch) => setRows((list) => list.map(
    (r) => (r.id === id ? { ...r, ...patch } : r)));

  const loadReview = useCallback(async () => {
    setBusy(true);
    try {
      const body = await postJson("/api/crosspost/etsy/review", {
        listing_ids: goable.map((r) => r.item.id),
        defaults, mode,
      });
      setRows((body.rows || []).map((r) => ({ ...r, skip: false })));
      setSkippedRows(body.skipped || []);
      setAiCalls(body.ai_calls || 0);
      setStep("review");
    } catch (e) {
      toast(`Couldn't work out what Etsy needs: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  }, [goable, defaults, mode, toast]);

  const suggestFor = async (row) => {
    setRow(row.id, { suggesting: true });
    try {
      const res = await postJson(`/api/etsy/suggest-taxonomy/${row.id}`,
        { listing: { title: row.etsy_title || row.title } });
      setRow(row.id, {
        taxonomy: { id: res.taxonomy_id, path: res.path, source: res.source || "ai" },
      });
    } catch (e) {
      toast(`Suggestion failed: ${e.message}`, { kind: "error" });
    } finally {
      setRow(row.id, { suggesting: false });
    }
  };

  const sendable = (rows || []).filter((r) => !r.skip && r.ready && r.taxonomy?.id);

  const run = async () => {
    setBusy(true);
    setStep("run");
    try {
      const started = await postJson("/api/crosspost/etsy/start", {
        mode,
        items: sendable.map((r) => ({
          id: r.id,
          etsy: { ...(r.etsy || {}), taxonomy_id: r.taxonomy.id },
          title_override: r.etsy_title !== r.title ? r.etsy_title : "",
        })),
      });
      setJobId(started.job_id);
      setProgress({ current: 0, total: sendable.length, items: [] });
      await pollJob(started.job_id, {
        onUpdate: (j) => setProgress({
          current: j.current || 0, total: j.total_items || sendable.length,
          items: j.items || [], done: !!j.done, cancelled: !!j.cancelled,
        }),
      });
      invalidateListings();
    } catch (e) {
      toast(`The crosspost stopped: ${e.message}`, { kind: "error" });
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!jobId) return;
    // The server marks the job stopped straight away, so the poller settles
    // on its next tick whether or not this answers.
    try { await api(`/api/bulk/cancel/${jobId}`, { method: "POST" }); }
    catch (e) { toast(`Couldn't stop it: ${e.message}`, { kind: "error" }); }
  };

  const body = {
    // ---- 1. which of the ticked listings can go ------------------------
    pick: (
      <>
        <p className="text-sm text-ink-secondary">
          What Etsy needs from each listing — its own category, who made it and
          when, and your shop's shipping, return and processing profiles. A
          listing already on Etsy, an auction, or one with variations is left out.
        </p>
        <ul className="mt-4 flex flex-col divide-y divide-line" aria-label="Listings to crosspost">
          {picked.map(({ item, skip, blockers, ready }) => (
            <Row key={item.id}>
              <div className="min-w-0 flex-1">
                <p className="font-semibold text-ink truncate">
                  {item.listing?.title || item.title || "Untitled"}
                </p>
                {skip ? (
                  <p className="text-[13px] text-ink-faint mt-0.5" data-skip>{skip}</p>
                ) : ready ? (
                  <p className="text-[13px] text-success mt-0.5 flex items-center gap-1" data-ready>
                    <CheckCircle2 size={14} aria-hidden /> Ready for Etsy
                  </p>
                ) : <Chips items={blockers} />}
              </div>
              {!skip && (
                <Button variant="soft" size="sm"
                  onClick={() => { onClose(); openListing(item.id); }}>
                  <ExternalLink aria-hidden /> Open
                </Button>
              )}
            </Row>
          ))}
        </ul>
      </>
    ),

    // ---- 2. the answers Etsy wants, once for the batch -----------------
    defaults: (
      <div className="flex flex-col gap-5">
        <p className="text-sm text-ink-secondary">
          Etsy asks three things eBay never did. Answer them once for these{" "}
          {goable.length} listing{goable.length === 1 ? "" : "s"} — anything a
          listing already says for itself wins, and an age written in its item
          details (a decade or a year) is read from there.
        </p>
        <div className="grid sm:grid-cols-2 gap-4">
          <Field label="Who made these?"
            help={`Etsy takes handmade, vintage (${ETSY_VINTAGE_YEARS}+ years) and craft supplies.`}>
            <Select value={defaults.who_made}
              onChange={(e) => setDefault("who_made", e.target.value)}>
              {WHO_MADE_OPTIONS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </Select>
          </Field>
          <Field label="When were they made?"
            help="Used only where the listing itself doesn't say.">
            <Select value={defaults.when_made}
              onChange={(e) => setDefault("when_made", e.target.value)}>
              <option value="">— read it from each listing —</option>
              {WHEN_MADE_OPTIONS.map(([v, label]) => (
                <option key={v} value={v}>{label}</option>
              ))}
            </Select>
          </Field>
        </div>
        <label className="flex items-center gap-2.5 text-sm text-ink cursor-pointer">
          <input type="checkbox" checked={!!defaults.is_supply}
            onChange={(e) => setDefault("is_supply", e.target.checked)}
            className="size-4 accent-blue" />
          These are craft supplies (not finished items)
        </label>
        <div className="grid sm:grid-cols-3 gap-4">
          <Field label="Shipping profile" help="Leave blank to use your Settings default.">
            <Select value={defaults.shipping_profile_id}
              onChange={(e) => setDefault("shipping_profile_id", e.target.value)}>
              <option value="">
                {selected.shipping_profile_id ? "Settings default" : "— none —"}
              </option>
              {((etsySettings && etsySettings.shipping_profiles) || []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Return policy">
            <Select value={defaults.return_policy_id}
              onChange={(e) => setDefault("return_policy_id", e.target.value)}>
              <option value="">
                {selected.return_policy_id ? "Settings default" : "— none —"}
              </option>
              {((etsySettings && etsySettings.return_policies) || []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
          </Field>
          <Field label="Processing time">
            <Select value={defaults.readiness_state_id}
              onChange={(e) => setDefault("readiness_state_id", e.target.value)}>
              <option value="">
                {selected.readiness_state_id ? "Settings default" : "— none —"}
              </option>
              {((etsySettings && etsySettings.readiness_states) || []).map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Publish as"
          help={mode === "live"
            ? `Etsy charges its listing fee for each one that goes live — that's ${goable.length} listing${goable.length === 1 ? "" : "s"}.`
            : "Drafts cost nothing and sit in your Etsy shop until you publish them there."}>
          <div className="flex flex-wrap items-center gap-2">
            {[["draft", "Etsy drafts"], ["live", "Live on Etsy"]].map(([value, label]) => (
              <button key={value} type="button" onClick={() => setMode(value)}
                aria-pressed={mode === value}
                className={cn(
                  "inline-flex items-center h-9 px-3.5 rounded-full text-[13px] font-semibold",
                  "cursor-pointer transition-colors border",
                  mode === value
                    ? "bg-blue text-on-accent border-blue"
                    : "bg-card text-ink-secondary border-line hover:text-ink")}>
                {label}
              </button>
            ))}
          </div>
        </Field>
        <label className="flex items-start gap-2.5 text-sm text-ink cursor-pointer">
          <input type="checkbox" checked={attested}
            onChange={(e) => setAttested(e.target.checked)}
            className="size-4 accent-blue mt-0.5" />
          <span>
            These items are handmade by me, vintage ({ETSY_VINTAGE_YEARS}+ years old),
            or craft supplies — the only things Etsy allows.
          </span>
        </label>
      </div>
    ),

    // ---- 3. what each listing will become ------------------------------
    review: (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-ink-secondary">
          What Etsy will get. Edit a title or a category here and it is used for
          this crosspost; everything else comes from the listing.
        </p>
        <ul className="flex flex-col divide-y divide-line">
          {(rows || []).map((row) => (
            <Row key={row.id} className={row.skip ? "opacity-50" : undefined}>
              <div className="min-w-0 flex-1 flex flex-col gap-2">
                <Input value={row.etsy_title || ""} aria-label={`Etsy title for ${row.title}`}
                  onChange={(e) => setRow(row.id, { etsy_title: e.target.value })} />
                <div className="flex flex-wrap items-center gap-2">
                  <Input className="max-w-32" type="number" min="0" inputMode="numeric"
                    aria-label={`Etsy category for ${row.title}`}
                    value={row.taxonomy?.id || ""}
                    onChange={(e) => setRow(row.id, {
                      taxonomy: { ...row.taxonomy, id: parseInt(e.target.value, 10) || 0,
                        path: "", source: "seller" } })} />
                  <Button variant="secondary" size="sm" loading={!!row.suggesting}
                    onClick={() => suggestFor(row)}>
                    <Sparkles aria-hidden /> Suggest
                  </Button>
                  <span className="text-[12px] text-ink-faint truncate">
                    {row.taxonomy?.path || "no category yet"}
                  </span>
                </div>
                <p className="text-[12px] text-ink-faint">
                  {row.who_made === "i_did" ? "I made it" : "Someone else made it"}
                  {row.when_made ? ` · ${row.when_made.replace(/_/g, "–")}` : ""}
                  {row.when_made_source === "details" ? " (from its details)" : ""}
                  {row.tags?.length ? ` · ${row.tags.length} tags` : ""}
                  {row.materials?.length ? ` · ${row.materials.join(", ")}` : ""}
                </p>
                {row.policy_flag && (
                  <p className="text-[12px] text-warning flex items-start gap-1">
                    <AlertTriangle size={13} className="shrink-0 mt-0.5" aria-hidden />
                    Etsy doesn't allow resale of items under {ETSY_VINTAGE_YEARS} years
                    old unless you made them.
                  </p>
                )}
                {!row.ready && <Chips items={(row.blockers || []).map((b) => ({
                  key: b.target, label: b.title, why: b.fix }))} />}
              </div>
              <label className="flex items-center gap-2 text-[13px] text-ink-secondary cursor-pointer">
                <input type="checkbox" checked={!!row.skip}
                  onChange={(e) => setRow(row.id, { skip: e.target.checked })}
                  className="size-4 accent-blue" />
                Skip
              </label>
            </Row>
          ))}
        </ul>
        <p className="text-[13px] text-ink-secondary" data-review-tally>
          {sendable.length} ready to send
          {aiCalls > 0 ? ` · the AI picked ${aiCalls} categor${aiCalls === 1 ? "y" : "ies"}` : ""}
          {skippedRows.length ? ` · ${skippedRows.length} skipped` : ""}
        </p>
      </div>
    ),

    // ---- 4. the run ----------------------------------------------------
    run: (
      <div className="flex flex-col gap-3">
        <p className="text-sm text-ink-secondary flex items-center gap-2" data-progress>
          {busy && <Loader2 size={15} className="animate-spin text-blue" aria-hidden />}
          {progress
            ? `${progress.current} of ${progress.total} sent`
            : "Starting…"}
        </p>
        <ul className="flex flex-col divide-y divide-line">
          {((progress && progress.items) || []).map((row) => {
            const meta = OUTCOME[row.status] || OUTCOME.queued;
            return (
              <Row key={row.id}>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-ink truncate">{row.title}</p>
                  <p className={cn("text-[13px] mt-0.5",
                    meta.tone === "success" ? "text-success"
                      : meta.tone === "warning" ? "text-warning" : "text-ink-faint")}>
                    {meta.label}{row.message ? ` — ${row.message}` : ""}
                  </p>
                </div>
                {row.url && (
                  <a href={row.url} target="_blank" rel="noreferrer"
                    className="text-[13px] font-semibold text-blue hover:underline">
                    View on Etsy
                  </a>
                )}
              </Row>
            );
          })}
        </ul>
        <p className="text-[12px] text-ink-faint">
          A sale on eBay takes the Etsy copy down automatically. A sale on Etsy
          doesn't reach eBay yet — end it here when it sells there.
        </p>
      </div>
    ),
  }[step];

  const footer = {
    pick: (
      <>
        <p className="text-[13px] text-ink-secondary tabular-nums" data-tally>
          {t.ready} ready · {t.needing} need something · {t.skipped} skipped
        </p>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={onClose}>Close</Button>
          <Button variant="primary" disabled={!goable.length}
            onClick={() => setStep("defaults")}>
            <ArrowRightLeft aria-hidden /> Continue ({goable.length})
          </Button>
        </div>
      </>
    ),
    defaults: (
      <>
        {/* What it costs, in the footer where the button is, rather than in
            the picker's tooltip: a listing fee per listing is the one thing
            a seller crossposting a whole store must not find out after. */}
        <p className="text-[13px] text-ink-secondary">
          {mode === "live"
            ? `Etsy charges its listing fee for each one that goes live — that's ${goable.length} listing${goable.length === 1 ? "" : "s"}.`
            : "They arrive as Etsy drafts — no listing fee until you publish them there."}
        </p>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setStep("pick")}>Back</Button>
          <Button variant="primary" disabled={!attested || busy} loading={busy}
            onClick={loadReview}>
            Review {goable.length}
          </Button>
        </div>
      </>
    ),
    review: (
      <>
        <p className="text-[13px] text-ink-secondary">
          {mode === "live"
            ? "Publishing live — Etsy charges its listing fee per listing."
            : "Sending as Etsy drafts."}
        </p>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setStep("defaults")}>Back</Button>
          <Button variant="primary" disabled={!sendable.length || busy} onClick={run}>
            <ArrowRightLeft aria-hidden /> Crosspost {sendable.length}
          </Button>
        </div>
      </>
    ),
    run: (
      <>
        <p className="text-[13px] text-ink-secondary">
          {progress?.cancelled ? "Stopped." : busy ? "Sending one at a time…" : "Finished."}
        </p>
        <div className="flex items-center gap-2">
          {busy && <Button variant="danger" onClick={stop}>Stop</Button>}
          <Button variant="secondary" onClick={onClose}>
            {busy ? "Close" : "Done"}
          </Button>
        </div>
      </>
    ),
  }[step];

  return (
    <Dialog open onClose={onClose} title="Crosspost to Etsy" wide>
      <p className="text-[12px] font-semibold text-ink-faint mb-3 flex items-center gap-1.5">
        {STEPS.map((id, i) => (
          <span key={id} className={cn("uppercase tracking-wide",
            step === id ? "text-blue" : "text-ink-faint")}>
            {i > 0 && <span className="mx-1.5 text-line">·</span>}
            {id}
          </span>
        ))}
      </p>
      {body}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
        {footer}
      </div>
    </Dialog>
  );
}
