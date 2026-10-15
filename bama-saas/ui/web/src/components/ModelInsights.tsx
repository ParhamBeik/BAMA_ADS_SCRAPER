/**
 * A model's history, beside its price on the Price tab: how its price moved,
 * how value falls with build year, and how long its listings stay up.
 *
 * These were the model half of the old Analyse page. The market-wide half is
 * the home page; keeping both questions in one place per answer is the point.
 */
import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs } from "../filters";
import { Async, Card, HowWeKnow, Provenance, SeriesCaveats, Stat, Table } from "../ui";
import { fa, num, pct, toman } from "../format";
import { WindowPicker } from "./WindowPicker";

const Chart = lazy(() => import("@/components/Chart").then((m) => ({ default: m.Chart })));

export const DEFAULT_DAYS = 90;

interface IndexPoint {
  date: string; index_value: number | null; cohort_count: number; ad_count: number;
  gap_days: number | null; low_coverage: boolean;
}
interface Trend extends Partial<Envelope> {
  latest_index: number | null;
  change_pct: number | null;
  base_value: number;
  window: { requested_days: number; days: number; clamped: boolean };
  sample: {
    cohort_count: number; ad_count: number; thin: boolean; min_cohorts: number;
    low_coverage_days: number; max_gap_days: number;
  };
  series: IndexPoint[];
}

interface Retention extends Partial<Envelope> {
  reference_year: number;
  span_years: number;
  retained_over_span_pct: number;
  avg_annual_decline_pct: number | null;
  points: { year_jalali: number; n: number; median_price: number; pct_of_newest: number }[];
}

interface Survival extends Partial<Envelope> {
  n: number;
  delisted: number;
  censored: number;
  median_days: number | null;
  /** When the curve never reaches 50%, the median is censored rather than
   *  missing: this is the "longer than we have watched" bound. */
  median_days_at_least: number | null;
  observed_days: number;
  clean_days: number;
  naive_mean_days_finished_only: number | null;
  curve: { day: number; still_listed: number; at_risk: number }[];
}

/**
 * Which endpoint answers "did this scope's price move".
 *
 * Market, brand and model series are persisted every warm tick. A trim or a
 * single model year is finer than anything stored, so it is computed on demand
 * from the same daily snapshots — persisting a series per trim would multiply
 * the worker's writes for a question most sessions never ask.
 */
export function trendRequest(f: {
  brand?: string; model?: string; variant?: string; year?: string; days: number;
}): string | null {
  if (f.variant || f.year) {
    if (!f.model) return null; // a trim without its model is not a scope
    return `/api/analytics/movement/${qs({
      model: f.model, variant: f.variant, year: f.year, days: f.days,
    })}`;
  }
  if (f.model) return `/api/analytics/market-index/${qs({ scope: "model", id: f.model, days: f.days })}`;
  if (f.brand) return `/api/analytics/market-index/${qs({ scope: "brand", id: f.brand, days: f.days })}`;
  return `/api/analytics/market-index/${qs({ days: f.days })}`;
}

export function TrendPanel({ url }: { url: string }) {
  const trend = useQuery({
    queryKey: ["trend", url],
    queryFn: ({ signal }) => api.get<Trend>(url, signal),
  });

  return (
    <Card title="روند قیمت" action={<WindowPicker defaultDays={DEFAULT_DAYS} />}>
      <Async query={trend} shape="chart" empty="هنوز سابقه‌ای برای این دسته ثبت نشده است.">
        {(data) => {
          const points = data.series ?? [];
          const last = points[points.length - 1];
          return (
            <>
              <div className="grid cols-2" style={{ marginBottom: 10 }}>
                <Stat
                  label="تغییر در این بازه"
                  value={pct(data.change_pct)}
                  tone={data.change_pct == null ? undefined
                        : data.change_pct >= 0 ? "up" : "down"}
                  sub={
                    data.window.clamped
                      ? `${fa(data.window.days)} روز واقعی از ${fa(data.window.requested_days)} روز درخواستی`
                      : `${fa(data.window.days)} روز`
                  }
                />
                <Stat
                  label="شاخص"
                  value={data.latest_index != null ? data.latest_index.toFixed(1) : "—"}
                  sub={`پایه ${fa(data.base_value)} در ابتدای بازه`}
                />
              </div>
              <SeriesCaveats window={data.window} sample={data.sample} />
              {points.length > 1 ? (
                <Suspense fallback={<p className="muted">…</p>}>
                  <Chart
                    xType="time"
                    x={points.map((p) => p.date)}
                    series={[{ name: "شاخص", data: points.map((p) => p.index_value), area: true }]}
                    yFormatter={(v) => v.toFixed(1)}
                    height={230}
                  />
                </Suspense>
              ) : (
                <div className="state">برای رسم نمودار دست‌کم دو روز داده لازم است.</div>
              )}
              {last && (
                <HowWeKnow><p>
                  در آخرین روزِ دارای پوشش کامل از{" "}
                  {num(data.sample.cohort_count)} دسته و{" "}
                  {num(data.sample.ad_count)} آگهی ساخته شده است.
                  هر دسته تنها با خودش مقایسه می‌شود، پس ورود و خروج آگهی‌ها شاخص را
                  جابه‌جا نمی‌کند — روزهای کم‌پوشش هم اصلاً وارد محاسبه نمی‌شوند،
                  چون آنجا «ترکیب آگهی‌ها» همان بخشی است که خزنده به آن نرسیده.
                </p></HowWeKnow>
              )}
              <Provenance envelope={data} />
            </>
          );
        }}
      </Async>
    </Card>
  );
}

export function RetentionPanel({ model, variant }: { model: string; variant?: string }) {
  const retention = useQuery({
    queryKey: ["retention", model, variant],
    queryFn: ({ signal }) =>
      api.get<Retention>(`/api/research/depreciation/${model}/${qs({ variant })}`, signal),
  });

  return (
    <Card title="ارزش بر پایه سال ساخت">
      <Async query={retention} shape="chart">
        {(data) => (
          <>
            <Suspense fallback={<p className="muted">…</p>}>
              <Chart
                x={data.points.map((p) => p.year_jalali)}
                series={[{ name: "درصد ارزش جدیدترین سال",
                           data: data.points.map((p) => p.pct_of_newest), area: true }]}
                yFormatter={(v) => `${v}%`}
                height={210}
              />
            </Suspense>
            <Table head={["سال ساخت", "تعداد آگهی", "میانه قیمت", "درصد جدیدترین سال"]}>
              {data.points.map((p) => (
                <tr key={p.year_jalali}>
                  <td>{p.year_jalali}</td>
                  <td className="num">{num(p.n)}</td>
                  <td className="num">{toman(p.median_price)}</td>
                  <td className="num">{p.pct_of_newest}%</td>
                </tr>
              ))}
            </Table>
            <p className="stat-sub">
              در {num(data.span_years)} سال،{" "}
              {data.retained_over_span_pct}% از ارزش حفظ شده است
              {data.avg_annual_decline_pct != null &&
                ` (حدود ${data.avg_annual_decline_pct}% در سال)`}
              . مبنا سال {data.reference_year} است: جدیدترین سالی که آگهی کافی دارد،
              نه قیمت کارخانه — چنین قیمتی در این داده وجود ندارد.
            </p>
            <Provenance envelope={data} />
          </>
        )}
      </Async>
    </Card>
  );
}

export function SurvivalPanel({
  model, variant, year,
}: { model: string; variant?: string; year?: string }) {
  const survival = useQuery({
    queryKey: ["survival", model, variant, year],
    queryFn: ({ signal }) =>
      api.get<Survival>(`/api/research/liquidity/${model}/${qs({ variant, year })}`, signal),
  });

  return (
    <Card title="مدت ماندن در بازار">
      <Async query={survival} shape="chart">
        {(data) => (
          <>
            <Suspense fallback={<p className="muted">…</p>}>
              {/* A numeric axis, not a category one. These are event *times*
                  (0.26, 0.27, 0.36, 1.05 … 9.98 days) and drawing them evenly
                  spaced made the curve a picture of the sort order — it fell
                  fastest wherever delistings were most frequent, regardless of
                  when they happened. `yMax` because this is a probability and
                  the auto-scaled axis ran to 102%. */}
              <Chart
                xType="value"
                yMax={100}
                x={data.curve.map((p) => p.day)}
                series={[{ name: "هنوز فهرست‌شده",
                           data: data.curve.map((p) => Math.round(p.still_listed * 1000) / 10),
                           area: true }]}
                yFormatter={(v) => `${v}%`}
                height={210}
              />
            </Suspense>
            <div style={{ marginTop: 10 }}>
              <div className="card-title">نیمی از آگهی‌ها تا</div>
              <div className="stat">
                {data.median_days != null
                  ? `${data.median_days.toFixed(0)} روز`
                  : data.median_days_at_least != null
                    ? `بیش از ${data.median_days_at_least.toFixed(0)} روز`
                    : "—"}
              </div>
              <div className="stat-sub">از {num(data.n)} آگهی برداشته شده‌اند</div>
            </div>
            {/* The span every figure above is bounded by, stated the way the
                price index states its own. "4 روز" with nothing saying it came
                from a fortnight of history reads as a fact about the market. */}
            <HowWeKnow><p>
              روی {fa(data.clean_days)} روز سابقه قابل اتکا محاسبه شده و طولانی‌ترین
              آگهیِ زیر نظر {fa(Math.round(data.observed_days))} روز است — هیچ‌کدام
              از این اعداد درباره بازه‌های بلندتر از آن چیزی نمی‌گویند.
            </p></HowWeKnow>
            <Provenance envelope={data} />
          </>
        )}
      </Async>
    </Card>
  );
}
