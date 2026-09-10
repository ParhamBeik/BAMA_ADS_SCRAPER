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
import { api } from "../api";
import type { Paginated } from "../api";
import { Async, Card, Fa, Table, pct, toman } from "../ui";
import { Sparkline } from "../components/Sparkline";

interface SavedAd {
  code: string;
  ad_title?: string | null;
  ad_price?: number | null;
  previous_price?: number | null;
  price_changed_at?: string | null;
}

interface Followed {
  id: number;
  brand_name: string;
  model_name: string;
  variant_name: string;
  year_jalali: number | null;
  analyse_path: string;
  available: boolean;
  change_pct: number | null;
  spark: (number | null)[];
}

function followedLabel(row: Followed): string {
  return [row.brand_name, row.model_name, row.variant_name, row.year_jalali]
    .filter(Boolean)
    .join(" ") || "بدون نام";
}

function directionOf(change: number | null): "up" | "down" | "flat" {
  if (change == null || Math.abs(change) < 0.05) return "flat";
  return change > 0 ? "up" : "down";
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

  const followed = useQuery({
    queryKey: ["watchlists", "digest"],
    queryFn: ({ signal }) =>
      api.get<{ results: Followed[] }>("/api/watchlists/digest/", signal),
  });

  const remove = useMutation({
    mutationFn: (code: string) => api.del(`/api/favorites/${code}/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["favorites"] }),
  });

  const unfollow = useMutation({
    mutationFn: (id: number) => api.del(`/api/watchlists/${id}/`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["watchlists"] });
    },
  });

  return (
    <div className="stack">
      <Card title="خودروهای دنبال‌شده">
        <Async query={followed} empty="هنوز خودرویی را دنبال نکرده‌اید." shape="table">
          {(data) =>
            // Four columns do not fit a 390px panel: measured, the row's minimum
            // came to 403px against 324px of usable width, and the car's name
            // lost — auto table layout wrapped «پژو ۲۰۶ تیپ ۲ ۱۴۰۱» to one word
            // per line rather than scroll. `.hide-narrow` is the column that
            // gives way below 560px, because it is the only one whose
            // information the row already carries: the percentage beside it
            // states the same move, with a sign and a magnitude. Its header is
            // `sr-only`, so nothing is announced and then hidden.
            data.results.length ? (
              <Table head={["خودرو", "تغییر ۳۰ روز", <span key="sp" className="sr-only hide-narrow">روند</span>, <span key="rm" className="sr-only">توقف دنبال کردن</span>]}>
                {data.results.map((row) => {
                  const dir = directionOf(row.change_pct);
                  return (
                    <tr key={row.id}>
                      <td>
                        <Link to={row.analyse_path}>
                          <Fa>{followedLabel(row)}</Fa>
                        </Link>
                      </td>
                      <td className={`num ${dir === "up" ? "up" : dir === "down" ? "down" : ""}`}>
                        {row.available ? pct(row.change_pct) : "—"}
                      </td>
                      <td className="hide-narrow">
                        <Sparkline values={row.spark} direction={dir} />
                      </td>
                      <td className="num">
                        <button
                          onClick={() => unfollow.mutate(row.id)}
                          aria-label="توقف دنبال کردن"
                        >
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </Table>
            ) : (
              <div className="state">
                <strong>هنوز خودرویی را دنبال نکرده‌اید.</strong>
                <p className="empty-hint">
                  در صفحه «تحلیل» روی «دنبال کردن» بزنید تا روند قیمت همان
                  خودرو اینجا دیده شود.
                </p>
              </div>
            )
          }
        </Async>
      </Card>

      <div className="grid cols-2">
        <Card title="آگهی‌های ذخیره‌شده">
          <Async query={saved} empty="هنوز چیزی ذخیره نشده است." shape="table">
            {(data) =>
              data.results.length ? (
                // The last column holds one icon button per row. An empty header
                // left it unnamed, so a screen reader moving across a row
                // announced the button with no column to attach it to.
                <Table head={["آگهی", "قیمت", <span key="rm" className="sr-only">حذف</span>]}>
                  {data.results.map((row) => (
                    <tr key={row.code}>
                      <td>
                        <Link to={`/listing/${row.code}`}>
                          <Fa>{row.ad_title || row.code}</Fa>
                        </Link>
                      </td>
                      <td className="num">{toman(row.ad_price ?? null)}</td>
                      <td className="num">
                        <button onClick={() => remove.mutate(row.code)} aria-label="حذف">
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </Table>
              ) : (
                <div className="state">
                  <strong>هنوز چیزی ذخیره نشده است.</strong>
                  <p className="empty-hint">
                    در صفحه «معامله‌ها» یا «جست‌وجو» روی «ذخیره» بزنید تا تغییر
                    قیمت آن آگهی اینجا دنبال شود.
                  </p>
                </div>
              )
            }
          </Async>
        </Card>

        <Card title="کاهش قیمت‌ها">
          <Async query={saved} empty="کاهش قیمتی ثبت نشده است." shape="table">
            {(data) => {
              const drops = data.results
                .map((row) => ({ row, change: dropPct(row) }))
                .filter((d): d is { row: SavedAd; change: number } => d.change != null)
                .sort((a, b) => a.change - b.change);
              if (!drops.length) {
                return (
                  <div className="state">
                    هیچ‌کدام از آگهی‌های ذخیره‌شده شما ارزان‌تر نشده‌اند.
                  </div>
                );
              }
              return (
                <Table head={[
                  "آگهی",
                  <span key="prev" className="hide-narrow">قیمت پیشین</span>,
                  "قیمت فعلی",
                  "تغییر",
                  <span key="when" className="hide-narrow">زمان</span>,
                ]}>
                  {drops.map(({ row, change }) => {
                    const when = row.price_changed_at
                      ? new Date(row.price_changed_at).toLocaleDateString("fa-IR")
                      : "—";
                    return (
                      <tr key={row.code}>
                        <td>
                          <Link to={`/listing/${row.code}`}>
                            <Fa>{row.ad_title || row.code}</Fa>
                          </Link>
                          {/* The date, moved under the title instead of dropped.
                              Five columns is two too many at 390px, and the two
                              that give way are chosen for different reasons: the
                              previous price the row can restate (current price
                              and the fall are both here), the date it cannot —
                              nothing else says when. So it moves rather than
                              goes, and the row loses a column without losing a
                              fact. The wide layout keeps its own column and
                              hides this one, so neither is announced twice. */}
                          <small className="stat-sub show-narrow">{when}</small>
                        </td>
                        <td className="num hide-narrow">{toman(row.previous_price ?? null)}</td>
                        <td className="num">{toman(row.ad_price ?? null)}</td>
                        <td className="num up">
                          <TrendingDown size={12} /> {pct(change)}
                        </td>
                        <td className="num hide-narrow">{when}</td>
                      </tr>
                    );
                  })}
                </Table>
              );
            }}
          </Async>
        </Card>
      </div>
    </div>
  );
}
