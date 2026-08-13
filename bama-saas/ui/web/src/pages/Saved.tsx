/**
 * Saved cars, and any price drops on them.
 *
 * Both panels read the same list. A saved ad already carries its current and
 * previous price, so a "price drops" feed is a filter over rows we have rather
 * than a second endpoint, an alert table and a notification inbox — which is
 * what this screen used to be.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, TrendingDown } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Paginated } from "../api/client";
import { Async, Card, Fa, Table, pct, toman } from "../ui";

interface SavedAd {
  code: string;
  title?: string;
  current_price?: number | null;
  latest_price_drop?: {
    old_price: number;
    new_price: number;
    drop_pct: number;
    observed_at: string;
  } | null;
}

function dropPct(row: SavedAd): number | null {
  const drop = row.latest_price_drop;
  if (!drop || drop.old_price <= 0 || drop.new_price >= drop.old_price) return null;
  return drop.drop_pct;
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
      <Card title="آگهی‌های ذخیره‌شده">
        <Async query={saved} empty="هنوز چیزی ذخیره نشده.">
          {(data) =>
            data.results.length ? (
              <Table head={["آگهی", "قیمت", ""]}>
                {data.results.map((row) => (
                  <tr key={row.code}>
                    <td>
                      <Link to={`/listing/${row.code}`}>
                        <Fa>{row.title ?? row.code}</Fa>
                      </Link>
                    </td>
                    <td className="num">{toman(row.current_price ?? null)}</td>
                    <td className="num">
                      <button onClick={() => remove.mutate(row.code)} aria-label="حذف">
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">هنوز چیزی ذخیره نشده.</div>
            )
          }
        </Async>
      </Card>

      <Card title="کاهش قیمت">
        <Async query={saved} empty="کاهش قیمتی ثبت نشده.">
          {(data) => {
            const drops = data.results
              .map((row) => ({ row, change: dropPct(row) }))
              .filter((d): d is { row: SavedAd; change: number } => d.change != null)
              .sort((a, b) => a.change - b.change);
            if (!drops.length) {
              return (
                <div className="state">
                  هیچ‌کدام از آگهی‌های ذخیره‌شده ارزان‌تر نشده‌اند.
                </div>
              );
            }
            return (
              <Table head={["آگهی", "قیمت پیشین", "قیمت فعلی", "تغییر", "زمان"]}>
                {drops.map(({ row, change }) => (
                  <tr key={row.code}>
                    <td>
                      <Link to={`/listing/${row.code}`}>
                        <Fa>{row.title ?? row.code}</Fa>
                      </Link>
                    </td>
                    <td className="num">{toman(row.latest_price_drop?.old_price ?? null)}</td>
                    <td className="num">{toman(row.current_price ?? null)}</td>
                    <td className="num up">
                      <TrendingDown size={12} /> {pct(change)}
                    </td>
                    <td className="num">
                      {row.latest_price_drop?.observed_at
                        ? new Date(row.latest_price_drop.observed_at).toLocaleDateString("fa-IR")
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
