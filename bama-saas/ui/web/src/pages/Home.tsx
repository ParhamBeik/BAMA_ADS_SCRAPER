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
  AlertTriangle, ArrowLeft, Timer, TrendingDown, TrendingUp,
} from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs, useFilters } from "../filters";
import { Async, Card, Fa, HowWeKnow, Provenance, SeriesCaveats, Stat, Table, type IndexSample } from "../ui";
import { fa, num, pct } from "../format";
import { Sparkline } from "../components/Sparkline";
import { WindowPicker } from "../components/WindowPicker";
import { ModelCombobox } from "../components/ModelCombobox";
import { DealCard, onePerSeller, type DealBoard } from "../components/DealCard";
import { Button } from "../components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";

const Chart = lazy(() => import("@/components/Chart").then((m) => ({ default: m.Chart })));

const DEFAULT_DAYS = 30;
const SHORTLIST = 6;
/** Fetched a page at a time past the shortlist so one-per-seller still fills
 *  it; capped so a board of one dealer's stock cannot page forever. */
const SHORTLIST_POOL = 30;
const SHORTLIST_SCAN = 300;

/** Board pages until SHORTLIST distinct sellers are in hand or it runs out:
 *  a big dealer can own the whole first page. */
async function sellerDiverse(band: string | undefined, signal: AbortSignal): Promise<DealBoard> {
  let board: DealBoard | undefined;
  const rows: DealBoard["results"] = [];
  for (let offset = 0; offset < SHORTLIST_SCAN; offset += SHORTLIST_POOL) {
    board = await api.get<DealBoard>(
      `/api/analytics/deal-scores/${qs({ limit: SHORTLIST_POOL, offset, band })}`, signal);
    rows.push(...(board.results ?? []));
    if (onePerSeller(rows, SHORTLIST).length >= SHORTLIST
        || offset + SHORTLIST_POOL >= (board.count ?? 0)) break;
  }
  return { ...board!, results: rows };
}



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
  /** Ratio of the last day to the first — what changed over the window. */
  change_pct: number;
  /** Robust slope over every point — what is happening *now*. A scope can have
   *  a large change and a flat slope; those are different facts and the board
   *  used to show only the first while implying the second. */
  slope_pct: number;
  recent_slope_pct: number;
  direction: "up" | "down" | "flat";
  turning: boolean;
  turning_up: boolean;
  latest_index: number;
  days: number;
  ad_count: number;
  cohort_count: number;
  /** Only on the segment axes: the numeric range the key stands for. */
  bounds?: string;
  series: number[];
}

interface Movers extends Partial<Envelope> {
  scope: string;
  scopes_ranked: number;
  risers: Mover[];
  fallers: Mover[];
  turning: Mover[];
}

/**
 * The axes the board can be sliced by.
 *
 * Brand and model answer "which nameplate moved". The three below answer "which
 * *part of the market* moved" — the question someone deciding whether to wait a
 * month is actually asking, and one the index could not be asked at all before.
 */
const MOVER_SCOPES = [
  { id: "model", label: "مدل" },
  { id: "brand", label: "برند" },
  { id: "price_band", label: "بازه قیمت" },
  { id: "year_band", label: "سن خودرو" },
  { id: "body_type", label: "نوع بدنه" },
] as const;

type MoverScope = (typeof MOVER_SCOPES)[number]["id"];

const PRICE_BAND_LABEL: Record<string, string> = {
  p0: "زیر 500 میلیون",
  p1: "500 میلیون تا 1 میلیارد",
  p2: "1 تا 2 میلیارد",
  p3: "2 تا 5 میلیارد",
  p4: "بالای 5 میلیارد",
};

const YEAR_BAND_LABEL: Record<string, string> = {
  y0: "تا 3 سال",
  y1: "3 تا 7 سال",
  y2: "8 تا 15 سال",
  y3: "بیش از 15 سال",
};

/**
 * A segment key rendered in Persian.
 *
 * The API returns machine keys (`p2`, `y1`) and the numeric bounds beside them,
 * following the same rule as `reason` codes and cohort flags: prose is composed
 * in the UI, never in the serializer. A body type is already Bama's own word,
 * so it passes straight through.
 */
function scopeLabel(scope: MoverScope, row: Mover): string {
  if (scope === "price_band") return PRICE_BAND_LABEL[row.scope_id] ?? row.name;
  if (scope === "year_band") return YEAR_BAND_LABEL[row.scope_id] ?? row.name;
  return row.name;
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

function toneOf(change: number | null | undefined) {
  if (change == null) return undefined;
  return change >= 0 ? ("up" as const) : ("down" as const);
}

interface MarketRead extends Partial<Envelope> {
  position: "sellers_market" | "buyers_market" | "stable" | "mixed";
  price_direction: "up" | "down" | "flat";
  price_trend: { slope_pct: number; recent_slope_pct: number; turning: boolean };
  flow: "tightening" | "building" | "balanced" | "unknown";
  absorption: number | null;
  arrived: number;
  departed: number;
  window_days: number;
  /** Days the slope really stands on; `window_days` is only what was asked for. */
  index_days: number;
}

/**
 * The one sentence the front page was missing.
 *
 * The index, arrivals and turnover panels below have always been here and have
 * always been correct; what they never did was combine. A rising index with
 * stock clearing and a rising index with inventory piling up are opposite
 * markets, and a reader was left to work that out from three separate cards.
 *
 * Deliberately not a score and not a forecast. It names what the market is
 * doing now and prints both inputs underneath, so a reader who disagrees can
 * see exactly which half they disagree with.
 */
const POSITION: Record<string, { title: string; advice: string }> = {
  sellers_market: {
    title: "بازار به سود فروشنده",
    advice: "قیمت‌ها بالا می‌روند و آگهی‌ها سریع‌تر از ورودشان خارج می‌شوند.",
  },
  buyers_market: {
    title: "بازار به سود خریدار",
    advice: "قیمت‌ها پایین می‌آیند و موجودی در حال انباشت است.",
  },
  stable: {
    title: "بازار آرام",
    advice: "قیمت و جریان ورود و خروج آگهی‌ها متوازن است.",
  },
  mixed: {
    title: "بازار بدون جهت واحد",
    advice: "قیمت و جریان عرضه هم‌جهت نیستند؛ دو شاخص را جداگانه بسنجید.",
  },
};

const DIRECTION_LABEL: Record<string, string> = {
  up: "رو به بالا", down: "رو به پایین", flat: "بدون جهت مشخص",
};

const FLOW_LABEL: Record<string, string> = {
  tightening: "خروج سریع‌تر از ورود",
  building: "انباشت موجودی",
  balanced: "ورود و خروج متوازن",
  unknown: "داده ناکافی",
};

/**
 * One side of a movers board.
 *
 * The sample columns are not optional. The same 4% move means something quite
 * different off three cohorts than off forty, and a leaderboard that prints only
 * the percentage is one where the thinnest scope wins.
 */
function MoversTable({
  rows, direction, scope, empty,
}: {
  rows: Mover[];
  direction: "up" | "down";
  /** Which kind of thing these ids are. Passed, not inferred from whether the
   *  row happens to carry a brand name — a brand with no name recorded would
   *  otherwise be linked to as if its id were a model's. */
  scope: MoverScope;
  empty?: string;
}) {
  const navigate = useNavigate();
  // Only brand and model are scopes the analysis page can be pointed at; a
  // price band is not a car. Linking them anyway would land the reader on an
  // empty analysis of a scope that does not exist.
  const drillable = scope === "model" || scope === "brand";

  if (!rows.length) {
    return (
      <p className="empty-hint">
        {empty ??
          (direction === "up"
            ? "در این بازه هیچ دسته‌ای گران‌تر نشده است."
            : "در این بازه هیچ دسته‌ای ارزان‌تر نشده است.")}
      </p>
    );
  }
  return (
    <div className="movers">
    <Table head={["خودرو", "تغییر", "روند فعلی", "نمودار", "آگهی", "دسته"]}>
      {rows.map((row) => (
        <tr
          key={row.scope_id}
          style={drillable ? { cursor: "pointer" } : undefined}
          onClick={
            drillable
              ? () => navigate(scope === "model"
                ? `/price?model=${row.scope_id}`
                : `/cars?brand=${encodeURIComponent(row.scope_id)}`)
              : undefined
          }
        >
          <td>
            <Fa>{scopeLabel(scope, row)}</Fa>
            {row.brand_name && (
              <div className="stat-sub"><Fa>{row.brand_name}</Fa></div>
            )}
          </td>
          <td className={`num ${direction}`}>{pct(row.change_pct)}</td>
          {/* The distinction the old board could not draw. "Changed 12% over
              the month" and "is currently rising" are different claims, and a
              scope can satisfy the first while flatly contradicting the
              second. */}
          <td>
            <span className={`badge${row.direction === "flat" ? "" : ` ${row.direction}`}`}>
              {DIRECTION_LABEL[row.direction]}
            </span>
            {row.turning && (
              <div className="badge warn" style={{ marginTop: 4 }}>
                در حال چرخش
              </div>
            )}
          </td>
          <td>
            <Sparkline values={row.series} direction={direction} />
          </td>
          <td className="num">{num(row.ad_count)}</td>
          <td className="num">{num(row.cohort_count)}</td>
        </tr>
      ))}
    </Table>
    </div>
  );
}

function MoversPanel({ days }: { days: number }) {
  const filters = useFilters();
  const raw = filters.get("movers");
  const scope: MoverScope =
    (MOVER_SCOPES.find((s) => s.id === raw)?.id as MoverScope) ?? "model";

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
        <TabsList className="mb-3 max-w-full">
          {MOVER_SCOPES.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>{s.label}</TabsTrigger>
          ))}
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

                {/* Its own section, because a scope that has just reversed sits
                    nowhere near either end of a change ranking — which is
                    exactly why a two-column board could never surface the thing
                    a buyer most wants to know. */}
                {data.turning?.length > 0 && (
                  <div style={{ marginTop: 14 }}>
                    <div className="card-title">
                      <AlertTriangle size={12} /> در حال چرخش
                    </div>
                    <MoversTable
                      rows={data.turning}
                      direction="up"
                      scope={scope}
                      empty="هیچ دسته‌ای در حال چرخش نیست."
                    />
                  </div>
                )}

                <HowWeKnow>
                  <p>
                  از میان {num(data.scopes_ranked)} دسته‌ی دارای سابقه کافی. «تغییر» اختلاف ابتدا و انتهای بازه و «روند فعلی» شیب روزهای اخیر است.
                </p>
                </HowWeKnow>
              </>
            )}
          </Async>
        </TabsContent>
      </Tabs>
    </Card>
  );
}

function FlowAndBrands({ days, overview }: { days: number; overview: ReturnType<typeof useQuery<OverviewData>> }) {
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
    <Card title="جریان و سهم بازار">
      <Tabs defaultValue="flow">
        <TabsList className="mb-3 max-w-full">
          <TabsTrigger value="flow">ورود و خروج آگهی‌ها</TabsTrigger>
          <TabsTrigger value="brands">پرآگهی‌ترین برندها</TabsTrigger>
        </TabsList>
        <TabsContent value="flow">
          <div className="grid cols-2">
            <div>
              <div className="card-title">بیشترین آگهی تازه</div>
              <Async query={arrivals} shape="table">
                {(data) => (
                  <Table head={["خودرو", "آگهی تازه", "الان در بازار"]}>
                    {data.models.map((row) => (
                      <tr key={row.model_id}>
                        <td>
                          <Link to={`/price?model=${row.model_id}`}>
                            <Fa>{row.name}</Fa>
                          </Link>
                          {row.brand_name && (
                            <div className="stat-sub"><Fa>{row.brand_name}</Fa></div>
                          )}
                        </td>
                        <td className="num">{num(row.new_listings)}</td>
                        <td className="num">{num(row.listed_now)}</td>
                      </tr>
                    ))}
                  </Table>
                )}
              </Async>
            </div>

            <div>
              {/* The window comes from the answer, not from the request. The panel
                  used to print the requested one — and since the home page asks for
                  30 days against a much shorter clean history, the endpoint refused
                  and this card was empty for every reader, every time. */}
              <div className="row between" style={{ marginBottom: 6 }}>
                <div className="card-title" style={{ margin: 0 }}>سریع‌ترین خروج از بازار</div>
                <span className="stat-sub">
                  <Timer size={12} className="inline align-[-2px]" /> ظرف {fa(turnover.data?.window_days ?? days)} روز
                </span>
              </div>
              <Async query={turnover} shape="table">
                {(data) => (
                  <>
                    {data.clamped && (
                      <span className="badge warn">
                        <AlertTriangle size={11} /> {fa(data.window_days)} روز
                      </span>
                    )}
                    <Table head={["خودرو", "خارج‌شده", "از میان"]}>
                      {data.fastest.map((row) => (
                        <tr key={row.model_id}>
                          <td>
                            <Link to={`/price?model=${row.model_id}`}>
                              <Fa>{row.name}</Fa>
                            </Link>
                            {row.brand_name && (
                              <div className="stat-sub"><Fa>{row.brand_name}</Fa></div>
                            )}
                          </td>
                          <td className="num up">{pct(row.left_pct, 0)}</td>
                          <td className="num">{num(row.n)}</td>
                        </tr>
                      ))}
                    </Table>
                    {/* The distinction the data can actually support. Bama publishes
                        no reason for a listing disappearing, so "sold" would be an
                        assertion nothing here observed. */}
                    <HowWeKnow>
                      <p>
                        فقط آگهی‌های ثبت‌شده دست‌کم {fa(data.window_days)} روز پیش شمرده می‌شوند. «خارج‌شده» یعنی حذف از باما (فروش، پایان اعتبار یا انصراف).
                      </p>
                    </HowWeKnow>
                  </>
                )}
              </Async>
            </div>
          </div>
        </TabsContent>
        <TabsContent value="brands">
          <Async query={overview} shape="table">
            {(data) => (
              /* A bar next to each count. The share is the question being asked
                 of this table, and a column of numbers makes the reader do the
                 division. */
              <Table head={["برند", "تعداد آگهی", "سهم"]}>
                {data.top_brands.map((b) => (
                  <tr key={b.brand__name_fa}>
                    <td><Fa>{b.brand__name_fa}</Fa></td>
                    <td className="num">{num(b.n)}</td>
                    {/* Of the whole market, not of the biggest row: scaled to
                        Peugeot, Peugeot's ~19% drew as a full bar. */}
                    <td style={{ width: "45%" }}>
                      <span
                        className="bar"
                        style={{ width: `${(b.n / Math.max(data.active_listings, 1)) * 100}%` }}
                      />
                      <span className="stat-sub"> {pct((b.n / Math.max(data.active_listings, 1)) * 100, 0)}</span>
                    </td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </TabsContent>
      </Tabs>
    </Card>
  );
}

function BestBuys() {
  const top = useQuery({
    queryKey: ["home-deals"],
    queryFn: ({ signal }) => sellerDiverse(undefined, signal),
  });
  // A quiet day can leave no verdict of "deal"; the strip then shows the
  // cheapest-against-peers cars instead of an empty box at the top of Home.
  const fallback = useQuery({
    queryKey: ["home-deals", "all"],
    enabled: top.data?.results?.length === 0,
    queryFn: ({ signal }) => sellerDiverse("all", signal),
  });
  // Switch to the fallback as soon as top is known empty, so its loading and
  // error states render instead of top's definitive "nothing today".
  const usingFallback = top.data?.results?.length === 0;
  const deals = usingFallback ? fallback : top;

  return (
    <Card
      // Prices here were set within two weeks, freshest first; "today" overclaimed.
      title="بهترین خریدهای تازه"
      action={
        <Button asChild variant="ghost" size="sm">
          <Link to={usingFallback ? "/cars?deals=1&band=all" : "/cars?deals=1"}>همه معامله‌ها <ArrowLeft className="size-4" /></Link>
        </Button>
      }
    >
      <Async query={deals} shape="cards" empty="هنوز امتیازی محاسبه نشده است.">
        {(board) => {
          const rows = onePerSeller(board.results ?? [], SHORTLIST);
          if (!rows.length) {
            return <div className="state">امروز آگهی‌ای به این حد نرسیده است.</div>;
          }
          const ceiling = board.window?.ceiling_pct ?? 25;
          return (
            <>
              <div className="card-grid card-strip strip-fit">
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
  const read = useQuery({
    queryKey: ["market-read", days],
    queryFn: ({ signal }) =>
      api.get<MarketRead>(`/api/analytics/market-read/${qs({ days })}`, signal),
  });

  const readData = read.data && read.data.available !== false ? read.data : undefined;
  const position = readData ? (POSITION[readData.position] ?? POSITION.mixed) : null;

  return (
    <div className="stack">
      {/* What most visitors came for, first: one car's price, then cars. */}
      <Card title="قیمت یک خودرو" action={
        <Button asChild variant="ghost" size="sm"><Link to="/price?mode=budget">با بودجه‌ی من</Link></Button>
      }>
        <ModelCombobox
          onSelect={(model) => model && navigate(`/price?model=${model.id}`)}
          placeholder="نام مدل را بنویسید…"
        />
      </Card>
      <BestBuys />

      <div className="row between gap-y-2" style={{ marginBottom: 2 }}>
        <div className="min-w-0">
          <h1 style={{ margin: 0, fontSize: 22, letterSpacing: "-0.01em" }}>
            نبض بازار خودرو
          </h1>
        </div>
        <WindowPicker className="w-full sm:w-auto" defaultDays={DEFAULT_DAYS} />
      </div>

      <Card title="شاخص قیمت و وضعیت بازار">
        <Async query={index} shape="chart" empty="هنوز سابقه‌ای برای شاخص وجود ندارد.">
          {(data) => {
            const points = data.series ?? [];
            const last = points[points.length - 1];
            return (
              <>
                {position && readData && (
                  <div className="verdict" style={{ marginBottom: 12 }}>
                    <strong>{position.title}</strong>
                    <span className="muted">{position.advice}</span>
                    {readData.price_trend?.turning && (
                      <span className="badge warn">
                        <AlertTriangle size={11} /> در حال چرخش
                      </span>
                    )}
                  </div>
                )}

                <div className="grid cols-4">
                  <Stat
                    label="تغییر شاخص"
                    value={pct(data.change_pct)}
                    tone={toneOf(data.change_pct)}
                    // Persian digits, like the window chips directly above
                    // this card. The two sat side by side reading «۳۰ روز» and
                    // "30 روز" — the same value in two numeral systems.
                    sub={
                      data.window.clamped
                        ? `${fa(data.window.days)} روز از ${fa(data.window.requested_days)} روز · پایه ${fa(data.base_value)}`
                        : `در ${fa(data.window.days)} روز · شاخص ${data.latest_index != null ? data.latest_index.toFixed(1) : "—"}`
                    }
                  />
                  {readData ? (
                    <Stat
                      label="عرضه و تقاضا"
                      value={FLOW_LABEL[readData.flow] ?? "—"}
                      sub={
                        readData.absorption != null
                          ? `${num(readData.departed)} خروج در برابر ${num(readData.arrived)} ورود`
                          : `جهت قیمت: ${DIRECTION_LABEL[readData.price_direction] ?? "—"}`
                      }
                    />
                  ) : (
                    <Stat
                      label="شاخص"
                      value={data.latest_index != null ? data.latest_index.toFixed(1) : "—"}
                      sub={`پایه ${fa(data.base_value)}`}
                    />
                  )}
                  <Async query={overview}>
                    {(o) => (
                      <Stat
                        label="آگهی‌های فعال"
                        value={num(o.active_listings)}
                        // The sub-label used to read "N آگهی قیمت‌دار" over the
                        // same N: it counted priced ads inside a population
                        // that was already priced, so the tile captioned
                        // itself. This is a number that can differ.
                        sub={`${num(o.instalment_listings)} اقساطی کنار گذاشته شده`}
                      />
                    )}
                  </Async>
                  <Async query={overview}>
                    {(o) => (
                      <Stat
                        label="برند و مدل"
                        value={`${num(o.brands)} / ${num(o.models)}`}
                        sub="برند / مدل فعال"
                      />
                    )}
                  </Async>
                </div>

                {/* The index's own sample size, which is much smaller than the
                    sweep's coverage reported in the strip below — printed here
                    so the two are never read as the same number. */}
                <SeriesCaveats window={data.window} sample={data.sample} />
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
                {last && (
                  <HowWeKnow><p>
                    ساخته‌شده از {num(data.sample.cohort_count)} دسته
                    و {num(data.sample.ad_count)} آگهی در آخرین روزِ
                    دارای پوشش کامل. هر دسته فقط با خودش مقایسه می‌شود تا تغییر در
                    ترکیب آگهی‌ها به‌اشتباه حرکت قیمت دیده نشود.
                  </p></HowWeKnow>
                )}
              </>
            );
          }}
        </Async>
      </Card>

      <MoversPanel days={days} />
      <FlowAndBrands days={days} overview={overview} />
      <Provenance envelope={index.data ?? overview.data} />
    </div>
  );
}
