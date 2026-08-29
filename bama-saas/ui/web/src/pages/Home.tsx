/**
 * The market pulse — what a buyer or seller should know before looking at a
 * single car.
 *
 * This page exists because the front door used to be the deal board: a filtered
 * list, useful only to someone who already knew what they were after. Everything
 * here is arithmetic over rows the worker already writes, and every number
 * carries the sample it was computed from.
 *
 * Four questions, in the order they matter:
 *
 *   1. Did the market move? The composition-controlled index, never a raw
 *      median — a median moves whenever the *mix* of listings moves.
 *   2. Which cars moved? Per-brand and per-model series have been built every
 *      warm tick since the index shipped; nothing had ever asked for them.
 *   3. What is arriving, and what is leaving? Supply against demand.
 *   4. What is cheap right now? A shortlist off the deal board.
 *
 * Every panel degrades through `Async`'s "not enough data" branch rather than
 * drawing an empty chart, because several of these series are younger than the
 * windows people will ask for.
 */
import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, PackagePlus, Timer, TrendingDown, TrendingUp,
} from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs, useFilters } from "../filters";
import {
  Async, Card, Fa, Provenance, SeriesCaveats, Stat, Table, fa, pct,
  type IndexSample,
} from "../ui";
import { Sparkline } from "../components/Sparkline";
import { WindowPicker } from "../components/WindowPicker";
import { ModelCombobox } from "../components/ModelCombobox";
import { DealCard, type Deal } from "../components/DealCard";
import { Button } from "../components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";

const Chart = lazy(() => import("../Chart").then((m) => ({ default: m.Chart })));

const DEFAULT_DAYS = 30;
const SHORTLIST = 6;

interface OverviewData extends Envelope {
  active_listings: number;
  /** Listings kept out of every count because their price is a down payment. */
  instalment_listings: number;
  brands: number;
  models: number;
  top_brands: { brand__name_fa: string; n: number }[];
}

interface IndexPoint {
  date: string;
  index_value: number | null;
  return_pct: number | null;
  cohort_count: number;
  ad_count: number;
  /** Calendar days this point is chained across; 1 on a healthy series. */
  gap_days: number | null;
  /** The crawler under-covered this day, so no return was taken from it. */
  low_coverage: boolean;
}

interface MarketIndex extends Partial<Envelope> {
  base_value: number;
  latest_index: number | null;
  change_pct: number | null;
  window: { requested_days: number; days: number; clamped: boolean;
            first_date: string | null; last_date: string | null };
  sample: IndexSample;
  series: IndexPoint[];
}

interface Mover {
  scope_id: string;
  name: string;
  brand_name: string | null;
  change_pct: number;
  latest_index: number;
  days: number;
  ad_count: number;
  cohort_count: number;
  series: number[];
}

interface Movers extends Partial<Envelope> {
  scope: string;
  scopes_ranked: number;
  risers: Mover[];
  fallers: Mover[];
}

interface TurnoverRow {
  model_id: number;
  name: string;
  brand_name: string | null;
  n: number;
  left_within_window: number;
  left_pct: number;
}

interface Turnover extends Partial<Envelope> {
  /** The window actually used, which is not always the one asked for. */
  window_days: number;
  requested_days: number;
  clamped: boolean;
  clean_days: number;
  fastest: TurnoverRow[];
}

interface ArrivalRow {
  model_id: number;
  name: string;
  brand_name: string | null;
  new_listings: number;
  listed_now: number;
}

interface Arrivals extends Partial<Envelope> {
  window_days: number;
  models: ArrivalRow[];
}

interface DealBoard extends Envelope {
  window: { window_days: number; min_discount_pct: number; ceiling_pct: number };
  results: Deal[];
}

function toneOf(change: number | null | undefined) {
  if (change == null) return undefined;
  return change >= 0 ? ("up" as const) : ("down" as const);
}

/**
 * One side of a movers board.
 *
 * The sample columns are not optional. The same 4% move means something quite
 * different off three cohorts than off forty, and a leaderboard that prints only
 * the percentage is one where the thinnest scope wins.
 */
function MoversTable({
  rows, direction, scope,
}: {
  rows: Mover[];
  direction: "up" | "down";
  /** Which kind of thing these ids are. Passed, not inferred from whether the
   *  row happens to carry a brand name — a brand with no name recorded would
   *  otherwise be linked to as if its id were a model's. */
  scope: "model" | "brand";
}) {
  const navigate = useNavigate();
  if (!rows.length) {
    return (
      <p className="empty-hint">
        {direction === "up"
          ? "در این بازه هیچ دسته‌ای گران‌تر نشده است."
          : "در این بازه هیچ دسته‌ای ارزان‌تر نشده است."}
      </p>
    );
  }
  return (
    <Table head={["خودرو", "تغییر", "روند", "آگهی", "دسته"]}>
      {rows.map((row) => (
        <tr
          key={row.scope_id}
          style={{ cursor: "pointer" }}
          onClick={() => navigate(`/analyse?${scope}=${row.scope_id}`)}
        >
          <td>
            <Fa>{row.name}</Fa>
            {row.brand_name && (
              <div className="stat-sub"><Fa>{row.brand_name}</Fa></div>
            )}
          </td>
          <td className={`num ${direction}`}>{pct(row.change_pct)}</td>
          <td>
            <Sparkline values={row.series} direction={direction} />
          </td>
          <td className="num">{row.ad_count.toLocaleString("en-US")}</td>
          <td className="num">{row.cohort_count.toLocaleString("en-US")}</td>
        </tr>
      ))}
    </Table>
  );
}

function MoversPanel({ days }: { days: number }) {
  const filters = useFilters();
  const scope = filters.get("movers") === "brand" ? "brand" : "model";

  const movers = useQuery({
    queryKey: ["movers", scope, days],
    queryFn: ({ signal }) =>
      api.get<Movers>(`/api/analytics/movers/${qs({ scope, days })}`, signal),
  });

  return (
    <Card title="بیشترین تغییر قیمت">
      <Tabs
        value={scope}
        onValueChange={(next) => filters.set({ movers: next === "model" ? null : next })}
      >
        <TabsList className="mb-3">
          <TabsTrigger value="model">بر پایه مدل</TabsTrigger>
          <TabsTrigger value="brand">بر پایه برند</TabsTrigger>
        </TabsList>
        <TabsContent value={scope}>
          <Async query={movers} shape="table">
            {(data) => (
              <>
                <div className="grid cols-2">
                  <div>
                    <div className="card-title">
                      <TrendingUp size={12} /> بیشترین افزایش
                    </div>
                    <MoversTable rows={data.risers} direction="up" scope={scope} />
                  </div>
                  <div>
                    <div className="card-title">
                      <TrendingDown size={12} /> بیشترین کاهش
                    </div>
                    <MoversTable rows={data.fallers} direction="down" scope={scope} />
                  </div>
                </div>
                <p className="empty-hint">
                  از میان {data.scopes_ranked.toLocaleString("en-US")} دسته‌ای که
                  سابقه کافی داشتند. «تغییر» حرکت شاخص هم‌ترکیب است، نه اختلاف
                  میانه — تغییر در ترکیب آگهی‌ها روی آن اثر ندارد. ستون‌های آگهی و
                  دسته می‌گویند این عدد بر چه پایه‌ای ساخته شده است.
                </p>
                <Provenance envelope={data} compact />
              </>
            )}
          </Async>
        </TabsContent>
      </Tabs>
    </Card>
  );
}

function SupplyAndDemand({ days }: { days: number }) {
  const arrivals = useQuery({
    queryKey: ["arrivals", days],
    queryFn: ({ signal }) =>
      api.get<Arrivals>(`/api/analytics/arrivals/${qs({ days })}`, signal),
  });
  const turnover = useQuery({
    queryKey: ["turnover", days],
    queryFn: ({ signal }) =>
      api.get<Turnover>(`/api/analytics/turnover/${qs({ days })}`, signal),
  });

  return (
    <div className="grid cols-2">
      <Card title="بیشترین آگهی تازه">
        <p className="stat-sub" style={{ marginTop: 0 }}>
          <PackagePlus size={12} /> چه خودروهایی بیشتر از همه به بازار اضافه شده‌اند
        </p>
        <Async query={arrivals} shape="table">
          {(data) => (
            <>
              <Table head={["خودرو", "آگهی تازه", "الان در بازار"]}>
                {data.models.map((row) => (
                  <tr key={row.model_id}>
                    <td>
                      <Link to={`/analyse?model=${row.model_id}`}>
                        <Fa>{row.name}</Fa>
                      </Link>
                      {row.brand_name && (
                        <div className="stat-sub"><Fa>{row.brand_name}</Fa></div>
                      )}
                    </td>
                    <td className="num">{row.new_listings.toLocaleString("en-US")}</td>
                    <td className="num">{row.listed_now.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </Table>
              <Provenance envelope={data} compact />
            </>
          )}
        </Async>
      </Card>

      <Card title="سریع‌ترین خروج از بازار">
        {/* The window comes from the answer, not from the request. The panel
            used to print the requested one — and since the home page asks for
            30 days against a much shorter clean history, the endpoint refused
            and this card was empty for every reader, every time. */}
        <p className="stat-sub" style={{ marginTop: 0 }}>
          <Timer size={12} /> چه سهمی از آگهی‌ها ظرف{" "}
          {fa(turnover.data?.window_days ?? days)} روز از سایت برداشته شدند
        </p>
        <Async query={turnover} shape="table">
          {(data) => (
            <>
              {data.clamped && (
                <p className="badge warn" style={{ display: "block", lineHeight: 1.7 }}>
                  <AlertTriangle size={11} /> بازه به {fa(data.window_days)} روز کوتاه
                  شد، چون سابقه قابل اتکای ما {fa(data.clean_days)} روز است و برای
                  اندازه‌گیری «خروج ظرف {fa(data.requested_days)} روز» کافی نیست.
                </p>
              )}
              <Table head={["خودرو", "خارج‌شده", "از میان"]}>
                {data.fastest.map((row) => (
                  <tr key={row.model_id}>
                    <td>
                      <Link to={`/analyse?model=${row.model_id}`}>
                        <Fa>{row.name}</Fa>
                      </Link>
                      {row.brand_name && (
                        <div className="stat-sub"><Fa>{row.brand_name}</Fa></div>
                      )}
                    </td>
                    <td className="num up">{pct(row.left_pct, 0)}</td>
                    <td className="num">{row.n.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </Table>
              {/* The distinction the data can actually support. Bama publishes
                  no reason for a listing disappearing, so "sold" would be an
                  assertion nothing here observed. */}
              <p className="empty-hint">
                فقط آگهی‌هایی شمرده می‌شوند که دست‌کم {fa(data.window_days)} روز پیش
                ثبت شده‌اند، تا هر آگهی فرصت کامل این بازه را داشته باشد. «خارج‌شده»
                یعنی از باما برداشته شده — فروش، پایان اعتبار یا انصراف فروشنده، که
                باما تفاوتشان را اعلام نمی‌کند.
              </p>
              <Provenance envelope={data} compact />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}

function BestBuys() {
  const deals = useQuery({
    queryKey: ["home-deals"],
    queryFn: ({ signal }) =>
      api.get<DealBoard>(`/api/analytics/deal-scores/${qs({ limit: SHORTLIST })}`, signal),
  });

  return (
    <Card
      title="بهترین خریدهای امروز"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to="/deals">همه معامله‌ها <ArrowLeft className="size-4" /></Link>
        </Button>
      }
    >
      <Async query={deals} shape="cards" empty="هنوز امتیازی محاسبه نشده است.">
        {(board) => {
          const rows = board.results ?? [];
          if (!rows.length) {
            return <div className="state">امروز آگهی‌ای به این حد نرسیده است.</div>;
          }
          const ceiling = board.window?.ceiling_pct ?? 25;
          return (
            <>
              {/* Both thresholds are measured from the current board, so the
                  page quotes them rather than describing a filter whose value
                  it does not know. */}
              <p className="stat-sub" style={{ marginTop: 0 }}>
                آگهی‌های {fa(board.window?.window_days)} روز گذشته که دست‌کم{" "}
                {/* One decimal: the floor is a percentile of today's board and
                    "7.15%" offers a precision that is an artefact of which
                    listings happen to be live this hour. */}
                {board.window ? pct(board.window.min_discount_pct, 1) : "—"} زیر
                میانه قیمت آگهی‌های مشابه خود هستند. خودروهایی که خودِ آگهی
                ارزانی‌شان را توضیح می‌دهد در «نیازمند بررسی»‌اند.
              </p>
              <div className="card-grid">
                {rows.map((deal) => (
                  <DealCard
                    key={deal.code}
                    deal={deal}
                    suspect={(deal.discount_pct ?? 0) > ceiling}
                  />
                ))}
              </div>
            </>
          );
        }}
      </Async>
    </Card>
  );
}

export function Home() {
  const filters = useFilters();
  const navigate = useNavigate();
  const days = filters.getInt("days") ?? DEFAULT_DAYS;

  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.get<OverviewData>("/api/analytics/overview/", signal),
  });
  const index = useQuery({
    queryKey: ["market-index", days],
    queryFn: ({ signal }) =>
      api.get<MarketIndex>(`/api/analytics/market-index/${qs({ days })}`, signal),
  });

  return (
    <div className="stack">
      <div className="row between" style={{ marginBottom: 2 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, letterSpacing: "-0.01em" }}>
            نبض بازار خودرو
          </h1>
          <p className="stat-sub" style={{ margin: 0 }}>
            وضعیت امروز بازار، بر پایه آگهی‌های باما
          </p>
        </div>
        <WindowPicker defaultDays={DEFAULT_DAYS} />
      </div>

      <Card title="شاخص قیمت با کنترل ترکیب آگهی‌ها">
        <Async query={index} shape="chart" empty="هنوز سابقه‌ای برای شاخص وجود ندارد.">
          {(data) => {
            const points = data.series ?? [];
            const last = points[points.length - 1];
            return (
              <>
                <div className="grid cols-4">
                  <Stat
                    label="تغییر در این بازه"
                    value={pct(data.change_pct)}
                    tone={toneOf(data.change_pct)}
                    // Persian digits, like the window chips directly above
                    // this card. The two sat side by side reading «۳۰ روز» and
                    // "30 روز" — the same value in two numeral systems.
                    sub={
                      data.window.clamped
                        ? `${fa(data.window.days)} روز واقعی از ${fa(data.window.requested_days)} روز درخواستی`
                        : `${fa(data.window.days)} روز`
                    }
                  />
                  <Stat
                    label="شاخص"
                    value={data.latest_index != null ? data.latest_index.toFixed(1) : "—"}
                    sub={`پایه ${fa(data.base_value)}`}
                  />
                  <Async query={overview}>
                    {(o) => (
                      <Stat
                        label="آگهی‌های فعال"
                        value={o.active_listings.toLocaleString("en-US")}
                        // The sub-label used to read "N آگهی قیمت‌دار" over the
                        // same N: it counted priced ads inside a population
                        // that was already priced, so the tile captioned
                        // itself. This is a number that can differ.
                        sub={`${o.instalment_listings.toLocaleString("en-US")} آگهی اقساطی کنار گذاشته شده`}
                      />
                    )}
                  </Async>
                  <Async query={overview}>
                    {(o) => (
                      <Stat
                        label="برند و مدل"
                        value={`${o.brands.toLocaleString("en-US")} / ${o.models.toLocaleString("en-US")}`}
                        sub="برند / مدل با آگهی فعال"
                      />
                    )}
                  </Async>
                </div>

                {/* The index's own sample size, which is much smaller than the
                    sweep's coverage reported in the strip below — printed here
                    so the two are never read as the same number. */}
                <SeriesCaveats window={data.window} sample={data.sample} />
                {last && (
                  <p className="stat-sub">
                    ساخته‌شده از {data.sample.cohort_count.toLocaleString("en-US")} دسته
                    و {data.sample.ad_count.toLocaleString("en-US")} آگهی در آخرین روزِ
                    دارای پوشش کامل. هر دسته فقط با خودش مقایسه می‌شود، تا تغییر در
                    ترکیب آگهی‌های موجود به‌اشتباه حرکت قیمت به نظر نرسد.
                  </p>
                )}
                {points.length ? (
                  <Suspense fallback={<p className="muted">…</p>}>
                    {/* A real time axis. As a category axis the four multi-day
                        holes in this series were drawn as ordinary one-day
                        steps, which put the market's largest "daily" move on a
                        gap the crawler never covered. */}
                    <Chart
                      xType="time"
                      x={points.map((p) => p.date)}
                      series={[{ name: "شاخص", data: points.map((p) => p.index_value), area: true }]}
                      yFormatter={(v) => v.toFixed(1)}
                      height={220}
                    />
                  </Suspense>
                ) : (
                  <div className="state">هنوز سابقه‌ای برای شاخص وجود ندارد.</div>
                )}
                <Provenance envelope={data} />
              </>
            );
          }}
        </Async>
      </Card>

      <MoversPanel days={days} />
      <SupplyAndDemand days={days} />
      <BestBuys />

      <Card title="یک خودرو را بررسی کنید">
        <p className="stat-sub" style={{ marginTop: 0 }}>
          روند قیمت، توزیع قیمت، افت ارزش و مدت ماندن در بازار — برای هر مدل، تیپ و
          سال ساخت.
        </p>
        <div style={{ maxWidth: 360 }}>
          <ModelCombobox
            onSelect={(model) => model && navigate(`/analyse?model=${model.id}`)}
            placeholder="نام مدل را بنویسید…"
          />
        </div>
      </Card>

      <Async query={overview}>
        {(data) => (
          <Card title="پرآگهی‌ترین برندها">
            {/* A bar next to each count. The share is the question being asked
                of this table, and a column of numbers makes the reader do the
                division. */}
            <Table head={["برند", "تعداد آگهی", "سهم"]}>
              {data.top_brands.map((b) => (
                <tr key={b.brand__name_fa}>
                  <td><Fa>{b.brand__name_fa}</Fa></td>
                  <td className="num">{b.n.toLocaleString("en-US")}</td>
                  <td style={{ width: "45%" }}>
                    <span
                      className="bar"
                      style={{ width: `${(b.n / data.top_brands[0].n) * 100}%` }}
                    />
                  </td>
                </tr>
              ))}
            </Table>
            <Provenance envelope={data} compact />
          </Card>
        )}
      </Async>
    </div>
  );
}
