import { Skeleton } from "@/components/ui/skeleton";

/**
 * Placeholder for the admin dashboard body. It lives in its own module so the
 * `/admin` route can paint it without importing the dashboard graph (recharts
 * and every section's data hooks) that it is standing in for.
 */
export function DashboardSkeleton() {
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
      <Skeleton className="h-10 w-32" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Skeleton key={index} className="h-20 w-full" />
        ))}
      </div>
      <Skeleton className="h-72 w-full" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}
