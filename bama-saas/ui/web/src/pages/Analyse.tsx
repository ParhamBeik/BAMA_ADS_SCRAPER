/**
 * Analyse — every question this data can answer, about any scope.
 *
 * Replaces two screens that answered a slice each: a "Market" page that was
 * three counters and one market-wide chart, and a "Research" page that could
 * only ever talk about a whole model. The scope now runs market → brand → model
 * → trim → model year, and every panel redraws for whatever is selected.
 *
 * Two of these panels needed no backend work at all: the depreciation and
 * time-on-market endpoints have always accepted a trim and a model year, and
 * nothing had ever sent them.
 *
 * The scope lives in the URL, so an analysis is a link you can send someone.
 *
 * Every panel goes through `Async`. A scope thin enough that a number would be
 * dishonest returns "not enough data" and says why — that is a result, not an
 * error, and it must never be rendered as an empty chart that reads as zero.
 */
import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs, useFilters } from "../filters";
import {
  Async, Card, Fa, Provenance, Stat, Table, pct, toman,
} from "../ui";
import { ScopePicker, useScopeLabel } from "../components/ScopePicker";
import { WindowPicker } from "../components/WindowPicker";
import { useModelLabel } from "../components/ModelCombobox";
import { DealCard, type Deal } from "../components/DealCard";
import { Button } from "../components/ui/button";

const Chart = lazy(() => import("../Chart").then((m) => ({ default: m.Chart })));

const DEFAULT_DAYS = 90;
const SHORTLIST = 8;

interface IndexPoint { date: string; index_value: number | null; cohort_count: number; ad_count: number }
interface Trend extends Partial<Envelope> {
  latest_index: number | null;
  change_pct: number | null;
  base_value: number;
  window: { requested_days: number; days: number; clamped: boolean };
  series: IndexPoint[];
}

interface Bucket { from: number; to: number; n: number }
interface Distribution extends Partial<Envelope> {
  distribution: {
    min: number; p10: number; p25: number; median: number;
    p75: number; p90: number; max: number; count: number;
  };
  histogram: { from: number; to: number;
               buckets: Bucket[]; below: number; above: number };
  cities: { name: string; n: number }[];
  years: { year_jalali: number; n: number }[];
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
  naive_mean_days_finished_only: number | null;
  curve: { day: number; still_listed: number; at_risk: number }[];
}

interface DealBoard extends Envelope {
  count: number;
  window: { ceiling_pct: number };
  results: Deal[];
}

/**
 * Which endpoint answers "did this scope's price move".
 *
 * Market, brand and model series are persisted every warm tick. A trim or a
 * single model year is finer than anything stored, so it is computed on demand
 * from the same daily snapshots — persisting a series per trim would multiply
 * the worker's writes for a question most sessions never ask.
 */
function trendRequest(f: {
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

function TrendPanel({ url }: { url: string }) {
  const trend = useQuery({
    queryKey: ["trend", url],
    queryFn: ({ signal }) => api.get<Trend>(url, signal),
  });

  return (
    <Card title="روند قیمت">
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
                      ? `${data.window.days} روز واقعی از ${data.window.requested_days} روز درخواستی`
                      : `${data.window.days} روز`
                  }
                />
                <Stat
                  label="شاخص"
                  value={data.latest_index != null ? data.latest_index.toFixed(1) : "—"}
                  sub={`پایه ${data.base_value} در ابتدای بازه`}
                />
              </div>
              {points.length > 1 ? (
                <Suspense fallback={<p className="muted">…</p>}>
                  <Chart
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
                <p className="empty-hint">
                  در آخرین روز از {last.cohort_count.toLocaleString("en-US")} دسته و{" "}
                  {last.ad_count.toLocaleString("en-US")} آگهی ساخته شده است. هر دسته
                  تنها با خودش مقایسه می‌شود، پس ورود و خروج آگهی‌ها شاخص را جابه‌جا
                  نمی‌کند.
                </p>
              )}
              <Provenance envelope={data} />
            </>
          );
        }}
      </Async>
    </Card>
  );
}

function DistributionPanel({ query }: { query: ReturnType<typeof useQuery<Distribution>> }) {
  return (
    <Card title="توزیع قیمت">
      <Async query={query} shape="chart">
        {(data) => {
          const d = data.distribution;
          const h = data.histogram;
          return (
            <>
              {/* Only numbers go in a Stat's value — it sets in the mono ledger
                  face, which has no Persian glyphs, so a word placed there gets
                  a fallback family and reads as broken text beside the digits. */}
              <div className="grid cols-4" style={{ marginBottom: 10 }}>
                <Stat label="میانه" value={toman(d.median)} sub={`${d.count.toLocaleString("en-US")} آگهی`} />
                <Stat label="نیمه میانی" value={`${toman(d.p25)} – ${toman(d.p75)}`} sub="از چارک اول تا سوم" />
                <Stat label="ارزان‌ترین ده درصد" value={toman(d.p10)} sub="و پایین‌تر" />
                <Stat label="گران‌ترین ده درصد" value={toman(d.p90)} sub="و بالاتر" />
              </div>
              <Suspense fallback={<p className="muted">…</p>}>
                <Chart
                  x={h.buckets.map((b) => toman(b.from))}
                  series={[{ name: "تعداد آگهی", data: h.buckets.map((b) => b.n), type: "bar" }]}
                  yFormatter={(v) => v.toLocaleString("en-US")}
                  height={200}
                />
              </Suspense>
              {/* What sits outside the drawn band, stated rather than dropped.
                  The band is p10-p90 because one typo listing at 5.8 trillion
                  toman would otherwise put every real car in the first bar. */}
              <p className="empty-hint">
                نمودار بازه {toman(h.from)} تا {toman(h.to)} تومان را نشان می‌دهد.
                {h.below > 0 && ` ${h.below.toLocaleString("en-US")} آگهی ارزان‌تر`}
                {h.below > 0 && h.above > 0 && " و"}
                {h.above > 0 && ` ${h.above.toLocaleString("en-US")} آگهی گران‌تر`}
                {(h.below > 0 || h.above > 0) && " بیرون از این بازه‌اند و در نمودار دیده نمی‌شوند."}
                {" "}آگهی‌های اقساطی کنار گذاشته شده‌اند، چون عددشان پیش‌پرداخت است نه
                قیمت خودرو.
              </p>

              {data.cities.length > 1 && (
                <Table head={["شهر", "تعداد آگهی", "سهم"]}>
                  {data.cities.map((c) => (
                    <tr key={c.name}>
                      <td><Fa>{c.name}</Fa></td>
                      <td className="num">{c.n.toLocaleString("en-US")}</td>
                      <td style={{ width: "45%" }}>
                        <span className="bar" style={{ width: `${(c.n / data.cities[0].n) * 100}%` }} />
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
              <Provenance envelope={data} />
            </>
          );
        }}
      </Async>
    </Card>
  );
}

function RetentionPanel({ model, variant }: { model: string; variant?: string }) {
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
                  <td className="num">{p.n.toLocaleString("en-US")}</td>
                  <td className="num">{toman(p.median_price)}</td>
                  <td className="num">{p.pct_of_newest}%</td>
                </tr>
              ))}
            </Table>
            <p className="stat-sub">
              در {data.span_years.toLocaleString("en-US")} سال،{" "}
              {data.retained_over_span_pct}٪ از ارزش حفظ شده است
              {data.avg_annual_decline_pct != null &&
                ` (حدود ${data.avg_annual_decline_pct}٪ در سال)`}
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

function SurvivalPanel({
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
              <Chart
                x={data.curve.map((p) => p.day)}
                series={[{ name: "هنوز فهرست‌شده",
                           data: data.curve.map((p) => Math.round(p.still_listed * 1000) / 10),
                           area: true }]}
                yFormatter={(v) => `${v}%`}
                height={210}
              />
            </Suspense>
            <div className="grid cols-2" style={{ marginTop: 10 }}>
              <div>
                <div className="card-title">میانه (با احتساب آگهی‌های فعال)</div>
                <div className="stat">
                  {data.median_days != null ? `${data.median_days.toFixed(0)} روز` : "—"}
                </div>
                <div className="stat-sub">از {data.n.toLocaleString("en-US")} آگهی</div>
              </div>
              <div>
                <div className="card-title">میانگین ساده (گمراه‌کننده)</div>
                <div className="stat warn">
                  {data.naive_mean_days_finished_only != null
                    ? `${data.naive_mean_days_finished_only.toFixed(0)} روز`
                    : "—"}
                </div>
                {/* Kept beside the censored median deliberately: the gap between
                    the two is the size of the error every simpler version of
                    this number carries. */}
                <div className="stat-sub">
                  تنها {data.delisted.toLocaleString("en-US")} آگهی پایان‌یافته را
                  می‌شمارد و {data.censored.toLocaleString("en-US")} آگهی هنوز فعال را
                  نادیده می‌گیرد
                </div>
              </div>
            </div>
            <Provenance envelope={data} />
          </>
        )}
      </Async>
    </Card>
  );
}

function ScopedDeals({ brand, model }: { brand?: string; model?: string }) {
  const deals = useQuery({
    queryKey: ["scoped-deals", brand, model],
    queryFn: ({ signal }) =>
      api.get<DealBoard>(
        `/api/analytics/deal-scores/${qs({ brand, model, limit: SHORTLIST })}`, signal),
  });

  return (
    <Card
      title="معامله‌های این دسته"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to={`/deals${qs({ brand, model })}`}>همه معامله‌ها</Link>
        </Button>
      }
    >
      <Async query={deals} shape="cards">
        {(board) => {
          const rows = board.results ?? [];
          if (!rows.length) {
            return (
              <div className="state">
                <strong>امروز آگهی زیرقیمتی در این دسته نیست.</strong>
                <p className="empty-hint">
                  فهرست معامله‌ها فقط آگهی‌های تازه را می‌سنجد، پس خالی بودنش یعنی
                  همین امروز چیزی پیدا نشده، نه اینکه این خودرو ارزان نمی‌شود.
                </p>
              </div>
            );
          }
          const ceiling = board.window?.ceiling_pct ?? 25;
          return (
            <div className="card-grid">
              {rows.map((deal) => (
                <DealCard key={deal.code} deal={deal}
                          suspect={(deal.discount_pct ?? 0) > ceiling} />
              ))}
            </div>
          );
        }}
      </Async>
    </Card>
  );
}

export function Analyse() {
  const filters = useFilters();
  const brand = filters.get("brand");
  const model = filters.get("model");
  const variant = filters.get("variant");
  const year = filters.get("year");
  const days = filters.getInt("days") ?? DEFAULT_DAYS;

  const selectedModel = useModelLabel(model);
  const scopeLabel = useScopeLabel(selectedModel?.name_fa, selectedModel?.brand_name);

  // One request feeds both the distribution panel and the model-year options in
  // the scope picker, so the year list can only ever offer years that have data.
  // It carries the year list even when it refuses to draw a distribution, or
  // picking a thin year would disable the control that got you there.
  const distribution = useQuery<Distribution>({
    queryKey: ["distribution", brand, model, variant, year],
    queryFn: ({ signal }) =>
      api.get<Distribution>(
        `/api/analytics/distribution/${qs({ brand, model, variant, year })}`, signal),
  });

  const trendUrl = trendRequest({ brand, model, variant, year, days });

  return (
    <div className="stack">
      <div>
        <h1 style={{ margin: 0, fontSize: 22, letterSpacing: "-0.01em" }}>تحلیل بازار</h1>
        <p className="stat-sub" style={{ margin: 0 }}>
          هرچه داده اجازه بدهد، از کل بازار تا یک تیپ و سال ساخت مشخص.
        </p>
      </div>

      <Card
        title={`دامنه تحلیل — ${scopeLabel}`}
        action={
          (brand || model) && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                filters.set({ brand: null, model: null, variant: null, year: null })
              }
            >
              بازگشت به کل بازار
            </Button>
          )
        }
      >
        <ScopePicker years={distribution.data?.years} />
        <div className="row between" style={{ marginTop: 12 }}>
          <span className="stat-sub">بازه زمانی روند قیمت</span>
          <WindowPicker defaultDays={DEFAULT_DAYS} />
        </div>
      </Card>

      {trendUrl && <TrendPanel url={trendUrl} />}
      <DistributionPanel query={distribution} />

      {model ? (
        <>
          <RetentionPanel model={model} variant={variant} />
          <SurvivalPanel model={model} variant={variant} year={year} />
        </>
      ) : (
        <Card title="افت ارزش و مدت ماندن در بازار">
          <div className="state">
            <strong>برای این دو تحلیل یک مدل انتخاب کنید.</strong>
            <p className="empty-hint">
              افت ارزش، مقایسه سال‌های ساخت یک مدل با یکدیگر است و مدت ماندن در بازار
              هم برای هر مدل جداگانه معنا دارد — در سطح کل بازار عددی که بشود به آن
              اتکا کرد به دست نمی‌آید.
            </p>
          </div>
        </Card>
      )}

      <ScopedDeals brand={brand} model={model} />
    </div>
  );
}
