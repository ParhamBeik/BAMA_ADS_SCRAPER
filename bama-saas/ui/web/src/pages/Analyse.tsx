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
import { AlertTriangle } from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs, useFilters } from "../filters";
import {
  Async, Card, Fa, Provenance, SeriesCaveats, Stat, Table, fa, pct, toman,
} from "../ui";
import { ScopePicker, useScopeLabel } from "../components/ScopePicker";
import { WindowPicker } from "../components/WindowPicker";
import { useModelLabel } from "../components/ModelCombobox";
import { DealCard, type Deal } from "../components/DealCard";
import { FollowButton } from "../components/FollowButton";
import { Button } from "../components/ui/button";

const Chart = lazy(() => import("../Chart").then((m) => ({ default: m.Chart })));

const DEFAULT_DAYS = 90;
const SHORTLIST = 8;

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
  /** Whether these are listings or an estimate of listings. Never omitted:
   *  comparing an adjusted band against a filtered one is comparing two
   *  different kinds of claim, and the reader has to be able to tell. */
  basis: {
    mode: "unconditioned" | "filtered" | "adjusted";
    condition: string | null;
    mileage_bucket: number | null;
    filtered_n: number | null;
    factor?: number;
    reason?: string;
  };
}

const CONDITION_CHOICES = [
  { value: "clean", label: "بدون رنگ" },
  { value: "cosmetic", label: "لکه / خط و خش" },
  { value: "painted", label: "رنگ‌شده" },
  { value: "structural", label: "تعویض / تصادفی" },
];

/** The ladder `pricing.MILEAGE_BUCKETS` is calibrated on. The API refuses
 *  anything off it, so these values must stay in step with that tuple. */
const MILEAGE_CHOICES = [
  { value: "0", label: "زیر ۲۰ هزار" },
  { value: "20000", label: "۲۰ تا ۵۰ هزار" },
  { value: "50000", label: "۵۰ تا ۱۰۰ هزار" },
  { value: "100000", label: "۱۰۰ تا ۱۵۰ هزار" },
  { value: "150000", label: "۱۵۰ تا ۲۰۰ هزار" },
  { value: "200000", label: "۲۰۰ تا ۳۰۰ هزار" },
  { value: "300000", label: "بالای ۳۰۰ هزار" },
];

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
                <p className="empty-hint">
                  در آخرین روزِ دارای پوشش کامل از{" "}
                  {data.sample.cohort_count.toLocaleString("en-US")} دسته و{" "}
                  {data.sample.ad_count.toLocaleString("en-US")} آگهی ساخته شده است.
                  هر دسته تنها با خودش مقایسه می‌شود، پس ورود و خروج آگهی‌ها شاخص را
                  جابه‌جا نمی‌کند — روزهای کم‌پوشش هم اصلاً وارد محاسبه نمی‌شوند،
                  چون آنجا «ترکیب آگهی‌ها» همان بخشی است که خزنده به آن نرسیده.
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

/**
 * The two controls that turn "what does this model cost" into "what does *my*
 * car cost".
 *
 * Without them one histogram averages a 300,000 km repainted example with a
 * 20,000 km clean one and reports their combined spread as the model's price,
 * which describes neither car. They are chips rather than selects because the
 * whole point is that they are quick to try and quick to drop.
 */
function ConditionControls() {
  const filters = useFilters();
  const condition = filters.get("condition");
  const bucket = filters.get("mileage_bucket");

  const chip = (active: boolean) => `preset${active ? " on" : ""}`;
  return (
    <div className="stack" style={{ gap: 8, marginBottom: 12 }}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className="stat-sub" style={{ minWidth: 76 }}>وضعیت بدنه</span>
        {CONDITION_CHOICES.map((c) => (
          <button
            key={c.value}
            type="button"
            className={chip(condition === c.value)}
            aria-pressed={condition === c.value}
            onClick={() =>
              filters.set({ condition: condition === c.value ? null : c.value })
            }
          >
            {c.label}
          </button>
        ))}
      </div>
      <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className="stat-sub" style={{ minWidth: 76 }}>کارکرد</span>
        {MILEAGE_CHOICES.map((m) => (
          <button
            key={m.value}
            type="button"
            className={chip(bucket === m.value)}
            aria-pressed={bucket === m.value}
            onClick={() =>
              filters.set({ mileage_bucket: bucket === m.value ? null : m.value })
            }
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}

/**
 * Whether the reader is looking at cars or at an estimate of cars.
 *
 * Stated rather than implied. Filtering to a condition drops most trims below
 * the minimum sample, so the useful answer is usually the unconditioned shape
 * shifted by the haircut `pricing` measures catalogue-wide — which is a
 * genuinely different kind of claim from "here are the listings", and a reader
 * comparing two bands needs to know which one they have.
 */
function BasisNote({ basis }: { basis: Distribution["basis"] }) {
  if (!basis || basis.mode === "unconditioned") {
    if (basis?.reason === "no_measured_adjustment") {
      return (
        <p className="badge warn" style={{ display: "block", lineHeight: 1.7 }}>
          <AlertTriangle size={11} /> برای این وضعیت آگهی کافی نبود و اختلاف
          اندازه‌گیری‌شده‌ای هم در کاتالوگ وجود ندارد، پس همان توزیع کلی نشان داده
          می‌شود — بدون تعدیل.
        </p>
      );
    }
    return null;
  }
  if (basis.mode === "filtered") {
    return (
      <p className="stat-sub">
        بر پایه {basis.filtered_n?.toLocaleString("en-US")} آگهی واقعی با همین
        وضعیت — بدون تخمین.
      </p>
    );
  }
  return (
    <p className="badge warn" style={{ display: "block", lineHeight: 1.7 }}>
      <AlertTriangle size={11} /> آگهی‌های این وضعیت کم بودند
      {basis.filtered_n != null && ` (${basis.filtered_n})`}، پس توزیع کلی با
      اختلاف اندازه‌گیری‌شده کل کاتالوگ تعدیل شده است. این یک تخمین است، نه
      فهرست آگهی‌های واقعی.
    </p>
  );
}

function DistributionPanel({ query }: { query: ReturnType<typeof useQuery<Distribution>> }) {
  return (
    <Card title="توزیع قیمت">
      <ConditionControls />
      <Async query={query} shape="chart">
        {(data) => {
          const d = data.distribution;
          const h = data.histogram;
          return (
            <>
              <BasisNote basis={data.basis} />
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
            <div className="grid cols-2" style={{ marginTop: 10 }}>
              <div>
                <div className="card-title">میانه (با احتساب آگهی‌های فعال)</div>
                {/* An em dash here left the figure labelled "misleading" as the
                    only number on the panel with a value. The median is not
                    missing — more than half these listings are still up, so it
                    is censored, and its lower bound is a real answer. */}
                <div className="stat">
                  {data.median_days != null
                    ? `${data.median_days.toFixed(0)} روز`
                    : data.median_days_at_least != null
                      ? `بیش از ${data.median_days_at_least.toFixed(0)} روز`
                      : "—"}
                </div>
                <div className="stat-sub">
                  از {data.n.toLocaleString("en-US")} آگهی
                  {data.median_days == null && (
                    <> — بیش از نیمی از آنها هنوز فعال‌اند، پس میانه واقعی از این
                      عدد بزرگ‌تر است</>
                  )}
                </div>
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
            {/* The span every figure above is bounded by, stated the way the
                price index states its own. "۴ روز" with nothing saying it came
                from a fortnight of history reads as a fact about the market. */}
            <p className="empty-hint">
              روی {fa(data.clean_days)} روز سابقه قابل اتکا محاسبه شده و طولانی‌ترین
              آگهیِ زیر نظر {fa(Math.round(data.observed_days))} روز است — هیچ‌کدام
              از این اعداد درباره بازه‌های بلندتر از آن چیزی نمی‌گویند.
            </p>
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
  const condition = filters.get("condition");
  const mileageBucket = filters.get("mileage_bucket");

  const distribution = useQuery<Distribution>({
    queryKey: ["distribution", brand, model, variant, year, condition, mileageBucket],
    queryFn: ({ signal }) =>
      api.get<Distribution>(
        `/api/analytics/distribution/${qs({
          brand, model, variant, year,
          condition, mileage_bucket: mileageBucket,
        })}`, signal),
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
          <div className="row">
            {/* Follow the scope, not a listing. The listing you are looking at
                will be gone in a fortnight; the car you want to buy will not. */}
            <FollowButton scope={{ brand, model, variant, year }} />
            {(brand || model) && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  filters.set({
                    brand: null, model: null, variant: null, year: null,
                    condition: null, mileage_bucket: null,
                  })
                }
              >
                بازگشت به کل بازار
              </Button>
            )}
          </div>
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
