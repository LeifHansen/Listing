import { forwardRef, useId } from "react";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

// InfoTip — a small ⓘ that reveals its text on hover/focus (native tooltip).
// The app-wide home for helper prose: keep the UI quiet, keep the help one
// hover away. Focusable so keyboard users get the tooltip too.
export function InfoTip({ text, className }) {
  if (!text) return null;
  return (
    <span
      title={text}
      tabIndex={0}
      aria-label={text}
      className={cn(
        "inline-grid place-items-center shrink-0 cursor-help align-middle",
        "text-ink-faint hover:text-ink-secondary focus:text-ink-secondary outline-none",
        className,
      )}
    >
      <Info size={13} aria-hidden />
    </span>
  );
}

const controlClasses = cn(
  "w-full bg-card text-ink border border-line rounded-input px-4",
  "placeholder:text-ink-faint transition-colors duration-150",
  "hover:border-line-strong focus:border-blue focus:outline-none",
  "focus:ring-2 focus:ring-blue/25",
  "data-[fix=true]:border-error data-[fix=true]:ring-2 data-[fix=true]:ring-error/25",
);

// Field — label above control. Helper prose (`help`) hides behind a hover ⓘ
// next to the label instead of a visible line of text, keeping forms quiet.
export function Field({ label, hint, help, error, required, children, className }) {
  return (
    <label className={cn("flex flex-col gap-1.5 min-w-0", className)}>
      {label && (
        <span className="text-[13px] font-semibold text-ink flex items-center gap-1.5">
          {label}
          {required && <span className="text-error" aria-hidden>*</span>}
          {hint && <span className="font-normal text-ink-faint">{hint}</span>}
          {help && <InfoTip text={help} />}
        </span>
      )}
      {children}
      {/* No label to hang the ⓘ on — fall back to the old inline line. */}
      {!label && help && <span className="text-xs text-ink-secondary">{help}</span>}
      {error && <span className="text-xs font-medium text-error">{error}</span>}
    </label>
  );
}

export const Input = forwardRef(function Input({ className, needsFix, ...props }, ref) {
  return (
    <input
      ref={ref}
      data-fix={needsFix || undefined}
      className={cn(controlClasses, "h-11 text-[15px]", className)}
      {...props}
    />
  );
});

export const Textarea = forwardRef(function Textarea({ className, needsFix, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      data-fix={needsFix || undefined}
      className={cn(controlClasses, "py-3 text-[15px] leading-relaxed resize-y", className)}
      {...props}
    />
  );
});

export const Select = forwardRef(function Select({ className, children, ...props }, ref) {
  return (
    <select
      ref={ref}
      className={cn(controlClasses, "h-11 text-[15px] appearance-none pr-9 bg-no-repeat",
        "bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2216%22%20height%3D%2216%22%20viewBox%3D%220%200%2024%2024%22%20fill%3D%22none%22%20stroke%3D%22%236b7280%22%20stroke-width%3D%222%22%20stroke-linecap%3D%22round%22%20stroke-linejoin%3D%22round%22%3E%3Cpath%20d%3D%22m6%209%206%206%206-6%22%2F%3E%3C%2Fsvg%3E')]",
        "bg-[position:right_14px_center]",
        className)}
      {...props}
    >
      {children}
    </select>
  );
});

export function Toggle({ checked, onChange, label, help, className }) {
  const id = useId();
  return (
    <label htmlFor={id} className={cn("flex items-start gap-3 cursor-pointer select-none", className)}>
      <span className="relative inline-flex shrink-0 mt-0.5">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span
          aria-hidden
          className={cn(
            "block h-6 w-11 rounded-full bg-line-strong transition-colors duration-200",
            "peer-checked:bg-blue peer-focus-visible:ring-2 peer-focus-visible:ring-blue/40",
          )}
        />
        <span
          aria-hidden
          className={cn(
            "absolute left-0.5 top-0.5 size-5 rounded-full bg-white shadow-card",
            "transition-transform duration-200 peer-checked:translate-x-5",
          )}
        />
      </span>
      <span className="min-w-0 flex items-center gap-1.5">
        <span className="text-sm font-semibold text-ink">{label}</span>
        {help && <InfoTip text={help} />}
      </span>
    </label>
  );
}
