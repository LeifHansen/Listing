import { cn } from "@/lib/utils";

export function Skeleton({ className }) {
  return <div className={cn("ai-shimmer rounded-[10px]", className)} aria-hidden />;
}

export function ListingCardSkeleton({ className }) {
  return (
    <div className={cn("bg-card rounded-card border border-line shadow-card p-4 flex gap-4 items-center",
      className)}>
      <Skeleton className="size-16 rounded-tile shrink-0" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-3 w-1/3" />
      </div>
    </div>
  );
}
