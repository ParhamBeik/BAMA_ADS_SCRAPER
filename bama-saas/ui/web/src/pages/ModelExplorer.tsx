/** Model-first, evidence-led exploration of Bama cash asking prices. */
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { qs, useFilters } from "../filters";
import { num, toman } from "../format";
import { Async, Card, Fa, ListingActions, Thumb } from "../ui";
import { ModelCombobox } from "../components/ModelCombobox";

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

function PriceSummary({ title, summary }: { title: string; summary: Summary }) {
  return (
    <div className="stack">
      <h2>{title}</h2>
      <p>{num(summary.count)} آگهی با قیمت نقدی</p>
      {summary.asking_range ? (
        <p className="text-xl font-bold tabular-nums">
          {toman(summary.asking_range.p25)} تا {toman(summary.asking_range.p75)} تومان
          <span className="block text-sm font-normal">
            میانه {toman(summary.asking_range.median)} · دهک ۱۰ تا ۹۰:
            {" "}{toman(summary.asking_range.p10)} تا {toman(summary.asking_range.p90)}
          </span>
        </p>
      ) : (
        <p>نمونه برای بازهٔ قابل اتکا کافی نیست. این آگهی‌ها همچنان شواهد واقعی‌اند.</p>
      )}
    </div>
  );
}

function FacetSelect({ label, value, options, onChange }: {
  label: string; value?: string; options: Facet[]; onChange: (value: string | null) => void;
}) {
  return (
    <label className="stack gap-1 text-sm">
      <span>{label}</span>
      <select className="input" value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
        <option value="">همه</option>
        {options.filter((option) => option.value != null && option.value !== "").map((option) => (
          <option key={String(option.value)} value={String(option.value)}>
            {String(option.label ?? option.value)} ({num(option.count)})
          </option>
        ))}
      </select>
    </label>
  );
}

export function ModelExplorer() {
  const filters = useFilters();
  const model = filters.get("model");
  const params = {
    variant: filters.get("variant"), year: filters.get("year"),
    city: filters.get("city"), condition: filters.get("condition"),
    mileage_min: filters.get("mileage_min"), mileage_max: filters.get("mileage_max"),
  };
  const exploration = useQuery({
    queryKey: ["model-exploration", model, params],
    enabled: Boolean(model),
    queryFn: ({ signal }) => api.get<Exploration>(
      `/api/models/${model}/explore/${qs(params)}`, signal,
    ),
  });

  return (
    <div className="stack" dir="rtl">
      <Card title="کاوش قیمت یک مدل">
        <p>مدل را انتخاب کنید؛ تفاوت تیپ، سال، کارکرد، وضعیت بدنه و شهر را با آگهی‌های پشتیبان ببینید.</p>
        <div className="max-w-md">
          <ModelCombobox value={model} onSelect={(next) => filters.set({
            model: next?.id, variant: null, year: null, city: null, condition: null,
            mileage_min: null, mileage_max: null,
          })} />
        </div>
      </Card>
      {model && <Async query={exploration}>
        {(data) => (
          <>
            <div className="grid cols-2">
              <Card title="بازار سراسری">
                <PriceSummary title={`${data.model.brand} ${data.model.name}`} summary={data.national} />
              </Card>
              <Card title="انتخاب شما">
                <PriceSummary title="آگهی‌های دقیق‌تر" summary={data.selected} />
              </Card>
            </div>
            <Card title="دقیق‌تر کنید">
              <div className="grid cols-4">
                <FacetSelect label="تیپ" value={params.variant} options={data.facets.variants}
                  onChange={(value) => filters.set({ variant: value })} />
                <FacetSelect label="سال ساخت" value={params.year} options={data.facets.years}
                  onChange={(value) => filters.set({ year: value })} />
                <FacetSelect label="وضعیت بدنه" value={params.condition} options={data.facets.conditions}
                  onChange={(value) => filters.set({ condition: value })} />
                <FacetSelect label="شهر" value={params.city} options={data.facets.cities}
                  onChange={(value) => filters.set({ city: value })} />
                <FacetSelect label="کارکرد از" value={params.mileage_min} options={data.facets.mileage}
                  onChange={(value) => filters.set({ mileage_min: value === "unknown" ? null : value })} />
                <FacetSelect label="کارکرد تا" value={params.mileage_max} options={data.facets.mileage}
                  onChange={(value) => filters.set({ mileage_max: value === "unknown" ? null : value })} />
              </div>
              <p className="stat-sub">
                بازه‌ها فقط از قیمت‌های درخواستی نقدی ساخته شده‌اند. گروه‌های کم‌شمار حذف نمی‌شوند؛
                بازه فقط وقتی دست‌کم {num(data.evidence.minimum_for_range)} آگهی وجود داشته باشد نشان داده می‌شود.
              </p>
            </Card>
            <Card title="آگهی‌های پشتیبان">
              {data.ads.length ? (
                <div className="card-grid">
                  {data.ads.map((ad) => (
                    <div key={ad.code} className="listing-card">
                      <Thumb src={ad.image_url} />
                      <Link to={ad.listing_url}><Fa>{ad.title}</Fa></Link>
                      <p>{toman(ad.current_price)} تومان · {ad.year_jalali ?? "—"} ·
                        {" "}{ad.mileage == null ? "کارکرد نامشخص" : `${num(ad.mileage)} کیلومتر`}</p>
                      <p>{ad.body_status || "وضعیت نامشخص"} · {ad.city__name_fa || "شهر نامشخص"}</p>
                      <ListingActions code={ad.code} />
                    </div>
                  ))}
                </div>
              ) : <p>آگهی‌ای برای این انتخاب پیدا نشد.</p>}
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
        )}
      </Async>}
    </div>
  );
}
