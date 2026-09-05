/**
 * Follow the car currently on screen.
 *
 * Deliberately a *scope* and not a listing. Saving one ad is what `Favorite`
 * already does; the question this answers is "what is happening to the kind of
 * car I want to buy", which outlives any single listing on it — and until this
 * existed, the app could not answer it at all.
 *
 * Whether a scope is already followed is decided by comparing `scope_key`. The
 * server is where that key is authoritative — `ScopedToACar.save()` derives it
 * and the unique constraint is on it, because a constraint over the four
 * nullable columns would not stop a user following «all of Peugeot» twice
 * (`NULL != NULL` in a unique index). `scopeKey` below deliberately mirrors that
 * derivation so the button can render the right state without a round trip per
 * scope change.
 *
 * That mirror is the thing to be careful with: it is a second definition of
 * "the same car", so the two have to be changed together. If they drift, the
 * button reads "follow" for something already followed and pressing it returns
 * the existing row rather than a new one — a bug with no error to explain it.
 * Keep the field order narrowest-last, and keep the `market` fallback.
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

/**
 * Every scope this user follows, not the first page of them.
 *
 * `/api/watchlists/` is paginated at the DRF default of 50, and the only
 * question asked of the answer is "is the scope on screen in here" — so a
 * caller who read one page got a wrong answer for everything after the 50th
 * follow. The button then read "follow" for a car already followed, and
 * pressing it hit the idempotent POST, which returns 200 and the existing row:
 * no error, no visible change, no way to tell what went wrong.
 *
 * Followed through to exhaustion rather than fixed by unpaginating the endpoint,
 * because the response shape is public API (`README.md` documents it) and this
 * is its only consumer. `next` is an absolute URL, so it is passed to `api.get`
 * as a path only after the origin is stripped.
 */
async function fetchAllWatchlists(signal?: AbortSignal): Promise<WatchlistEntry[]> {
  const entries: WatchlistEntry[] = [];
  let path: string | null = "/api/watchlists/";
  while (path) {
    const page: Paginated<WatchlistEntry> = await api.get<Paginated<WatchlistEntry>>(
      path,
      signal,
    );
    entries.push(...page.results);
    path = page.next ? new URL(page.next).pathname + new URL(page.next).search : null;
  }
  return entries;
}

export function useWatchlist() {
  return useQuery({
    queryKey: ["watchlists"],
    queryFn: ({ signal }) => fetchAllWatchlists(signal),
  });
}

export function FollowButton({ scope }: { scope: Scope }) {
  const client = useQueryClient();
  const watchlist = useWatchlist();
  const key = scopeKey(scope);
  const existing = watchlist.data?.find((w) => w.scope_key === key);

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
