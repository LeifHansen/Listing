import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

// EmptyState — cute illustration + helpful explanation + one primary action.
export function EmptyState({ illustration: Illo, title, message, action, className }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={cn("flex flex-col items-center text-center gap-3 py-12 px-6", className)}
    >
      {Illo && <Illo className="mb-1" />}
      <h3 className="text-lg font-bold text-ink">{title}</h3>
      {message && <p className="text-sm text-ink-secondary max-w-sm text-balance">{message}</p>}
      {action && <div className="mt-2">{action}</div>}
    </motion.div>
  );
}
