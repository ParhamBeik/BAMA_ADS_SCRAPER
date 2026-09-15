/**
 * Catalogue queries shared across screens.
 *
 * `/api/brands/` was fetched from three places — the filter panel, the scope
 * picker's brand select, and the scope label that resolves a slug to a name —
 * each repeating the same key, the same ten-minute staleTime and the same
 * array-or-paginated unwrapping. Three copies of a cache key is the part that
 * matters: get one of them wrong and a screen silently runs its own request
 * against its own cache entry.
 */
import { useQuery } from "@tanstack/react-query";
import { api, type Brand, type Paginated } from "./api";

/** The brand list, unwrapped. Cached for ten minutes; the catalogue barely moves. */
export function useBrands({ enabled = true }: { enabled?: boolean } = {}) {
  const query = useQuery({
    queryKey: ["brands"],
    enabled,
    staleTime: 10 * 60_000,
    queryFn: ({ signal }) => api.get<Paginated<Brand> | Brand[]>("/api/brands/", signal),
  });
  const list: Brand[] = Array.isArray(query.data)
    ? query.data
    : (query.data?.results ?? []);
  return { query, list };
}
