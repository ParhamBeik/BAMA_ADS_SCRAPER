/**
 * The unread-alert count, on its own so the header can read it.
 *
 * This lived in `pages/Alerts.tsx` for about ten minutes, and the build caught
 * why it cannot: `AppHeader` renders on every screen, so importing the hook
 * from the page statically imported the *page* too — which cancelled its
 * `lazy()` and pulled the whole alert feed, its rule form and the model
 * combobox into the main bundle for every reader, including ones who never
 * open it. Vite says so out loud (INEFFECTIVE_DYNAMIC_IMPORT), and a
 * cross-cutting hook belongs beside `auth` and `theme` rather than inside a
 * route.
 *
 * Its own endpoint, not a slice of the feed: a badge drawn on every screen must
 * not fetch and discard a page of alerts to find one number.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export function useUnreadAlerts() {
  return useQuery({
    queryKey: ["alerts-unread"],
    queryFn: ({ signal }) =>
      api.get<{ unread: number }>("/api/alerts/unread-count/", signal),
    // The worker fills the feed on the hot tick (15 min), so anything tighter
    // than this is polling for a change that cannot have happened yet.
    refetchInterval: 5 * 60_000,
  });
}
