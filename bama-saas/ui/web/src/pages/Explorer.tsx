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
 * The fair-price panel renders the components, not just the verdict. "1.02B,
 * because the cohort median is 1.10B and this car has 60,000km more than its
 * peers" is checkable. A bare score is not.
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
import { api } from "../api/client";
import type { Envelope, Paginated } from "../api/client";
import { qs, useFilters } from "../filters";
import { Async, Card, FLAG_LABEL, Fa, Pager, Provenance, Table, Thumb, km, toman } from "../ui";
import { ListingActions } from "../engagement";

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
  cohort_flags: string[];
  primary_image_url?: string;
  price_basis_unclear?: boolean;
  condition_flagged?: boolean;
}

interface Brand {
  slug: string;
  name_fa: string;
}

interface Model {
  id: number;
  name_fa: string;
}

// Exact strings as stored on Ad (scraped, no fixed vocabulary in the backend);
// AdFilter matches `transmission` case-sensitively and `fuel`/`body_type`
// case-insensitively, so values here must match the DB text byte-for-byte.
const TRANSMISSIONS = ["اتوماتیک", "دنده ای"];
const FUELS = ["بنزینی", "هیبریدی", "برقی", "دوگانه سوز", "پلاگین هیبرید", "بردافزا", "هیبرید ملایم", "دیزلی"];
const BODY_TYPES = ["سدان", "کراس اور", "هاچبک", "وانت", "شاسی بلند‌", "ون", "کوپه", "کروک"];

// Must match REST_FRAMEWORK.PAGE_SIZE in config/settings/base.py — the API
// gives back a count and a next/previous link, never a page size, so the
// pager has to know it independently to compute the last page.
const PAGE_SIZE = 50;

const FILTER_KEYS = [
  "brand", "model", "q", "price_min", "price_max", "year_min", "year_max",
  "mileage_max", "transmission", "fuel", "body_type",
];

interface FairPrice extends Envelope {
  code: string;
  asking: number | null;
  fair_value: number | null;
  gap_pct: number | null;
  peer_count: number;
  dispersion: number | null;
  confidence: string;
  components: { name: string; amount: number; detail: string }[];
}

const COMPONENT_LABEL: Record<string, string> = {
  cohort_median: "Peer median",
  mileage: "Mileage adjustment",
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
        <span className="badge warn" title="This number is likely a down payment or installment, not the full car price">
          <Wallet size={11} /> Down payment?
        </span>
      )}
      {ad.condition_flagged && (
        <span className="badge warn" title="Listing description mentions an accident, free-zone plate, or body condition">
          <AlertTriangle size={11} /> Condition
        </span>
      )}
      {flag && (
        <span className="badge warn" title={FLAG_LABEL[flag] ?? flag}>
          <AlertTriangle size={11} /> {flag === "price_outlier_high" ? "Overpriced" : "Underpriced"}
        </span>
      )}
    </>
  );
}

export function Explorer() {
  const filters = useFilters();
  const brand = filters.get("brand");
  const page = filters.getInt("page") ?? 1;
  const [selected, setSelected] = useState<string | null>(null);
  const view = filters.get("view") === "table" ? "table" : "cards";
  const q = filters.get("q") ?? "";
  const ordering = filters.get("ordering") ?? "-publish_at";
  const model = filters.get("model");
  const priceMin = filters.get("price_min");
  const priceMax = filters.get("price_max");
  const yearMin = filters.get("year_min");
  const yearMax = filters.get("year_max");
  const mileageMax = filters.get("mileage_max");
  const transmission = filters.get("transmission");
  const fuel = filters.get("fuel");
  const bodyType = filters.get("body_type");

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) => api.get<Paginated<Brand> | Brand[]>("/api/brands/", signal),
  });

  const models = useQuery({
    queryKey: ["models", brand],
    enabled: Boolean(brand),
    queryFn: ({ signal }) => api.get<Model[]>(`/api/brands/${brand}/models/`, signal),
  });

  const adParams = {
    brand, page, q, ordering, model,
    price_min: priceMin, price_max: priceMax,
    year_min: yearMin, year_max: yearMax,
    mileage_max: mileageMax, transmission, fuel, body_type: bodyType,
  };

  const ads = useQuery({
    queryKey: ["ads", adParams],
    queryFn: ({ signal }) => api.get<Paginated<AdRow>>(`/api/ads/${qs(adParams)}`, signal),
  });

  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);
  const modelList: Model[] = models.data ?? [];
  const hasActiveFilter = FILTER_KEYS.some((k) => filters.get(k));

  const clearFilters = () => {
    const cleared: Record<string, null> = { page: null };
    for (const k of FILTER_KEYS) cleared[k] = null;
    filters.set(cleared);
  };

  return (
    <div dir="rtl">
      <div className="filters">
        <input
          key={q}
          placeholder="Search…"
          defaultValue={q}
          onBlur={(e) => filters.set({ q: e.target.value || null, page: 1 })}
        />
        <select
          value={ordering}
          onChange={(e) => filters.set({ ordering: e.target.value, page: 1 })}
          aria-label="Sort by"
        >
          <option value="-publish_at">Newest</option>
          <option value="current_price">Cheapest</option>
          <option value="-current_price">Most expensive</option>
          <option value="mileage">Lowest mileage</option>
          <option value="-year_jalali">Newest model year</option>
        </select>
        <div className="segmented">
          <button
            className={view === "cards" ? "on" : ""}
            onClick={() => filters.set({ view: null })}
          >
            Cards
          </button>
          <button
            className={view === "table" ? "on" : ""}
            onClick={() => filters.set({ view: "table" })}
          >
            Table
          </button>
        </div>
        <select
          value={brand ?? ""}
          onChange={(e) => filters.set({ brand: e.target.value || null, model: null, page: null })}
          aria-label="Brand"
        >
          <option value="">All brands</option>
          {brandList.map((b) => (
            <option key={b.slug} value={b.slug}>
              {b.name_fa}
            </option>
          ))}
        </select>
        {hasActiveFilter && <button onClick={clearFilters}>Clear filters</button>}
      </div>

      <details className="filters-adv">
        <summary>Advanced filters</summary>
        <div className="filters">
          <select
            value={model ?? ""}
            onChange={(e) => filters.set({ model: e.target.value || null, page: null })}
            disabled={!brand}
            aria-label="Model"
          >
            <option value="">All models</option>
            {modelList.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name_fa}
              </option>
            ))}
          </select>

          <input
            key={`price_min-${priceMin}`}
            type="number"
            placeholder="Min price (toman)"
            defaultValue={priceMin ?? ""}
            onBlur={(e) => filters.set({ price_min: e.target.value || null, page: null })}
          />
          <input
            key={`price_max-${priceMax}`}
            type="number"
            placeholder="Max price (toman)"
            defaultValue={priceMax ?? ""}
            onBlur={(e) => filters.set({ price_max: e.target.value || null, page: null })}
          />

          <input
            key={`year_min-${yearMin}`}
            type="number"
            placeholder="From year (Jalali)"
            defaultValue={yearMin ?? ""}
            onBlur={(e) => filters.set({ year_min: e.target.value || null, page: null })}
          />
          <input
            key={`year_max-${yearMax}`}
            type="number"
            placeholder="To year (Jalali)"
            defaultValue={yearMax ?? ""}
            onBlur={(e) => filters.set({ year_max: e.target.value || null, page: null })}
          />

          <input
            key={`mileage_max-${mileageMax}`}
            type="number"
            placeholder="Max mileage"
            defaultValue={mileageMax ?? ""}
            onBlur={(e) => filters.set({ mileage_max: e.target.value || null, page: null })}
          />

          <select
            value={transmission ?? ""}
            onChange={(e) => filters.set({ transmission: e.target.value || null, page: null })}
            aria-label="Transmission"
          >
            <option value="">Transmission (all)</option>
            {TRANSMISSIONS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>

          <select
            value={fuel ?? ""}
            onChange={(e) => filters.set({ fuel: e.target.value || null, page: null })}
            aria-label="Fuel"
          >
            <option value="">Fuel (all)</option>
            {FUELS.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>

          <select
            value={bodyType ?? ""}
            onChange={(e) => filters.set({ body_type: e.target.value || null, page: null })}
            aria-label="Body type"
          >
            <option value="">Body type (all)</option>
            {BODY_TYPES.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        </div>
      </details>

      <div className="grid cols-2">
        <Card title="Listings">
          <Async
            query={ads}
            empty="No listings match these filters."
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
                        <Thumb src={ad.primary_image_url}>
                          <span className="card-badges">
                            <AdWarnings ad={ad} />
                          </span>
                        </Thumb>
                        <div className="listing-meta">
                          <strong><Fa>{ad.title}</Fa></strong>
                          <span className="deal-price">{toman(ad.current_price)}</span>
                          <div className="row">
                            <span>{ad.year_jalali ?? ad.year ?? "—"}</span>
                            <span>·</span>
                            <span>{km(ad.mileage)}</span>
                          </div>
                          <div className="row">
                            <Fa>{ad.city_name || "—"}</Fa>
                            <Link to={`/listing/${ad.code}`} style={{ marginInlineStart: "auto" }}>
                              Details
                            </Link>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Table head={["Car", "Year", "Mileage", "Price"]}>
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
                      </tr>
                    ))}
                  </Table>
                )}
                <div style={{ marginTop: 10 }}>
                  <Pager
                    page={page}
                    lastPage={Math.max(1, Math.ceil(data.count / PAGE_SIZE))}
                    total={data.count}
                    label="active priced listings"
                    onChange={(next) => filters.set({ page: next })}
                  />
                </div>
              </>
            )}
          </Async>
        </Card>

        <FairPricePanel code={selected} />
      </div>
    </div>
  );
}

function FairPricePanel({ code }: { code: string | null }) {
  const query = useQuery({
    queryKey: ["fair-price", code],
    enabled: Boolean(code),
    queryFn: ({ signal }) => api.get<FairPrice>(`/api/ads/${code}/fair-price/`, signal),
  });

  if (!code) {
    return (
      <Card title="Fair price">
        <div className="state">
          <strong>Select a listing.</strong>
          <p className="empty-hint">
            Click any card or row to compare its price against its peer group.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <Card title="Fair price">
      <Async query={query} shape="table">
        {(data) => (
          <>
            <div className="grid cols-2" style={{ marginBottom: 10 }}>
              <div>
                <div className="card-title">Seller's asking price</div>
                <div className="stat">{toman(data.asking)}</div>
              </div>
              <div>
                <div className="card-title">Fair value</div>
                <div className={`stat ${(data.gap_pct ?? 0) < 0 ? "up" : ""}`}>
                  {toman(data.fair_value)}
                </div>
                {data.gap_pct != null && (
                  <div className="stat-sub">
                    {Math.abs(data.gap_pct)}%{" "}
                    {data.gap_pct > 0 ? "above" : "below"} fair value
                  </div>
                )}
              </div>
            </div>

            <Table head={["Component", "Toman"]}>
              {data.components.map((c) => (
                <tr key={c.name}>
                  <td>
                    {COMPONENT_LABEL[c.name] ?? c.name.replace(/_/g, " ")}
                    <div className="stat-sub">{c.detail}</div>
                  </td>
                  <td className="num">
                    {c.amount > 0 && c.name !== "cohort_median" ? "+" : ""}
                    {toman(c.amount)}
                  </td>
                </tr>
              ))}
            </Table>

            <Provenance envelope={data} />
            <ListingActions code={code} />
          </>
        )}
      </Async>
    </Card>
  );
}
