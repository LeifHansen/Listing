import { createContext, useCallback, useContext, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, AlertTriangle, Info, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

// Toasts + async confirm dialogs, replacing the old alert()/confirm() calls.
const ToastContext = createContext(null);

const ICONS = {
  success: [CheckCircle2, "text-success"],
  error: [XCircle, "text-error"],
  warning: [AlertTriangle, "text-warning"],
  info: [Info, "text-blue"],
};

let _id = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [confirmState, setConfirmState] = useState(null);
  const resolver = useRef(null);

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const toast = useCallback((message, opts = {}) => {
    const id = ++_id;
    const t = { id, message, kind: opts.kind || "info", title: opts.title };
    setToasts((cur) => [...cur.slice(-3), t]);
    const ttl = opts.sticky ? null : (opts.ttl || (opts.kind === "error" ? 8000 : 5000));
    if (ttl) setTimeout(() => dismiss(id), ttl);
  }, [dismiss]);

  // await confirm({ title, message, confirmLabel, danger }) → boolean
  const confirm = useCallback((opts) => {
    return new Promise((resolve) => {
      resolver.current = resolve;
      setConfirmState(opts);
    });
  }, []);

  const settle = (value) => {
    setConfirmState(null);
    if (resolver.current) { resolver.current(value); resolver.current = null; }
  };

  return (
    <ToastContext.Provider value={{ toast, confirm }}>
      {children}
      {/* On a phone the BOTTOM of the screen is spoken for twice over: the
          fixed nav lives there, and so does the editor's publish bar. A toast
          anchored there lands on top of both — so on small screens it comes
          down from the top instead, and only takes the bottom-right corner
          from `sm` up, where there is room for it. */}
      {createPortal(
        <div className="fixed z-[60] top-[max(1rem,env(safe-area-inset-top))] right-4 left-4 sm:top-auto sm:bottom-4 sm:left-auto sm:w-96 sm:pb-[env(safe-area-inset-bottom)] flex flex-col gap-2 pointer-events-none">
          <AnimatePresence>
            {toasts.map((t) => {
              const [Icon, color] = ICONS[t.kind] || ICONS.info;
              return (
                <motion.div
                  key={t.id}
                  layout
                  initial={{ opacity: 0, y: 16, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={{ opacity: 0, y: 8, scale: 0.97 }}
                  transition={{ duration: 0.2, ease: "easeOut" }}
                  role={t.kind === "error" ? "alert" : "status"}
                  /* Not pointer-events-auto, and this is the whole point: a
                     toast is an announcement, not a control. Tap-to-dismiss
                     made every message an invisible tap trap over whatever it
                     covered -- and what it covers is decided by where the app
                     happens to have put its buttons. An eBay rejection raises
                     an 8-second, screen-wide error toast directly over the
                     editor's Publish button, so the seller's next tap
                     dismissed the toast (taking the explanation with it) and
                     published nothing. Nothing happened, twice over.
                     The X below is the only part that takes a tap; everything
                     else passes straight through to the page. */
                  className={cn(
                    "bg-card border border-line rounded-tile shadow-float",
                    "p-4 flex items-start gap-3",
                  )}
                >
                  <Icon size={19} className={cn("shrink-0 mt-0.5", color)} aria-hidden />
                  <div className="min-w-0 text-sm">
                    {t.title && <p className="font-bold text-ink">{t.title}</p>}
                    <p className="text-ink-secondary whitespace-pre-line break-words">{t.message}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => dismiss(t.id)}
                    aria-label="Dismiss"
                    className={cn(
                      "pointer-events-auto shrink-0 -m-1 ml-auto grid place-items-center",
                      "size-8 rounded-full cursor-pointer text-ink-faint",
                      "hover:bg-bg-sunken hover:text-ink transition-colors",
                    )}
                  >
                    <X size={15} aria-hidden />
                  </button>
                </motion.div>
              );
            })}
          </AnimatePresence>
        </div>,
        document.body,
      )}
      <Dialog
        open={!!confirmState}
        onClose={() => settle(false)}
        title={confirmState?.title || "Are you sure?"}
      >
        {confirmState && (
          <>
            {confirmState.message && (
              <p className="text-sm text-ink-secondary whitespace-pre-line mb-5">
                {confirmState.message}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => settle(false)}>
                {confirmState.cancelLabel || "Cancel"}
              </Button>
              <Button
                variant={confirmState.danger ? "danger" : "primary"}
                onClick={() => settle(true)}
              >
                {confirmState.confirmLabel || "Confirm"}
              </Button>
            </div>
          </>
        )}
      </Dialog>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}
