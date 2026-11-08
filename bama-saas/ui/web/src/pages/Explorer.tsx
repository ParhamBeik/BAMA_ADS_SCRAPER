/**
 * Buyer Explorer — find a car, then find out what it is actually worth.
 *
 * Three product decisions are visible here.
 *
 * A listing flagged as an unbelievable price is *shown*, with its warning, not
 * hidden. A suspiciously cheap car is the most valuable thing this product can
 * surface; suppressing it to keep the statistics tidy would defeat the purpose.
 * The same applies to a car that is cheap because it is damaged, or because its
 * price is a down payment: both are badged, neither is removed.
 *
 * The fair-price answer leads with *where this car sits among its peers* and
 * keeps the components underneath. "1.02B, because the peer median is 1.10B and
 * this car has 60,000km more than its peers" is checkable, and it stays — but
 * the question people arrive with is "is this cheap", and a bar answers that
 * without arithmetic. It lives in a slide-over rather than a permanent column:
 * that column used to hold an empty prompt half the time, at the cost of half
 * the width of the listings it was about.
 *
 * The year column reads `year_jalali`, never `year`. The raw column mixes 1399
 * and 2025 in one field (see apps/core/filters.py, which range-filters on the
 * Jalali one for exactly this reason) and rendering it put both calendars in one
 * column of the same table.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, ArrowUpDown, Gauge, Wallet } from "lucide-react";
import { api, ApiError } from "../api";
import type { Paginated } from "../api";
import { FilterPanel } from "@/components/FilterPanel";
import { listedFrom, qs, useFilters } from "../filters";
import { Button } from "../components/ui/button";
import { Async, BamaLink, FLAG_LABEL, Fa, ListingActions, Pager, Table, Thumb } from "../ui";
import { km, modelYear, num, toman, trimNote } from "../format";
import { ViewToggle, useListView } from "../components/ViewToggle";
import { conditionNote, conditionTag } from "../components/DealCard";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "../components/ui/select";

/** Sort orders the API supports, in the order a buyer reaches for them. */
const ORDERINGS = [
  { value: "-publish_at", label: "تازه‌ترین" },
  // Bama's date jumps on every bump; this is when the ad first appeared.
  { value: "-first_seen_at", label: "تازه‌ترین آگهی ثبت‌شده" },
  { value: "current_price", label: "ارزان‌ترین" },
  { value: "-current_price", label: "گران‌ترین" },
  { value: "mileage", label: "کمترین کارکرد" },
  { value: "-mileage", label: "بیشترین کارکرد" },
  { value: "-year_jalali", label: "جدیدترین سال ساخت" },
];

interface AdRow {
  code: string;
  title: string;
  brand_name: string;
  model_name: string;
  model_id: number | null;
  year: number | null;
  year_jalali?: number | null;
  variant_name?: string;
  year_calendar?: string;
  mileage: number | null;
  current_price: number | null;
  city_name: string;
  body_status?: string;
  cohort_flags: string[];
  image_url?: string;
  bama_url?: string;
  price_basis_unclear?: boolean;
  condition_flagged?: boolean;
  condition_band?: string | null;
  mileage_implausible?: boolean;
}

// Must match REST_FRAMEWORK.PAGE_SIZE in config/settings.py — the API gives
// back a count and a next/previous link, never a page size, so the pager has to
// know it independently to compute the last page.
const PAGE_SIZE = 50;

/** One-word badges for `Ad.cohort_flags`; the full sentence is the tooltip. */
const FLAG_SHORT: Record<string, string> = {
  price_outlier_high: "گران‌تر از مشابه‌ها",
  price_outlier_low: "ارزان‌تر از مشابه‌ها",
  suspect_installment_price: "پیش‌پرداخت؟",
  suspect_misfiled: "تیپ یا سال مشکوک",
  price_implausible: "قیمت نامعتبر",
};

/**
 * Every reason this row's price may not mean what it looks like.
 *
 * Short labels with the full sentence on `title`: these sit on top of a 4:3
 * thumbnail, and a wrapped two-line badge covered the car it was warning about.
 * The unabbreviated explanation lives on the listing page.
 */
function AdWarnings({ ad }: { ad: AdRow }) {
  return (
    <>
      {ad.price_basis_unclear && (
        <span
          className="badge warn"
          title="این عدد به احتمال زیاد پیش‌پرداخت یا قسط است، نه قیمت کامل خودرو"
        >
          <Wallet size={11} /> پیش‌پرداخت؟
        </span>
      )}
      {ad.condition_flagged && (
        <span className="badge warn" title={conditionNote(ad)}>
          <AlertTriangle size={11} /> {conditionTag(ad)}
        </span>
      )}
      {/* An odometer nobody can believe. `verify` flags these and `pricing`
          declines to adjust on them, but every screen still printed the number
          as a fact — a 1404 Koleos at 9,000,000 km — and "most mileage first"
          ranked the typos to the top of the list. */}
      {ad.mileage_implausible && (
        <span
          className="badge warn"
          title="کارکرد ثبت‌شده باورپذیر نیست — احتمالاً اشتباه تایپی فروشنده است و در محاسبه قیمت نادیده گرفته شده"
        >
          <Gauge size={11} /> کارکرد مشکوک
        </span>
      )}
      {(ad.cohort_flags ?? []).map((flag) => (
        <span key={flag} className="badge warn" title={FLAG_LABEL[flag] ?? flag}>
          <AlertTriangle size={11} /> {FLAG_SHORT[flag] ?? flag}
        </span>
      ))}
    </>
  );
}

export function Explorer() {
  const filters = useFilters();
  const navigate = useNavigate();
  const page = filters.getInt("page") ?? 1;
  const view = useListView();
  const ordering = filters.get("ordering") ?? "-publish_at";

  const adParams = {
    page, ordering,
    brand: filters.get("brand"),
    model: filters.get("model"),
    variant: filters.get("variant"),
    // Set by the model page's "see all" link; the API filters on it already.
    city: filters.get("city"),
    q: filters.get("q"),
    price_min: filters.get("price_min"),
    price_max: filters.get("price_max"),
    year_min: filters.get("year_min"),
    year_max: filters.get("year_max"),
    listed_from: listedFrom(filters.getInt("listed_days")),
    mileage_min: filters.get("mileage_min"),
    mileage_max: filters.get("mileage_max"),
    color: filters.get("color"),
    transmission: filters.get("transmission"),
    fuel: filters.get("fuel"),
    body_type: filters.get("body_type"),
    condition: filters.get("condition"),
    seller_type: filters.get("seller_type"),
  };

  const ads = useQuery({
    queryKey: ["ads", adParams],
    queryFn: ({ signal }) => api.get<Paginated<AdRow>>(`/api/ads/${qs(adParams)}`, signal),
  });

  return (
    <div className="stack">
      <FilterPanel />

      <div>
        <div className="grid-header">
          <p className="stat-sub list-count" role="status">
            {ads.data?.count != null ? `${num(ads.data.count)} آگهی` : "\u00a0"}
          </p>
          <div className="row">
            <Select
              value={ordering}
              onValueChange={(next) => filters.set({ ordering: next, page: 1 })}
            >
              <SelectTrigger className="w-44" aria-label="ترتیب نمایش">
                <ArrowUpDown className="size-4 opacity-60" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ORDERINGS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <ViewToggle />
          </div>
        </div>

        {/* A page past the end answers 404, not an empty list. */}
        {ads.error instanceof ApiError && ads.error.status === 404 && page > 1 ? (
          <div className="state">
            <strong>این صفحه دیگر وجود ندارد.</strong>
            <Button variant="outline" size="sm" onClick={() => filters.set({ page: null })}>
              برگشت به صفحه‌ی اول
            </Button>
          </div>
        ) : <Async
          query={ads}
          empty="آگهی‌ای با این فیلترها پیدا نشد."
          shape={view === "cards" ? "cards" : "table"}
        >
          {(data) => !data.results.length ? (
            // A page of nothing used to render a bare pager reading "0".
            <div className="state">
              <strong>آگهی‌ای با این فیلترها پیدا نشد.</strong>
              <p className="empty-hint">
                {page > 1
                  ? "ممکن است این صفحه دیگر وجود نداشته باشد."
                  : "یکی از فیلترها را بردارید یا بازه را بازتر کنید."}
              </p>
              {page > 1 && (
                <Button variant="outline" size="sm" onClick={() => filters.set({ page: null })}>
                  برگشت به صفحه‌ی اول
                </Button>
              )}
            </div>
          ) : (
            <>
              {view === "cards" ? (
                <div className="card-grid">
                  {data.results.map((ad) => (
                    <div
                      key={ad.code}
                      className="listing-card stretch-host"
                    >
                      <Thumb src={ad.image_url}>
                        <span className="card-badges">
                          <AdWarnings ad={ad} />
                        </span>
                        <ListingActions code={ad.code} compact name={ad.title || ad.model_name} />
                      </Thumb>
                      <div className="listing-meta">
                        <strong>
                          <Link
                            to={`/listing/${ad.code}`}
                            className="stretch-link"
                          >
                            <Fa>{ad.title}</Fa>
                          </Link>
                        </strong>
                        {trimNote(ad.title, ad.variant_name) && (
                          <span className="stat-sub"><Fa>{trimNote(ad.title, ad.variant_name)}</Fa></span>
                        )}
                        <span className="deal-price">{toman(ad.current_price)}</span>
                        <div className="row card-facts">
                          <span>{modelYear(ad.year_jalali, ad.year_calendar)}</span>
                          <span aria-hidden>·</span>
                          <span>{km(ad.mileage)}</span>
                          <span aria-hidden>·</span>
                          <Fa>{ad.city_name || "—"}</Fa>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="card cars-table"><Table head={["خودرو", "سال", "کارکرد", "قیمت", ""]}>
                  {data.results.map((ad) => (
                    <tr
                      key={ad.code}
                      onClick={() => navigate(`/listing/${ad.code}`)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <Link
                          to={`/listing/${ad.code}`}
                          className="row-open"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <Fa>{ad.title || ad.model_name}</Fa>
                        </Link>
                        <div className="cars-table-facts stat-sub">
                          {modelYear(ad.year_jalali, ad.year_calendar)} · {km(ad.mileage)}
                        </div>
                        <div className="row" style={{ marginTop: 4 }}>
                          <AdWarnings ad={ad} />
                        </div>
                      </td>
                      <td className="num">{modelYear(ad.year_jalali, ad.year_calendar)}</td>
                      <td className="num">
                        {ad.mileage == null ? "—" : (
                          <span className={ad.mileage_implausible ? "warn" : undefined}>
                            {km(ad.mileage)}
                            {ad.mileage_implausible && "؟"}
                          </span>
                        )}
                      </td>
                      <td className="num">{toman(ad.current_price)}</td>
                      <td className="num">
                        <BamaLink href={ad.bama_url} className="ghost">باما</BamaLink>
                      </td>
                    </tr>
                  ))}
                </Table></div>
              )}
              <div style={{ marginTop: 14 }}>
                <Pager
                  page={page}
                  lastPage={Math.max(1, Math.ceil(data.count / PAGE_SIZE))}
                  total={data.count}
                  label="آگهی فعال قیمت‌دار"
                  onChange={(next) => filters.set({ page: next })}
                />
              </div>
            </>
          )}
        </Async>}
      </div>
    </div>
  );
}
