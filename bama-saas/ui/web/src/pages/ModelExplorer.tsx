/**
 * Model-first, evidence-led exploration of Bama cash asking prices.
 *
 * Two states, and both have to be useful.
 *
 * With no model chosen the page used to be one sentence and a search box — a
 * reader who did not already know the exact name of the car they wanted had
 * nothing to look at. It is now a browser: brands as chips, ranked by how many
 * listings they carry, and the models under them as tiles that can be sorted by
 * volume or by name. The search box stays at the top for the reader who does
 * know.
 *
 * With a model chosen, the controls come before the numbers they change. The
 * "refine" card used to sit *under* both price summaries, so a reader read the
 * range, scrolled, changed a trim, and had to scroll back up to see what moved.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowUpDown, X } from "lucide-react";
import { api } from "../api";
import { qs, useFilters } from "../filters";
import { km, num, toman } from "../format";
import { Async, Card, Fa, ListingActions, Thumb } from "../ui";
import { ModelCombobox, type ModelRow } from "../components/ModelCombobox";
import { Button } from "../components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";

type Range = { p10: number; p25: number; median: number; p75: number; p90: number };
type Summary = { count: number; thin: boolean; asking_range: Range | null;
  observed_min: number | null; observed_max: number | null };
type Facet = Summary & { value: string | number | null; label: string | number | null };
type Ad = { code: string; title: string; current_price: number; year_jalali: number | null;
  mileage: number | null; body_status: string; city__name_fa: string | null;
  image_url: string; listing_url: string };
type Exploration = {
  model: { id: number; name: string; brand: string };
  basis: string;
  national: Summary;
  selected: Summary;
  facets: { variants: Facet[]; years: Facet[]; conditions: Facet[];
    mileage: Facet[]; cities: Facet[] };
  ads: Ad[];
  evidence: { feed_coverage_complete: boolean; minimum_for_range: number;
    latest_selected_sighting: string | null };
  purchase_price: null;
  purchase_price_reason: string;
};

const REFINE_KEYS = ["variant", "year", "city", "condition", "mileage_min", "mileage_max"] as const;

function PriceSummary({ title, summary, emphasis = false }: {
  title: string; summary: Summary; emphasis?: boolean;
}) {
  return (
    <div className="stack">
      <h2 className={emphasis ? "text-lg" : "text-base"}><Fa>{title}</Fa></h2>
      <p className="muted">{num(summary.count)} آگهی با قیمت نقدی</p>
      {summary.asking_range ? (
        <>
          <p className="text-xl font-bold">
            <span className="font-mono tabular-nums">{toman(summary.asking_range.p25)}</span>
            {" تا "}
            <span className="font-mono tabular-nums">{toman(summary.asking_range.p75)}</span>
            {" تومان"}
          </p>
          <p className="stat-sub">
            میانه <b className="font-mono">{toman(summary.asking_range.median)}</b>
            {" · "}بازه‌ی ۸۰٪ آگهی‌ها{" "}
            <b className="font-mono">{toman(summary.asking_range.p10)}</b> تا{" "}
            <b className="font-mono">{toman(summary.asking_range.p90)}</b>
          </p>
        </>
      ) : (
        <p>نمونه برای بازهٔ قابل اتکا کافی نیست. این آگهی‌ها همچنان شواهد واقعی‌اند.</p>
      )}
    </div>
  );
}

const ANY = "__any__";

/** A Radix select, not a native one: the native list ignored the theme and
 *  opened as a full-screen OS picker on Android with no counts aligned. */
function FacetSelect({ label, value, options, onChange, format }: {
  label: string; value?: string; options: Facet[];
  onChange: (value: string | null) => void;
  format?: (option: Facet) => string;
}) {
  const usable = options.filter((o) => o.value != null && o.value !== "" && o.value !== "unknown");
  return (
    <label className="grid gap-1.5">
      <span className="text-muted-foreground text-xs font-semibold">{label}</span>
      <Select value={value ?? ANY} onValueChange={(next) => onChange(next === ANY ? null : next)}>
        <SelectTrigger className="w-full" aria-label={label}><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>همه</SelectItem>
          {usable.map((o) => (
            <SelectItem key={String(o.value)} value={String(o.value)}>
              <Fa>{format ? format(o) : String(o.label ?? o.value)}</Fa>
              <span className="text-muted-foreground ms-2 font-mono text-[11px]">{num(o.count)}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

const mileageLabel = (o: Facet) => {
  const edge = Number(o.value);
  return edge === 0 ? "صفر کیلومتر" : `${num(edge)} کیلومتر`;
};

export function ModelExplorer() {
  const filters = useFilters();
  const model = filters.get("model");
  const params = {
    variant: filters.get("variant"), year: filters.get("year"),
    city: filters.get("city"), condition: filters.get("condition"),
    mileage_min: filters.get("mileage_min"), mileage_max: filters.get("mileage_max"),
  };
  const refined = REFINE_KEYS.some((k) => params[k]);
  const exploration = useQuery({
    queryKey: ["model-exploration", model, params],
    enabled: Boolean(model),
    queryFn: ({ signal }) => api.get<Exploration>(
      `/api/models/${model}/explore/${qs(params)}`, signal,
    ),
  });

  const clearRefine = () => filters.set(Object.fromEntries(REFINE_KEYS.map((k) => [k, null])));
  const pick = (next: ModelRow | null) => filters.set({
    model: next?.id, variant: null, year: null, city: null, condition: null,
    mileage_min: null, mileage_max: null,
  });

  return (
    <div className="stack" dir="rtl">
      <Card title="کاوش قیمت یک مدل">
        <p className="mb-3">
          مدل را جست‌وجو کنید یا از فهرست زیر انتخاب کنید؛ تفاوت تیپ، سال، کارکرد،
          وضعیت بدنه و شهر را با آگهی‌های پشتیبان ببینید.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1 sm:max-w-md">
            <ModelCombobox value={model} onSelect={pick} />
          </div>
          {model && (
            <Button variant="ghost" size="sm" onClick={() => pick(null)}>
              <X className="size-4" /> مدل دیگر
            </Button>
          )}
        </div>
      </Card>

      {!model && <ModelBrowser onPick={pick} />}

      {model && <Async query={exploration}>
        {(data) => {
          // Everything in the model page's selection that the listings page
          // can also filter on. Condition is left out on purpose: here it is
          // Bama's raw body-status text, there it is the four-band grouping,
          // and passing one as the other would silently match nothing.
          const toListings = `/explore${qs({
            model: data.model.id,
            variant: params.variant,
            year_min: params.year, year_max: params.year,
            city: params.city,
            mileage_min: params.mileage_min, mileage_max: params.mileage_max,
          })}`;
          return (
            <>
              <Card
                title="دقیق‌تر کنید"
                action={refined
                  ? <Button variant="ghost" size="sm" onClick={clearRefine}>پاک کردن</Button>
                  : undefined}
              >
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
                  <FacetSelect label="تیپ" value={params.variant} options={data.facets.variants}
                    onChange={(value) => filters.set({ variant: value })} />
                  <FacetSelect label="سال ساخت" value={params.year}
                    options={[...data.facets.years].sort((a, b) => Number(b.value) - Number(a.value))}
                    onChange={(value) => filters.set({ year: value })} />
                  <FacetSelect label="وضعیت بدنه" value={params.condition} options={data.facets.conditions}
                    onChange={(value) => filters.set({ condition: value })} />
                  <FacetSelect label="شهر" value={params.city} options={data.facets.cities}
                    onChange={(value) => filters.set({ city: value })} />
                  <FacetSelect label="کارکرد از" value={params.mileage_min} format={mileageLabel}
                    options={[...data.facets.mileage].sort((a, b) => Number(a.value) - Number(b.value))}
                    onChange={(value) => filters.set({ mileage_min: value })} />
                  <FacetSelect label="کارکرد تا" value={params.mileage_max} format={mileageLabel}
                    options={[...data.facets.mileage].sort((a, b) => Number(a.value) - Number(b.value))}
                    onChange={(value) => filters.set({ mileage_max: value })} />
                </div>
                <p className="stat-sub mt-3">
                  بازه‌ها فقط از قیمت‌های درخواستی نقدی ساخته شده‌اند. بازه فقط وقتی نشان
                  داده می‌شود که دست‌کم {num(data.evidence.minimum_for_range)} آگهی وجود داشته باشد.
                </p>
              </Card>

              {/* "Your selection" beside "the whole market" only once there is a
                  selection. Before that the two cards printed identical numbers
                  side by side, which read as a bug. */}
              <div className={refined ? "grid cols-2" : "stack"}>
                <Card title="بازار سراسری">
                  <PriceSummary title={`${data.model.brand} ${data.model.name}`}
                                summary={data.national} emphasis={!refined} />
                </Card>
                {refined && (
                  <Card title="انتخاب شما">
                    <PriceSummary title="آگهی‌های دقیق‌تر" summary={data.selected} emphasis />
                  </Card>
                )}
              </div>

              <Card
                title={`آگهی‌های پشتیبان (${num(data.ads.length)} از ${num(data.selected.count)})`}
                action={
                  <Link to={toListings} className="btn">
                    <ArrowUpDown className="size-4" aria-hidden />
                    همه با مرتب‌سازی
                    <ArrowLeft className="size-4" aria-hidden />
                  </Link>
                }
              >
                {data.ads.length ? (
                  <div className="card-grid">
                    {data.ads.map((ad) => (
                      <div key={ad.code} className="listing-card stretch-host">
                        <Thumb src={ad.image_url}>
                          <ListingActions code={ad.code} compact />
                        </Thumb>
                        <div className="listing-meta">
                          <strong>
                            <Link to={ad.listing_url} className="stretch-link"><Fa>{ad.title}</Fa></Link>
                          </strong>
                          <span className="deal-price">{toman(ad.current_price)}</span>
                          <div className="row card-facts">
                            <span>{ad.year_jalali ?? "—"}</span>
                            <span aria-hidden>·</span>
                            <span>{km(ad.mileage)}</span>
                            <span aria-hidden>·</span>
                            <Fa>{ad.city__name_fa || "—"}</Fa>
                          </div>
                          <div className="row card-facts"><Fa>{ad.body_status || "وضعیت نامشخص"}</Fa></div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : <p>آگهی‌ای برای این انتخاب پیدا نشد.</p>}
                {data.selected.count > data.ads.length && (
                  <p className="stat-sub mt-3">
                    این‌ها {num(data.ads.length)} آگهی تازه‌ترند.{" "}
                    <Link to={toListings}>هر {num(data.selected.count)} آگهی را در صفحه‌ی آگهی‌ها ببینید</Link>
                    {" "}— آنجا می‌توانید بر اساس قیمت، کارکرد یا سال مرتب کنید.
                  </p>
                )}
              </Card>

              <Card title="حد شواهد">
                <p>{data.evidence.feed_coverage_complete
                  ? "پوشش خوراک در پنجرهٔ اخیر کامل بوده است."
                  : "پوشش خوراک در پنجرهٔ اخیر کامل نبوده است؛ غیبت یک آگهی به معنی فروش نیست."}</p>
                {data.evidence.latest_selected_sighting && (
                  <p>آخرین مشاهدهٔ آگهی‌های انتخاب‌شده: {new Date(
                    data.evidence.latest_selected_sighting,
                  ).toLocaleString("fa-IR")}</p>
                )}
                <p>قیمت خرید واقعی فعلاً در دسترس نیست؛ دادهٔ تأییدشده از معامله‌ها نداریم.</p>
              </Card>
            </>
          );
        }}
      </Async>}
    </div>
  );
}

type SortKey = "count" | "name";

/**
 * Browse when you do not know the name.
 *
 * Brands are ranked by the listings their models carry, summed client-side
 * from the same ranked model list the picker uses — so the number on a brand
 * chip and the numbers on its model tiles always add up. Choosing a brand
 * refetches that brand's full model list, which is not capped at the top 60.
 */
function ModelBrowser({ onPick }: { onPick: (model: ModelRow) => void }) {
  const [brand, setBrand] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>("count");

  const top = useQuery({
    queryKey: ["model-search", "", undefined],
    staleTime: 10 * 60_000,
    queryFn: ({ signal }) => api.get<ModelRow[]>("/api/models/", signal),
  });
  const brandModels = useQuery({
    queryKey: ["model-search", "", brand],
    enabled: Boolean(brand),
    staleTime: 10 * 60_000,
    queryFn: ({ signal }) => api.get<ModelRow[]>(`/api/models/${qs({ brand })}`, signal),
  });

  const brands = useMemo(() => {
    const totals = new Map<string, { slug: string; name: string; count: number }>();
    for (const m of top.data ?? []) {
      const row = totals.get(m.brand_slug) ?? { slug: m.brand_slug, name: m.brand_name, count: 0 };
      row.count += m.ad_count;
      totals.set(m.brand_slug, row);
    }
    return [...totals.values()].sort((a, b) => b.count - a.count);
  }, [top.data]);

  const source = brand ? brandModels : top;
  const models = useMemo(() => {
    const rows = [...(source.data ?? [])].filter((m) => m.ad_count > 0);
    return sort === "count"
      ? rows.sort((a, b) => b.ad_count - a.ad_count)
      : rows.sort((a, b) =>
          `${a.brand_name} ${a.name_fa}`.localeCompare(`${b.brand_name} ${b.name_fa}`, "fa"));
  }, [source.data, sort]);

  return (
    <Card
      title={brand ? "مدل‌های این برند" : "پرآگهی‌ترین مدل‌ها"}
      action={
        <div className="segmented" role="group" aria-label="ترتیب">
          <button type="button" className={sort === "count" ? "on" : ""}
                  aria-pressed={sort === "count"} onClick={() => setSort("count")}>
            بیشترین آگهی
          </button>
          <button type="button" className={sort === "name" ? "on" : ""}
                  aria-pressed={sort === "name"} onClick={() => setSort("name")}>
            الفبایی
          </button>
        </div>
      }
    >
      <div className="brand-strip" role="group" aria-label="برند">
        <button type="button" className={`preset${brand == null ? " on" : ""}`}
                aria-pressed={brand == null} onClick={() => setBrand(null)}>
          همه برندها
        </button>
        {brands.map((b) => (
          <button key={b.slug} type="button"
                  className={`preset${brand === b.slug ? " on" : ""}`}
                  aria-pressed={brand === b.slug}
                  onClick={() => setBrand(brand === b.slug ? null : b.slug)}>
            <Fa>{b.name}</Fa>
            <span className="font-mono text-[11px] opacity-70">{num(b.count)}</span>
          </button>
        ))}
      </div>

      <Async query={source} shape="cards">
        {() => (
          <div className="model-tiles">
            {models.map((m) => (
              <button key={m.id} type="button" className="model-tile" onClick={() => onPick(m)}>
                <span className="model-tile-brand"><Fa>{m.brand_name}</Fa></span>
                <span className="model-tile-name"><Fa>{m.name_fa}</Fa></span>
                <span className="model-tile-count"><b>{num(m.ad_count)}</b> آگهی</span>
              </button>
            ))}
          </div>
        )}
      </Async>
    </Card>
  );
}
