import { forwardRef } from "react";
import { cva } from "class-variance-authority";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 font-semibold select-none",
    "whitespace-nowrap transition-shadow duration-150 cursor-pointer",
    "disabled:opacity-50 disabled:pointer-events-none",
    "[&_svg]:size-[1.1em] [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-blue text-on-accent rounded-full shadow-card hover:shadow-card-hover hover:bg-blue-strong",
        secondary: "bg-card text-ink border border-line rounded-button shadow-card hover:shadow-card-hover hover:border-line-strong",
        ghost: "text-ink-secondary rounded-button hover:bg-bg-sunken hover:text-ink",
        soft: "bg-blue-soft text-blue rounded-button hover:shadow-card",
        danger: "bg-error-soft text-error rounded-button hover:shadow-card",
        success: "bg-green text-on-accent rounded-full shadow-card hover:shadow-card-hover",
      },
      size: {
        sm: "h-9 px-3.5 text-[13px]",
        md: "h-11 px-5 text-sm",
        lg: "h-12 px-6 text-[15px]",
        icon: "size-11 rounded-button",
        iconSm: "size-9 rounded-[12px]",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

// ActionButton — the app's one button. Tiny lift on hover, soft press.
export const Button = forwardRef(function Button(
  { className, variant, size, loading, children, disabled, ...props },
  ref,
) {
  return (
    <motion.button
      ref={ref}
      type="button"
      whileHover={{ y: -1 }}
      whileTap={{ scale: 0.97 }}
      transition={{ duration: 0.15, ease: "easeOut" }}
      className={cn(buttonVariants({ variant, size }), className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading && <Loader2 className="animate-spin" aria-hidden />}
      {children}
    </motion.button>
  );
});

export { buttonVariants };
