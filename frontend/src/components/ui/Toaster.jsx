import { createContext, useCallback, useContext, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, AlertTriangle, Info, XCircle } from "lucide-react";
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
      {createPortal(
        <div className="fixed z-[60] bottom-4 right-4 left-4 sm:left-auto sm:w-96 flex flex-col gap-2 pointer-events-none pb-[env(safe-area-inset-bottom)]">
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
                  className={cn(
                    "pointer-events-auto bg-card border border-line rounded-tile shadow-float",
                    "p-4 flex items-start gap-3 cursor-pointer",
                  )}
                  onClick={() => dismiss(t.id)}
                >
                  <Icon size={19} className={cn("shrink-0 mt-0.5", color)} aria-hidden />
                  <div className="min-w-0 text-sm">
                    {t.title && <p className="font-bold text-ink">{t.title}</p>}
                    <p className="text-ink-secondary whitespace-pre-line break-words">{t.message}</p>
                  </div>
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
