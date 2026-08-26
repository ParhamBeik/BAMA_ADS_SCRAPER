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
import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Wallet } from "lucide-react";
import { api } from "../api";
import type { Envelope, Paginated } from "../api";
import { FilterPanel } from "../FilterPanel";
import { qs, useFilters } from "../filters";
import {
  Async, BamaLink, Card, FLAG_LABEL, Fa, ListingActions, Pager,
  PriceBar, Provenance, Sheet, Table, Thumb, km, toman,
} from "../ui";
import type { Distribution } from "../ui";

interface AdRow {
  code: string;
  title: string;
  brand_name: string;
  model_name: string;
  model_id: number | null;
  year: number | null;
  year_jalali?: number | null;
  mileage: number | null;
  current_price: number | null;
  city_name: string;
  body_status?: string;
  cohort_flags: string[];
  image_url?: string;
  bama_url?: string;
  price_basis_unclear?: boolean;
  condition_flagged?: boolean;
}

// Must match REST_FRAMEWORK.PAGE_SIZE in config/settings.py — the API gives
// back a count and a next/previous link, never a page size, so the pager has to
// know it independently to compute the last page.
const PAGE_SIZE = 50;

interface FairPrice extends Envelope {
  code: string;
  asking: number | null;
  fair_value: number | null;
  gap_pct: number | null;
  peer_count: number;
  dispersion: number | null;
  confidence: string;
  distribution?: Distribution;
  components: { name: string; amount: number; detail: string }[];
}

const COMPONENT_LABEL: Record<string, string> = {
  cohort_median: "میانه قیمت آگهی‌های مشابه",
  mileage: "تعدیل بابت کارکرد",
};

/**
 * Every reason this row's price may not mean what it looks like.
 *
 * Short labels with the full sentence on `title`: these sit on top of a 4:3
 * thumbnail, and a wrapped two-line badge covered the car it was warning about.
 * The unabbreviated explanation lives on the listing page.
 */
function AdWarnings({ ad }: { ad: AdRow }) {
  const flag = ad.cohort_flags?.[0];
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
        <span
          className="badge warn"
          title={ad.body_status || "توضیحات آگهی به تصادف، پلاک منطقه آزاد یا وضعیت بدنه اشاره کرده است"}
        >
          <AlertTriangle size={11} /> {ad.body_status || "وضعیت بدنه"}
        </span>
      )}
      {flag && (
        <span className="badge warn" title={FLAG_LABEL[flag] ?? flag}>
          <AlertTriangle size={11} />{" "}
          {flag === "price_outlier_high" ? "گران‌تر از مشابه‌ها" : "ارزان‌تر از مشابه‌ها"}
        </span>
      )}
    </>
  );
}

export function Explorer() {
  const filters = useFilters();
  const page = filters.getInt("page") ?? 1;
  const [selected, setSelected] = useState<string | null>(null);
  const view = filters.get("view") === "table" ? "table" : "cards";
  const ordering = filters.get("ordering") ?? "-publish_at";

  const adParams = {
    page, ordering,
    brand: filters.get("brand"),
    model: filters.get("model"),
    variant: filters.get("variant"),
    q: filters.get("q"),
    price_min: filters.get("price_min"),
    price_max: filters.get("price_max"),
    year_min: filters.get("year_min"),
    year_max: filters.get("year_max"),
    mileage_min: filters.get("mileage_min"),
    mileage_max: filters.get("mileage_max"),
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
    <div>
      <FilterPanel />

      <Card
        title="آگهی‌ها"
        action={
          <div className="row">
            <select
              value={ordering}
              onChange={(e) => filters.set({ ordering: e.target.value, page: 1 })}
              aria-label="ترتیب نمایش"
            >
              <option value="-publish_at">تازه‌ترین</option>
              <option value="current_price">ارزان‌ترین</option>
              <option value="-current_price">گران‌ترین</option>
              <option value="mileage">کمترین کارکرد</option>
              <option value="-mileage">بیشترین کارکرد</option>
              <option value="-year_jalali">جدیدترین سال ساخت</option>
            </select>
            <div className="segmented">
              <button
                className={view === "cards" ? "on" : ""}
                onClick={() => filters.set({ view: null })}
              >
                کارتی
              </button>
              <button
                className={view === "table" ? "on" : ""}
                onClick={() => filters.set({ view: "table" })}
              >
                جدولی
              </button>
            </div>
          </div>
        }
      >
        <Async
          query={ads}
          empty="آگهی‌ای با این فیلترها پیدا نشد."
          shape={view === "cards" ? "cards" : "table"}
        >
          {(data) => (
            <>
              {view === "cards" ? (
                <div className="card-grid">
                  {data.results.map((ad) => (
                    // A div, not a Link: clicking a card prices it in the panel
                    // beside it. It used to be a Link that *also* set the
                    // selection, so the fair-price panel it populated was
                    // unmounted by the navigation before anyone could read it.
                    <div
                      key={ad.code}
                      className="listing-card"
                      onClick={() => setSelected(ad.code)}
                      onKeyDown={(e) => e.key === "Enter" && setSelected(ad.code)}
                      role="button"
                      tabIndex={0}
                      aria-pressed={selected === ad.code}
                      style={{ cursor: "pointer" }}
                    >
                      <Thumb src={ad.image_url}>
                        <span className="card-badges">
                          <AdWarnings ad={ad} />
                        </span>
                      </Thumb>
                      <div className="listing-meta">
                        <strong><Fa>{ad.title}</Fa></strong>
                        <span className="deal-price">{toman(ad.current_price)}</span>
                        <div className="row">
                          <span>{km(ad.mileage)}</span>
                          <span>·</span>
                          <span>{ad.year_jalali ?? ad.year ?? "—"}</span>
                        </div>
                        <div className="row">
                          <Fa>{ad.city_name || "—"}</Fa>
                          <Link
                            to={`/listing/${ad.code}`}
                            style={{ marginInlineStart: "auto" }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            جزئیات
                          </Link>
                        </div>
                        <div className="row">
                          <BamaLink href={ad.bama_url} className="ghost" />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <Table head={["خودرو", "سال", "کارکرد", "قیمت", ""]}>
                  {data.results.map((ad) => (
                    <tr
                      key={ad.code}
                      onClick={() => setSelected(ad.code)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <Fa>{ad.title || ad.model_name}</Fa>
                        <div className="row" style={{ marginTop: 4 }}>
                          <AdWarnings ad={ad} />
                        </div>
                      </td>
                      <td className="num">{ad.year_jalali ?? ad.year ?? "—"}</td>
                      <td className="num">
                        {ad.mileage != null
                          ? `${ad.mileage.toLocaleString("en-US")} km`
                          : "—"}
                      </td>
                      <td className="num">{toman(ad.current_price)}</td>
                      <td className="num">
                        <BamaLink href={ad.bama_url} className="ghost">باما</BamaLink>
                      </td>
                    </tr>
                  ))}
                </Table>
              )}
              <div style={{ marginTop: 10 }}>
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
        </Async>
      </Card>

      {selected && (
        <FairPriceSheet code={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function FairPriceSheet({ code, onClose }: { code: string; onClose: () => void }) {
  const query = useQuery({
    queryKey: ["fair-price", code],
    queryFn: ({ signal }) => api.get<FairPrice>(`/api/ads/${code}/fair-price/`, signal),
  });

  return (
    <Sheet title="قیمت منصفانه" onClose={onClose}>
      <Async query={query} shape="table">
        {(data) => (
          <>
            <div className="grid cols-2" style={{ marginBottom: 12 }}>
              <div>
                <div className="card-title">قیمت درخواستی فروشنده</div>
                <div className="stat">{toman(data.asking)}</div>
              </div>
              <div>
                <div className="card-title">قیمت منصفانه</div>
                <div className={`stat ${(data.gap_pct ?? 0) < 0 ? "up" : ""}`}>
                  {toman(data.fair_value)}
                </div>
                {data.gap_pct != null && (
                  <div className="stat-sub">
                    {Math.abs(data.gap_pct)}٪{" "}
                    {data.gap_pct > 0 ? "بالاتر از" : "پایین‌تر از"} قیمت منصفانه
                  </div>
                )}
              </div>
            </div>

            {/* The answer first, the arithmetic under it. */}
            <PriceBar distribution={data.distribution} asking={data.asking} />

            <p className="empty-hint">
              بر پایه {data.peer_count} آگهی مشابه با همان مدل، تیپ و سال ساخت.
            </p>

            <Table head={["جزء محاسبه", "تومان"]}>
              {data.components.map((c) => (
                <tr key={c.name}>
                  <td>
                    {COMPONENT_LABEL[c.name] ?? c.name.replace(/_/g, " ")}
                    <div className="stat-sub"><Fa>{c.detail}</Fa></div>
                  </td>
                  <td className="num">
                    {c.amount > 0 && c.name !== "cohort_median" ? "+" : ""}
                    {toman(c.amount)}
                  </td>
                </tr>
              ))}
            </Table>

            <div className="row" style={{ marginTop: 12 }}>
              <Link className="btn" to={`/listing/${code}`}>صفحه آگهی</Link>
              <ListingActions code={code} />
            </div>
            <Provenance envelope={data} />
          </>
        )}
      </Async>
    </Sheet>
  );
}
