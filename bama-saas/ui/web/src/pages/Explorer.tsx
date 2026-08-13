/**
 * Buyer Explorer — find a car, then find out what it is actually worth.
 *
 * Two product decisions are visible here.
 *
 * A listing flagged as an unbelievable price is *shown*, with its warning, not
 * hidden. A suspiciously cheap car is the most valuable thing this product can
 * surface; suppressing it to keep the statistics tidy would defeat the purpose.
 *
 * The fair-price panel renders the components, not just the verdict. "1.02B,
 * because the cohort median is 1.10B and this car has 60,000km more than its
 * peers" is checkable. A bare score is not.
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { api } from "../api/client";
import type { Envelope, Paginated } from "../api/client";
import { qs, useFilters } from "../filters";
import { Async, Card, Fa, Provenance, Table, toman } from "../ui";
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
}

interface Brand {
  slug: string;
  name_fa: string;
}

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

const FLAG_LABEL: Record<string, string> = {
  price_outlier_low: "Priced far below its peers — check why",
  price_outlier_high: "Priced far above its peers",
};

export function Explorer() {
  const filters = useFilters();
  const brand = filters.get("brand");
  const page = filters.getInt("page") ?? 1;
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<"cards" | "table">(filters.get("view") === "table" ? "table" : "cards");
  const q = filters.get("q") ?? "";
  const ordering = filters.get("ordering") ?? "-publish_at";

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) => api.get<Paginated<Brand> | Brand[]>("/api/brands/", signal),
  });

  const ads = useQuery({
    queryKey: ["ads", brand, page, q, ordering],
    queryFn: ({ signal }) =>
      api.get<Paginated<AdRow>>(`/api/ads/${qs({ brand, page, q, ordering })}`, signal),
  });

  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);

  return (
    <>
      <div className="filters">
        <input
          placeholder="جستجو…"
          defaultValue={q}
          onBlur={(e) => filters.set({ q: e.target.value || null, page: 1 })}
        />
        <select
          value={ordering}
          onChange={(e) => filters.set({ ordering: e.target.value, page: 1 })}
          aria-label="مرتب‌سازی"
        >
          <option value="-publish_at">جدیدترین</option>
          <option value="current_price">ارزان‌ترین</option>
          <option value="-current_price">گران‌ترین</option>
          <option value="mileage">کم‌کارکرد</option>
          <option value="-year_jalali">جدیدترین سال</option>
        </select>
        <div className="segmented">
          <button className={view === "cards" ? "on" : ""} onClick={() => { setView("cards"); filters.set({ view: "cards" }); }}>کارت</button>
          <button className={view === "table" ? "on" : ""} onClick={() => { setView("table"); filters.set({ view: "table" }); }}>جدول</button>
        </div>
        <select
          value={brand ?? ""}
          onChange={(e) => filters.set({ brand: e.target.value || null, page: null })}
          aria-label="Brand"
        >
          <option value="">All brands</option>
          {brandList.map((b) => (
            <option key={b.slug} value={b.slug}>
              {b.name_fa}
            </option>
          ))}
        </select>
        {brand && (
          <button onClick={() => filters.set({ brand: null, page: null })}>Clear</button>
        )}
      </div>

      <div className="grid cols-2">
        <Card title="Listings">
          <Async query={ads} empty="No listings match these filters.">
            {(data) => (
              <>
                {view === "cards" ? (
              <div className="card-grid">
                {data.results.map((ad) => (
                  <Link key={ad.code} to={`/listing/${ad.code}`} className="listing-card" onClick={() => setSelected(ad.code)}>
                    <div className="thumb">
                      {ad.primary_image_url ? (
                        <img src={ad.primary_image_url} alt="" loading="lazy" />
                      ) : (
                        <div className="thumb-fallback">—</div>
                      )}
                    </div>
                    <div className="listing-meta">
                      <strong><Fa>{ad.title}</Fa></strong>
                      <span>{toman(ad.current_price)}</span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
            <Table head={["Car", "Year", "Mileage", "Price"]}>
                  {data.results.map((ad) => {
                    const flag = ad.cohort_flags?.[0];
                    return (
                      <tr
                        key={ad.code}
                        onClick={() => setSelected(ad.code)}
                        style={{ cursor: "pointer" }}
                      >
                        <td>
                          <Fa>{ad.title || ad.model_name}</Fa>
                          {flag && (
                            <div className="badge warn" style={{ marginTop: 4 }}>
                              <AlertTriangle size={11} /> {FLAG_LABEL[flag] ?? flag}
                            </div>
                          )}
                        </td>
                        <td className="num">{ad.year ?? "—"}</td>
                        <td className="num">
                          {ad.mileage != null ? `${ad.mileage.toLocaleString("en-US")}km` : "—"}
                        </td>
                        <td className="num">{toman(ad.current_price)}</td>
                      </tr>
                    );
                  })}
                </Table>
                )}
                <div className="filters" style={{ marginTop: 10, marginBottom: 0 }}>
                  <button disabled={page <= 1} onClick={() => filters.set({ page: page - 1 })}>
                    Previous
                  </button>
                  <span className="stat-sub">
                    {data.count.toLocaleString("en-US")} listings
                  </span>
                  <button disabled={!data.next} onClick={() => filters.set({ page: page + 1 })}>
                    Next
                  </button>
                </div>
              </>
            )}
          </Async>
        </Card>

        <FairPricePanel code={selected} />
      </div>
    </>
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
        <div className="state">Select a listing to value it against its peers.</div>
      </Card>
    );
  }

  return (
    <Card title="Fair price">
      <Async query={query}>
        {(data) => (
          <>
            <div className="grid cols-2" style={{ marginBottom: 10 }}>
              <div>
                <div className="card-title">Asking</div>
                <div className="stat">{toman(data.asking)}</div>
              </div>
              <div>
                <div className="card-title">Fair value</div>
                <div className={`stat ${(data.gap_pct ?? 0) < 0 ? "up" : ""}`}>
                  {toman(data.fair_value)}
                </div>
                {data.gap_pct != null && (
                  <div className="stat-sub">
                    {data.gap_pct > 0 ? "above" : "below"} fair value by{" "}
                    {Math.abs(data.gap_pct)}%
                  </div>
                )}
              </div>
            </div>

            <Table head={["Component", "Toman"]}>
              {data.components.map((c) => (
                <tr key={c.name}>
                  <td>
                    {c.name.replace(/_/g, " ")}
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
