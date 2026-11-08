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
import { Async, Card, Fa, Table, useUndo } from "../ui";
import { faDate, km, pct, toman, trimNote } from "../format";
import { Sparkline } from "../components/Sparkline";

interface SavedAd {
  code: string;
  ad_title?: string | null;
  ad_price?: number | null;
  ad_year?: number | null;
  ad_mileage?: number | null;
  ad_city?: string;
  ad_variant?: string;
  ad_image?: string;
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

  // One mis-tap used to lose a saved ad or a followed car for good.
  const { offer, bar } = useUndo();

  const remove = useMutation({
    mutationFn: (row: SavedAd) => api.del(`/api/favorites/${row.code}/`),
    onSuccess: (_, row) => {
      client.invalidateQueries({ queryKey: ["favorites"] });
      offer("آگهی از ذخیره‌شده‌ها حذف شد", async () => {
        await api.post("/api/favorites/", { code: row.code });
        client.invalidateQueries({ queryKey: ["favorites"] });
      });
    },
  });

  const unfollow = useMutation({
    mutationFn: (row: Followed) => api.del(`/api/watchlists/${row.id}/`),
    onSuccess: (_, row) => {
      client.invalidateQueries({ queryKey: ["watchlists"] });
      // The digest row carries the car as a /price link; follow it back from that.
      const q = new URLSearchParams(row.analyse_path.split("?")[1] ?? "");
      offer("دنبال کردن متوقف شد", async () => {
        await api.post("/api/watchlists/", {
          brand_slug: "",
          model: q.get("model") ? Number(q.get("model")) : null,
          variant: q.get("variant") ? Number(q.get("variant")) : null,
          year_jalali: q.get("year") ? Number(q.get("year")) : null,
        });
        client.invalidateQueries({ queryKey: ["watchlists"] });
      });
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
              <Table head={["خودرو", "تغییر 30 روز", <span key="sp" className="sr-only hide-narrow">روند</span>, <span key="rm" className="sr-only">توقف دنبال کردن</span>]}>
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
                          onClick={() => unfollow.mutate(row)}
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
                  در «قیمت» یا صفحه‌ی هر آگهی روی «دنبال کردن» بزنید تا روند قیمت همان
                  خودرو اینجا دیده شود.
                </p>
                <Link to="/price" className="empty-action">رفتن به «قیمت»</Link>
              </div>
            )
          }
        </Async>
      </Card>

      <Card title="آگهی‌های ذخیره‌شده">
        <Async query={saved} empty="هنوز چیزی ذخیره نشده است." shape="table">
          {(data) =>
            data.results.length ? (
              // The last column holds one icon button per row. An empty header
              // left it unnamed, so a screen reader moving across a row
              // announced the button with no column to attach it to.
              <Table head={["آگهی", "قیمت", <span key="rm" className="sr-only">حذف</span>]}>
                {data.results.map((row) => {
                  const change = dropPct(row);
                  return (
                    <tr key={row.code}>
                      <td>
                        <div className="saved-row">
                          {row.ad_image && <img src={row.ad_image} alt="" loading="lazy" />}
                          <div>
                            <Link to={`/listing/${row.code}`}>
                              <Fa>{row.ad_title || row.code}</Fa>
                            </Link>
                            {/* Several saved 207s read the same without these. */}
                            <div className="stat-sub">
                              <Fa>{[trimNote(row.ad_title, row.ad_variant), row.ad_year,
                                    row.ad_mileage != null && km(row.ad_mileage), row.ad_city]
                                .filter(Boolean).join(" · ")}</Fa>
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="num">
                        <div>{toman(row.ad_price ?? null)}</div>
                        {change != null && (
                          <div className="stat-sub" style={{ marginTop: 2 }}>
                            <span className="badge ok">
                              <TrendingDown size={11} /> {pct(change, 0)}
                            </span>{" "}
                            <span className="deal-median">{toman(row.previous_price ?? null)}</span>
                            {row.price_changed_at && ` · ${faDate(row.price_changed_at)}`}
                          </div>
                        )}
                      </td>
                      <td className="num">
                        <button onClick={() => remove.mutate(row)} aria-label="حذف">
                          <Trash2 size={13} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </Table>
            ) : (
              <div className="state">
                <strong>هنوز چیزی ذخیره نشده است.</strong>
                <p className="empty-hint">
                  در «خودروها» روی «ذخیره» بزنید تا تغییر
                  قیمت آن آگهی اینجا دنبال شود.
                </p>
                <Link to="/cars" className="empty-action">رفتن به «خودروها»</Link>
              </div>
            )
          }
        </Async>
      </Card>
      {bar}
    </div>
  );
}
