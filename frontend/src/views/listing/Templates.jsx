import { useState } from "react";
import { LayoutTemplate, Plus, Sparkles, Trash2, X, Check } from "lucide-react";
import { useApp } from "@/store";
import { useToast } from "@/components/ui/Toaster";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/fields";
import { templateFromListing } from "@/lib/templates";
import { shippingServiceLabel } from "@/lib/shipping";

// Shown above the uploader on a fresh listing: pick a saved logistical
// template (category, condition, package, shipping pre-filled) or start blank.
export function TemplatePicker() {
  const { templates, activeTemplate, setActiveTemplate, user } = useApp();
  if (!user || !templates.length) return null;

  return (
    <div className="rounded-card bg-card border border-line shadow-card p-5">
      <div className="flex items-center gap-2 mb-1">
        <LayoutTemplate size={17} className="text-blue" aria-hidden />
        <p className="font-bold text-sm text-ink">Start from a template</p>
      </div>
      <p className="text-xs text-ink-secondary mb-3">
        Pre-fills category, condition, package size &amp; shipping — the AI still
        writes the title, description, and price from your photos.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setActiveTemplate(null)}
          className={chip(!activeTemplate)}
        >
          <Sparkles size={14} aria-hidden /> Start from scratch
        </button>
        {templates.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setActiveTemplate(t)}
            className={chip(activeTemplate?.id === t.id)}
            title={templateSummary(t)}
          >
            {activeTemplate?.id === t.id && <Check size={14} aria-hidden />}
            {t.name}
          </button>
        ))}
      </div>
      {activeTemplate && (
        <p className="mt-3 text-[13px] text-ink-secondary">
          Using <strong className="text-ink">{activeTemplate.name}</strong>
          {templateSummary(activeTemplate) ? ` · ${templateSummary(activeTemplate)}` : ""}
        </p>
      )}
    </div>
  );
}

function chip(active) {
  return [
    "inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-semibold cursor-pointer transition-colors duration-150 border",
    active
      ? "bg-blue text-on-accent border-blue"
      : "bg-card text-ink border-line hover:border-blue/50",
  ].join(" ");
}

function templateSummary(t) {
  const d = t.data || {};
  const bits = [];
  if (d.category_name) bits.push(d.category_name);
  if (d.shipping_service) bits.push(shippingServiceLabel(d.shipping_service));
  const lb = Number(d.package_weight_lb) || 0;
  const oz = Number(d.package_weight_oz) || 0;
  if (lb || oz) bits.push(`${lb ? `${lb}lb ` : ""}${oz ? `${oz}oz` : ""}`.trim());
  return bits.join(" · ");
}

// A button (used in the workflow) that snapshots the current listing's
// logistics into a reusable template.
export function SaveTemplateButton({ w }) {
  const { saveTemplate, user, openAuth } = useApp();
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);

  const start = () => {
    if (!user) { openAuth(); return; }
    setName(w.form.category_name || w.form.brand || "");
    setOpen(true);
  };

  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      await saveTemplate({ name: name.trim(), data: templateFromListing(w.collect()) });
      toast(`Template "${name.trim()}" saved — pick it on your next new listing.`, { kind: "success" });
      setOpen(false);
      setName("");
    } catch (e) {
      toast(`Couldn't save template: ${e.message}`, { kind: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Button variant="ghost" onClick={start}>
        <LayoutTemplate aria-hidden /> Save as template
      </Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Save as template">
        <p className="text-sm text-ink-secondary mb-4">
          Saves this listing's category, condition, brand, package size &amp;
          shipping service (not the title, photos, or price) so you can reuse it.
        </p>
        <Input
          autoFocus
          placeholder="e.g. Men's T-Shirt, Vinyl Record, Trading Card"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); }}
        />
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={!name.trim()}>
            <Check aria-hidden /> Save template
          </Button>
        </div>
      </Dialog>
    </>
  );
}

// Compact manager (used in Settings): list + delete templates.
export function TemplateManager() {
  const { templates, deleteTemplate } = useApp();
  const { toast, confirm } = useToast();

  const remove = async (t) => {
    if (!(await confirm({
      title: `Delete "${t.name}"?`,
      message: "This only removes the template — your listings are untouched.",
      confirmLabel: "Delete",
      danger: true,
    }))) return;
    try {
      await deleteTemplate(t.id);
      toast("Template deleted.", { kind: "success" });
    } catch (e) {
      toast(`Couldn't delete: ${e.message}`, { kind: "error" });
    }
  };

  if (!templates.length) {
    return (
      <p className="text-sm text-ink-secondary">
        No templates yet. On a listing, tap <strong>Save as template</strong> to
        capture its category, package size &amp; shipping for reuse.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {templates.map((t) => (
        <li key={t.id} className="flex items-center gap-3 rounded-tile border border-line bg-card px-3.5 py-2.5">
          <LayoutTemplate size={16} className="text-blue shrink-0" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-ink truncate">{t.name}</p>
            {templateSummary(t) && (
              <p className="text-xs text-ink-secondary truncate">{templateSummary(t)}</p>
            )}
          </div>
          <Button variant="ghost" size="iconSm" aria-label={`Delete ${t.name}`}
            title="Delete template" onClick={() => remove(t)}>
            <Trash2 size={15} className="text-error" />
          </Button>
        </li>
      ))}
    </ul>
  );
}
