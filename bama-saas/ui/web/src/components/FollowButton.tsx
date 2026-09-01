/**
 * Follow the car currently on screen.
 *
 * Deliberately a *scope* and not a listing. Saving one ad is what `Favorite`
 * already does; the question this answers is "what is happening to the kind of
 * car I want to buy", which outlives any single listing on it — and until this
 * existed, the app could not answer it at all.
 *
 * Whether a scope is already followed is decided by comparing `scope_key`, which
 * the API derives server-side (`accounts.models.ScopedToACar.build_scope_key`).
 * Re-deriving that comparison here would be a second definition of "the same
 * car", and the two would disagree the first time either changed — the button
 * would then read "follow" for something already followed, and pressing it
 * would return the existing row rather than a new one, which looks like a bug
 * with no error to explain it.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, BellRing } from "lucide-react";
import { api } from "../api";
import type { Paginated } from "../api";
import { Button } from "./ui/button";

export interface Scope {
  brand?: string;
  model?: string;
  variant?: string;
  year?: string;
}

export interface WatchlistEntry {
  id: number;
  brand_slug: string;
  model: number | null;
  variant: number | null;
  year_jalali: number | null;
  scope_key: string;
  model_name: string;
  variant_name: string;
  brand_name: string;
}

/** The same key the server derives, so the two cannot disagree about identity. */
export function scopeKey(scope: Scope): string {
  const parts: string[] = [];
  if (scope.brand) parts.push(`brand:${scope.brand}`);
  if (scope.model) parts.push(`model:${scope.model}`);
  if (scope.variant) parts.push(`variant:${scope.variant}`);
  if (scope.year) parts.push(`year:${scope.year}`);
  return parts.join("/") || "market";
}

export function useWatchlist() {
  return useQuery({
    queryKey: ["watchlists"],
    queryFn: ({ signal }) =>
      api.get<Paginated<WatchlistEntry>>("/api/watchlists/", signal),
  });
}

export function FollowButton({ scope }: { scope: Scope }) {
  const client = useQueryClient();
  const watchlist = useWatchlist();
  const key = scopeKey(scope);
  const existing = watchlist.data?.results?.find((w) => w.scope_key === key);

  const invalidate = () => {
    client.invalidateQueries({ queryKey: ["watchlists"] });
  };

  const follow = useMutation({
    mutationFn: () =>
      api.post<WatchlistEntry>("/api/watchlists/", {
        brand_slug: scope.brand ?? "",
        model: scope.model ? Number(scope.model) : null,
        variant: scope.variant ? Number(scope.variant) : null,
        year_jalali: scope.year ? Number(scope.year) : null,
      }),
    onSuccess: invalidate,
  });

  const unfollow = useMutation({
    mutationFn: (id: number) => api.del(`/api/watchlists/${id}/`),
    onSuccess: invalidate,
  });

  // The whole market is not a thing to follow — every alert would match it, so
  // the button would promise something the feed could not usefully deliver.
  if (key === "market") return null;

  const busy = follow.isPending || unfollow.isPending;
  return (
    <Button
      variant={existing ? "secondary" : "outline"}
      size="sm"
      disabled={busy || watchlist.isLoading}
      onClick={() => (existing ? unfollow.mutate(existing.id) : follow.mutate())}
      aria-pressed={Boolean(existing)}
    >
      {existing ? <BellRing className="size-4" /> : <Bell className="size-4" />}
      {existing ? "دنبال می‌کنید" : "دنبال کردن"}
    </Button>
  );
}
