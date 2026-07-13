import { cn } from "@/lib/utils";

export function Skeleton({ className }) {
  return <div className={cn("ai-shimmer rounded-[10px]", className)} aria-hidden />;
}

export function ListingCardSkeleton() {
  return (
    <div className="bg-card rounded-card border border-line shadow-card p-4 flex gap-4 items-center">
      <Skeleton className="size-16 rounded-tile shrink-0" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-3 w-1/3" />
      </div>
    </div>
  );
}

export function WorkflowSkeleton() {
  return (
    <div className="space-y-4" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="bg-card rounded-card border border-line shadow-card p-6 space-y-3">
          <Skeleton className="h-5 w-40" />
          <Skeleton className="h-11 w-full" />
          <Skeleton className="h-11 w-2/3" />
        </div>
      ))}
    </div>
  );
}
