/**
 * Filter state lives in the URL, not in React state.
 *
 * That makes every view shareable, bookmarkable and back-button-correct for free,
 * and it means two panels reading the same filter cannot disagree — there is only
 * one copy. The cost is that filters must be strings, which is a small price for
 * never debugging "why does the chart show a different cohort than the table".
 */
import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export function useFilters() {
  const [params, setParams] = useSearchParams();

  const set = useCallback(
    (next: Record<string, string | number | null | undefined>) => {
      const merged = new URLSearchParams(params);
      for (const [key, value] of Object.entries(next)) {
        if (value === null || value === undefined || value === "") merged.delete(key);
        else merged.set(key, String(value));
      }
      setParams(merged, { replace: true });
    },
    [params, setParams],
  );

  return {
    get: (key: string) => params.get(key) ?? undefined,
    getInt: (key: string) => {
      const raw = params.get(key);
      const n = raw ? Number(raw) : NaN;
      return Number.isFinite(n) ? n : undefined;
    },
    set,
    toString: () => params.toString(),
  };
}

/** Build a query string from defined values only. */
export function qs(values: Record<string, string | number | undefined | null>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** "Listed within N days" as the API's absolute `listed_from`. Floored to the
 *  hour: it is part of the query key, and a millisecond-fresh timestamp would
 *  make every render a new query. */
export function listedFrom(days: number | null | undefined): string | null {
  if (!days) return null;
  const hour = Math.floor(Date.now() / 3_600_000) * 3_600_000;
  return new Date(hour - days * 86_400_000).toISOString();
}
