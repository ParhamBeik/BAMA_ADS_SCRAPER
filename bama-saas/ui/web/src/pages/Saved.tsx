/**
 * Saved cars, and any price drops on them.
 *
 * Both panels read the same list. A saved ad already carries its current and
 * previous price, so a "price drops" feed is a filter over rows we have rather
 * than a second endpoint, an alert table and a notification inbox — which is
 * what this screen used to be.
 *
 * The field names below are `ad_title` / `ad_price` / `previous_price` because
 * that is what `FavoriteSerializer` emits (apps/accounts/views.py). This file
 * previously declared a `{title, current_price, latest_price_drop:{…}}` shape
 * that the API has never returned, so every row rendered its raw code with a
 * dash for a price and the drops panel was permanently empty — including when
 * a drop had genuinely been recorded.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, TrendingDown } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Paginated } from "../api/client";
import { Async, Card, Fa, Table, pct, toman } from "../ui";

interface SavedAd {
  code: string;
  ad_title?: string | null;
  ad_price?: number | null;
  previous_price?: number | null;
  price_changed_at?: string | null;
}

/** Percent fall from the recorded previous price, or null if it did not fall. */
function dropPct(row: SavedAd): number | null {
  const { previous_price: prev, ad_price: now } = row;
  if (prev == null || now == null || prev <= 0 || now >= prev) return null;
  return ((now - prev) / prev) * 100;
}

export function Saved() {
  const client = useQueryClient();

  const saved = useQuery({
    queryKey: ["favorites"],
    queryFn: ({ signal }) => api.get<Paginated<SavedAd>>("/api/favorites/", signal),
  });

  const remove = useMutation({
    mutationFn: (code: string) => api.del(`/api/favorites/${code}/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["favorites"] }),
  });

  return (
    <div className="grid cols-2" dir="rtl">
      <Card title="Saved listings">
        <Async query={saved} empty="Nothing saved yet." shape="table">
          {(data) =>
            data.results.length ? (
              <Table head={["Listing", "Price", ""]}>
                {data.results.map((row) => (
                  <tr key={row.code}>
                    <td>
                      <Link to={`/listing/${row.code}`}>
                        <Fa>{row.ad_title || row.code}</Fa>
                      </Link>
                    </td>
                    <td className="num">{toman(row.ad_price ?? null)}</td>
                    <td className="num">
                      <button onClick={() => remove.mutate(row.code)} aria-label="Remove">
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">
                <strong>Nothing saved yet.</strong>
                <p className="empty-hint">
                  Click "Save" on the Deals or Explore page to track that
                  listing's price changes here.
                </p>
              </div>
            )
          }
        </Async>
      </Card>

      <Card title="Price drops">
        <Async query={saved} empty="No price drops recorded." shape="table">
          {(data) => {
            const drops = data.results
              .map((row) => ({ row, change: dropPct(row) }))
              .filter((d): d is { row: SavedAd; change: number } => d.change != null)
              .sort((a, b) => a.change - b.change);
            if (!drops.length) {
              return (
                <div className="state">
                  None of your saved listings have gotten cheaper.
                </div>
              );
            }
            return (
              <Table head={["Listing", "Previous price", "Current price", "Change", "Time"]}>
                {drops.map(({ row, change }) => (
                  <tr key={row.code}>
                    <td>
                      <Link to={`/listing/${row.code}`}>
                        <Fa>{row.ad_title || row.code}</Fa>
                      </Link>
                    </td>
                    <td className="num">{toman(row.previous_price ?? null)}</td>
                    <td className="num">{toman(row.ad_price ?? null)}</td>
                    <td className="num up">
                      <TrendingDown size={12} /> {pct(change)}
                    </td>
                    <td className="num">
                      {row.price_changed_at
                        ? new Date(row.price_changed_at).toLocaleDateString("en-US")
                        : "—"}
                    </td>
                  </tr>
                ))}
              </Table>
            );
          }}
        </Async>
      </Card>
    </div>
  );
}
