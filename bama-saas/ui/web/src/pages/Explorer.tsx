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
import { AlertTriangle } from "lucide-react";
import { api } from "../api/client";
import type { Envelope, Paginated } from "../api/client";
import { qs, useFilters } from "../filters";
import { Async, Card, Fa, Provenance, Table, toman } from "../ui";

interface AdRow {
  code: string;
  title: string;
  brand_name: string;
  model_name: string;
  model_id: number | null;
  year: number | null;
  mileage: number | null;
  current_price: number | null;
  city_name: string;
  cohort_flags: string[];
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

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) => api.get<Paginated<Brand> | Brand[]>("/api/brands/", signal),
  });

  const ads = useQuery({
    queryKey: ["ads", brand, page],
    queryFn: ({ signal }) =>
      api.get<Paginated<AdRow>>(`/api/ads/${qs({ brand, page })}`, signal),
  });

  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);

  return (
    <>
      <div className="filters">
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

            <div className="filters" style={{ marginTop: 10, marginBottom: 0 }}>
              <span className="badge">{data.peer_count} peers</span>
              <span className={`badge ${data.confidence === "low" ? "warn" : "accent"}`}>
                {data.confidence} confidence
              </span>
              {data.dispersion != null && (
                <span className="badge">spread {(data.dispersion * 100).toFixed(1)}%</span>
              )}
            </div>
            <Provenance envelope={data} />
          </>
        )}
      </Async>
    </Card>
  );
}
