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
  p0: "زیر ۵۰۰ میلیون",
  p1: "۵۰۰ میلیون تا ۱ میلیارد",
  p2: "۱ تا ۲ میلیارد",
  p3: "۲ تا ۵ میلیارد",
  p4: "بالای ۵ میلیارد",
};

const YEAR_BAND_LABEL: Record<string, string> = {
  y0: "تا ۳ سال",
  y1: "۳ تا ۷ سال",
  y2: "۸ تا ۱۵ سال",
  y3: "بیش از ۱۵ سال",
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

interface DealBoard extends Envelope {
  window: { window_days: number; min_discount_pct: number; ceiling_pct: number };
  results: Deal[];
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
    advice: "قیمت‌ها بالا می‌روند و آگهی‌ها سریع‌تر از ورودشان از بازار خارج می‌شوند. اگر خریدارید، منتظر ماندن احتمالاً گران‌تر تمام می‌شود.",
  },
  buyers_market: {
    title: "بازار به سود خریدار",
    advice: "قیمت‌ها پایین می‌آیند و موجودی در حال انباشت است. اگر عجله ندارید، صبر کردن به نفع شماست.",
  },
  stable: {
    title: "بازار آرام",
    advice: "نه قیمت جهت مشخصی دارد و نه ورود و خروج آگهی‌ها نامتوازن است. زمان خرید را وضعیت خودِ آگهی تعیین می‌کند، نه بازار.",
  },
  mixed: {
    title: "نشانه‌ها هم‌جهت نیستند",
    advice: "قیمت و جریان عرضه یک چیز نمی‌گویند، پس یک توصیه واحد از این داده در نمی‌آید. دو عدد زیر را جدا بخوانید.",
  },
};

const DIRECTION_LABEL: Record<string, string> = {
  up: "رو به بالا", down: "رو به پایین", flat: "بدون جهت مشخص",
};

const FLOW_LABEL: Record<string, string> = {
  tightening: "خروج سریع‌تر از ورود",
  building: "انباشت موجودی",
  balanced: "ورود و خروج متوازن",
  unknown: "برای اظهار نظر کافی نیست",
};

function MarketReadPanel({ days }: { days: number }) {
  const read = useQuery({
    queryKey: ["market-read", days],
    queryFn: ({ signal }) =>
      api.get<MarketRead>(`/api/analytics/market-read/${qs({ days })}`, signal),
  });

  return (
    <Card title="بازار الان کجاست؟">
      <Async query={read} shape="table">
        {(data) => {
          const position = POSITION[data.position] ?? POSITION.mixed;
          return (
            <>
              <p style={{ margin: "0 0 4px", fontSize: 18, fontWeight: 700 }}>
                {position.title}
              </p>
              <p className="stat-sub" style={{ marginTop: 0 }}>{position.advice}</p>

              {/* The two inputs, never hidden behind the conclusion. A reader
                  who disagrees needs to see which half they disagree with. */}
              <div className="grid cols-2" style={{ marginTop: 10 }}>
                <Stat
                  label="جهت قیمت"
                  value={DIRECTION_LABEL[data.price_direction] ?? "—"}
                  tone={data.price_direction === "flat" ? undefined
                        : data.price_direction === "up" ? "up" : "down"}
                  sub={`شیب شاخص در ${fa(data.window_days)} روز`}
                />
                <Stat
                  label="عرضه و تقاضا"
                  value={FLOW_LABEL[data.flow] ?? "—"}
                  sub={
                    data.absorption != null
                      ? `${data.departed.toLocaleString("en-US")} خروج در برابر ${data.arrived.toLocaleString("en-US")} ورود`
                      : "تعداد آگهی‌های ثبت و حذف‌شده کم است"
                  }
                />
              </div>

              {data.price_trend?.turning && (
                <p className="badge warn" style={{ display: "block", lineHeight: 1.7 }}>
                  <AlertTriangle size={11} /> روند هفته گذشته خلاف جهت بلندمدت است —
                  ممکن است بازار در حال چرخش باشد.
                </p>
              )}

              <p className="empty-hint">
                «جهت قیمت» از شیب شاخص هم‌ترکیب می‌آید، نه از اختلاف دو روز؛ و
                «عرضه و تقاضا» نسبت آگهی‌های خارج‌شده به آگهی‌های تازه در همین
                بازه است. هیچ‌کدام پیش‌بینی نیستند — وضعیت امروزند.
              </p>
              <Provenance envelope={data} compact />
            </>
          );
        }}
      </Async>
    </Card>
  );
}

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
    <Table head={["خودرو", "تغییر", "روند فعلی", "نمودار", "آگهی", "دسته"]}>
      {rows.map((row) => (
        <tr
          key={row.scope_id}
          style={drillable ? { cursor: "pointer" } : undefined}
          onClick={
            drillable
              ? () => navigate(`/analyse?${scope}=${row.scope_id}`)
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
          <td className="num">{row.ad_count.toLocaleString("en-US")}</td>
          <td className="num">{row.cohort_count.toLocaleString("en-US")}</td>
        </tr>
      ))}
    </Table>
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
        <TabsList className="mb-3">
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
                    <p className="stat-sub" style={{ marginTop: 0 }}>
                      روند هفته گذشته این دسته‌ها خلاف جهت بلندمدتشان است.
                    </p>
                    <MoversTable
                      rows={data.turning}
                      direction="up"
                      scope={scope}
                      empty="هیچ دسته‌ای در حال چرخش نیست."
                    />
                  </div>
                )}

                <p className="empty-hint">
                  از میان {data.scopes_ranked.toLocaleString("en-US")} دسته‌ای که
                  سابقه کافی داشتند. «تغییر» حرکت شاخص هم‌ترکیب بین ابتدا و انتهای
                  بازه است و «روند فعلی» شیب همه روزها — یک دسته می‌تواند در کل
                  بازه بالا رفته باشد ولی همین حالا رو به پایین باشد. ستون‌های
                  آگهی و دسته می‌گویند این اعداد بر چه پایه‌ای ساخته شده‌اند.
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

      {/* Directly under the index, because it is the reading *of* the index
          plus the two flow panels below — and a reader who takes only one
          thing from this page should take this. */}
      <MarketReadPanel days={days} />
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
