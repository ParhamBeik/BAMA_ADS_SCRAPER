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
import { useMemo, useState, type ReactNode } from "react";
import { useBrands } from "../catalogue";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft, X } from "lucide-react";
import { api } from "../api";
import { qs, useFilters } from "../filters";
import { conditionLabel, km, num, toman, tomanRange } from "../format";
import { Async, Card, Fa, HowWeKnow, ListingActions, RangeNotes, Thumb } from "../ui";
import type { Distribution } from "../ui";
import { isZeroKm, MILEAGE_PRESETS } from "../components/FilterPanel";
import { ModelCombobox, type ModelRow } from "../components/ModelCombobox";
import { FollowButton } from "../components/FollowButton";
import {
  DEFAULT_DAYS, RetentionPanel, SurvivalPanel, TrendPanel, trendRequest,
} from "../components/ModelInsights";
import { Budget } from "./Budget";
import { Button } from "../components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";

type Range = Pick<Distribution, "p10" | "p25" | "median" | "p75" | "p90" | "width_pct"
  | "wide" | "clusters" | "old_price_pct" | "old_price_days">;
type Summary = { count: number; thin: boolean; asking_range: Range | null;
  observed_min: number | null; observed_max: number | null;
  /** True once trim, year and condition are all pinned: the range is peers. */
  strict?: boolean };
type Facet = Summary & { value: string | number | null; label: string | number | null };
type Ad = { code: string; title: string; current_price: number; year_jalali: number | null;
  mileage: number | null; body_status: string; city__name_fa: string | null;
  image_url: string; listing_url: string };
type Exploration = {
  model: { id: number; name: string; brand: string };
  basis: string;
  national: Summary;
  selected: Summary;
  facets: { variants: Facet[]; years: Facet[]; conditions: Facet[]; cities: Facet[] };
  ads: Ad[];
  evidence: { feed_coverage_complete: boolean; minimum_for_range: number;
    latest_selected_sighting: string | null };
  purchase_price: null;
  purchase_price_reason: string;
};

const REFINE_KEYS = ["variant", "year", "city", "condition", "mileage_min", "mileage_max"] as const;

function PriceSummary({ title, summary, emphasis = false, children }: {
  title: string; summary: Summary; emphasis?: boolean; children?: ReactNode;
}) {
  const r = summary.asking_range;
  return (
    <div className="price-answer">
      <div className="row between">
        <h2 className={emphasis ? "text-lg" : "text-base"}><Fa>{title}</Fa></h2>
        <span className="muted">{num(summary.count)} آگهی</span>
      </div>
      {r ? (
        <>
          <p className={emphasis ? "price-range big" : "price-range"}>
            <bdi>{tomanRange(r.p25, r.p75)}</bdi> تومان
          </p>
          <p className="stat-sub">
            میانه <bdi>{toman(r.median)}</bdi>{" · "}80% آگهی‌ها <bdi>{tomanRange(r.p10, r.p90)}</bdi>
          </p>
          {summary.strict === false && <span className="badge">بازه‌ی ترکیبی</span>}
          <RangeNotes distribution={{ ...r, count: summary.count, thin: summary.thin } as Distribution}>
            {summary.strict === false && (
              <p>
                این بازه خودروهای ناهمسان را کنار هم گذاشته است. برای بازه‌ی دقیق، تیپ، سال
                ساخت و وضعیت بدنه را انتخاب کنید.
              </p>
            )}
            {children}
          </RangeNotes>
        </>
      ) : (
        <>
          <span className="badge warn">نمونه کم</span>
          <HowWeKnow>
            <p>نمونه برای بازه‌ی قابل اتکا کافی نیست. این آگهی‌ها همچنان شواهد واقعی‌اند.</p>
            {children}
          </HowWeKnow>
        </>
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

const MAX_COMPARE = 4;

/**
 * Two to four cars side by side, under the same year, condition and mileage.
 *
 * Trim is left out on purpose: trims belong to one model, and "same trim"
 * across a 206 and a Quick means nothing.
 */
function CompareCard({ ids, params, onAdd, onRemove }: {
  ids: string[];
  params: Record<string, string | undefined>;
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["model-exploration", id, params],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        api.get<Exploration>(`/api/models/${id}/explore/${qs(params)}`, signal),
    })),
  });
  const refined = Object.values(params).some(Boolean);
  return (
    <Card title="مقایسه">
      <ul className="compare-list">
        {ids.map((id, i) => {
          const q = results[i];
          const data = q.data;
          const summary = data && (refined && data.selected.asking_range ? data.selected : data.national);
          const r = summary?.asking_range;
          return (
            <li key={id} className="compare-row">
              <span className="compare-name">
                <Fa>{data ? `${data.model.brand} ${data.model.name}` : "…"}</Fa>
              </span>
              <span className="compare-range">
                {q.isLoading ? "…" : r ? <><bdi>{tomanRange(r.p25, r.p75)}</bdi> تومان</> : "نمونه کم"}
              </span>
              <span className="stat-sub">
                {r && <>میانه <bdi>{toman(r.median)}</bdi> · </>}
                {summary && <>{num(summary.count)} آگهی</>}
              </span>
              {ids.length > 1 && (
                <button type="button" className="compare-remove" aria-label="حذف از مقایسه"
                        onClick={() => onRemove(id)}>
                  <X className="size-4" />
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {ids.length < MAX_COMPARE && (
        <div className="mt-3">
          <ModelCombobox placeholder="افزودن خودرو برای مقایسه…"
                         onSelect={(m) => m && !ids.includes(String(m.id)) && onAdd(String(m.id))} />
        </div>
      )}
    </Card>
  );
}

/** Mileage as one select, like the other refinements: seven preset buttons
 *  and a from/to pair pushed the price below the first screen on a phone. */
function MileageSelect() {
  const filters = useFilters();
  const min = filters.get("mileage_min") ?? "";
  const max = filters.get("mileage_max") ?? "";
  const current = MILEAGE_PRESETS.find(([, lo, hi]) => String(lo) === min && String(hi ?? "") === max);
  return (
    <label className="grid gap-1.5">
      <span className="text-muted-foreground text-xs font-semibold">کارکرد</span>
      <Select
        value={current ? current[0] : ANY}
        onValueChange={(next) => {
          const preset = MILEAGE_PRESETS.find(([label]) => label === next);
          filters.set(preset
            ? { mileage_min: preset[1], mileage_max: preset[2],
                ...(preset[2] === 0 ? { condition: null } : {}) }
            : { mileage_min: null, mileage_max: null });
        }}
      >
        <SelectTrigger className="w-full" aria-label="کارکرد"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem value={ANY}>همه</SelectItem>
          {MILEAGE_PRESETS.map(([label]) => (
            <SelectItem key={label} value={label}>{label}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}

export function ModelExplorer() {
  const filters = useFilters();
  const model = filters.get("model");
  const mode = filters.get("mode");
  const compare = (filters.get("compare") ?? "").split(",").filter((x) => x && x !== model);
  const [comparing, setComparing] = useState(false);
  const days = filters.getInt("days") ?? DEFAULT_DAYS;
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
    mileage_min: null, mileage_max: null, mode: null, compare: null,
  });

  return (
    <div className="stack" dir="rtl">
      <Card title="قیمت یک خودرو">
        <div className="flex flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1 basis-full sm:max-w-md sm:basis-auto">
            <ModelCombobox value={model} onSelect={pick} />
          </div>
          {model && (
            <>
              <Button variant="ghost" size="sm" onClick={() => pick(null)}>
                <X className="size-4" /> مدل دیگر
              </Button>
              <FollowButton scope={{ model, variant: params.variant, year: params.year }} />
              {!comparing && compare.length === 0 && (
                <Button variant="outline" size="sm" onClick={() => setComparing(true)}>
                  مقایسه
                </Button>
              )}
            </>
          )}
        </div>
      </Card>

      {model && (comparing || compare.length > 0) && (
        <CompareCard
          ids={[model, ...compare]}
          params={{ year: params.year, condition: params.condition,
                    mileage_min: params.mileage_min, mileage_max: params.mileage_max }}
          onAdd={(id) => filters.set({ compare: [...compare, id].join(",") })}
          onRemove={(id) => {
            if (id === model) {
              // The first of the others becomes the page's car.
              const [next, ...rest] = compare;
              filters.set({ model: next ?? null, compare: rest.join(",") || null, variant: null });
            } else {
              filters.set({ compare: compare.filter((x) => x !== id).join(",") || null });
            }
          }}
        />
      )}

      {!model && (
        <div className="segmented" role="group" aria-label="چطور پیدا کنید">
          <button type="button" className={mode !== "budget" ? "on" : ""}
                  aria-pressed={mode !== "budget"} onClick={() => filters.set({ mode: null })}>
            یک مدل
          </button>
          <button type="button" className={mode === "budget" ? "on" : ""}
                  aria-pressed={mode === "budget"} onClick={() => filters.set({ mode: "budget" })}>
            با بودجه‌ی من
          </button>
        </div>
      )}
      {!model && (mode === "budget" ? <Budget /> : <ModelBrowser onPick={pick} />)}

      {model && <Async query={exploration}>
        {(data) => {
          // Everything in the model page's selection that the listings page
          // can also filter on; both speak the same four condition bands.
          const toListings = `/cars${qs({
            model: data.model.id,
            variant: params.variant,
            condition: params.condition,
            year_min: params.year, year_max: params.year,
            city: params.city,
            mileage_min: params.mileage_min, mileage_max: params.mileage_max,
          })}`;
          const evidence = (
            <>
              <p>
                بازه‌ها فقط از قیمت‌های درخواستی نقدی ساخته می‌شوند و فقط وقتی نشان داده
                می‌شوند که دست‌کم {num(data.evidence.minimum_for_range)} آگهی وجود داشته باشد.
              </p>
              <p>{data.evidence.feed_coverage_complete
                ? "پوشش خوراک در پنجره‌ی اخیر کامل بوده است."
                : "پوشش خوراک در پنجره‌ی اخیر کامل نبوده است؛ غیبت یک آگهی به معنی فروش نیست."}</p>
              {data.evidence.latest_selected_sighting && (
                <p>آخرین مشاهده‌ی آگهی‌های انتخاب‌شده: {new Date(
                  data.evidence.latest_selected_sighting,
                ).toLocaleString("fa-IR-u-nu-latn")}</p>
              )}
              <p>قیمت خرید واقعی در دسترس نیست؛ داده‌ی تأییدشده از معامله‌ها نداریم.</p>
            </>
          );
          const label = (facets: Facet[], value?: string) =>
            facets.find((f) => String(f.value) === value)?.label;
          const useSelected = refined && data.selected.asking_range != null;
          const thinSelection = refined && !useSelected;
          const selectionTitle = [
            `${data.model.brand} ${data.model.name}`,
            label(data.facets.variants, params.variant),
            params.year,
            params.condition && conditionLabel(params.condition),
            label(data.facets.cities, params.city),
          ].filter(Boolean).join(" · ");
          return (
            <>
              <Card
                title="دقیق‌تر کنید"
                action={refined
                  ? <Button variant="ghost" size="sm" onClick={clearRefine}>پاک کردن</Button>
                  : undefined}
              >
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
                  <FacetSelect label="تیپ" value={params.variant} options={data.facets.variants}
                    onChange={(value) => filters.set({ variant: value })} />
                  <FacetSelect label="سال ساخت" value={params.year}
                    options={[...data.facets.years].sort((a, b) => Number(b.value) - Number(a.value))}
                    onChange={(value) => filters.set({ year: value })} />
                  {!isZeroKm(params.mileage_max) && (
                    <FacetSelect label="وضعیت بدنه" value={params.condition} options={data.facets.conditions}
                      format={(o) => conditionLabel(o.value)}
                      onChange={(value) => filters.set({ condition: value })} />
                  )}
                  <FacetSelect label="شهر" value={params.city} options={data.facets.cities}
                    onChange={(value) => filters.set({ city: value })} />
                  <MileageSelect />
                </div>
              </Card>

              {/* The answer first: the selection's range when it has one,
                  otherwise the whole model's — a refinement too thin for a
                  range must not push the one range there is below the
                  filters. The whole-model card below appears only when the
                  answer above is the selection's. */}
              <Card title={useSelected ? "قیمت انتخاب شما" : "قیمت در بازار"}>
                {thinSelection && (
                  <span className="badge warn">
                    انتخاب شما: {num(data.selected.count)} آگهی، کم برای بازه
                  </span>
                )}
                <PriceSummary
                  title={useSelected ? selectionTitle : `${data.model.brand} ${data.model.name}`}
                  summary={useSelected ? data.selected : data.national} emphasis>
                  {evidence}
                </PriceSummary>
              </Card>

              {/* The cars behind the range, straight under it. */}
              <Card
                title={`آگهی‌ها (${num(data.selected.count)})`}
                action={
                  <Link to={toListings} className="btn">
                    همه
                    <ArrowLeft className="size-4" aria-hidden />
                  </Link>
                }
              >
                {data.ads.length ? (
                  <div className="card-grid card-strip">
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
              </Card>


              {useSelected && (
                <Card title="کل این مدل">
                  <PriceSummary title={`${data.model.brand} ${data.model.name}`}
                                summary={data.national} />
                </Card>
              )}

              <TrendPanel url={trendRequest({
                model: String(data.model.id), variant: params.variant, year: params.year, days,
              })!} />
              <RetentionPanel model={String(data.model.id)} variant={params.variant} />
              <SurvivalPanel model={String(data.model.id)} variant={params.variant}
                             year={params.year} />


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

  // The catalogue's own per-brand totals. Summing the top-60 model list
  // undercounted every brand with a long tail: Toyota read 636 here and 1,680
  // on the home page.
  const { list: brandList } = useBrands();
  const brands = brandList
    .filter((b) => (b.ad_count ?? 0) > 0)
    .map((b) => ({ slug: b.slug, name: b.name_fa, count: b.ad_count ?? 0 }));

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
