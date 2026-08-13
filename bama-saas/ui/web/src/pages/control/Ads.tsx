import { type FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async } from "../../ui";

type AdRow = {
  code: string;
  title: string;
  status: string;
  brand_slug: string | null;
  brand_name: string | null;
  model_id: number | null;
  model_name: string | null;
  variant_name: string | null;
  year: number | null;
  year_jalali: number | null;
  year_gregorian: number | null;
  year_calendar: string;
  mileage: number | null;
  current_price: number | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  removed_at: string | null;
  quality_flags: string[];
  cohort_flags: string[];
};

type AdDetail = AdRow & {
  category: string;
  price_type: string;
  publish_at: string | null;
  trim: string;
  city_name: string | null;
  dealer_id: number | null;
  transmission: string;
  body_type: string;
  fuel: string;
  url: string;
  canonical_path: string;
  source_modified_at: string | null;
  description: string;
  image_count: number | null;
  seller_authenticated: boolean | null;
  raw_payload: unknown;
  observation_count: number;
  version_count: number;
};

type Page<T> = { count: number; page: number; page_size: number; results: T[] };

function cell(v: unknown) {
  if (v == null || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  return String(v);
}

export function ControlAds() {
  const { code } = useParams();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [flagged, setFlagged] = useState(false);
  const [page, setPage] = useState(1);

  const list = useQuery({
    queryKey: ["inspect-ads", q, status, flagged, page],
    queryFn: ({ signal }) => {
      const p = new URLSearchParams({ page: String(page), page_size: "50" });
      if (q) p.set("q", q);
      if (status) p.set("status", status);
      if (flagged) p.set("flagged", "1");
      return api.get<Page<AdRow>>(`/api/admin/inspect/ads/?${p}`, signal);
    },
    enabled: !code,
  });

  const detail = useQuery({
    queryKey: ["inspect-ad", code],
    queryFn: ({ signal }) => api.get<AdDetail>(`/api/admin/inspect/ads/${code}/`, signal),
    enabled: !!code,
  });

  function onSearch(e: FormEvent) {
    e.preventDefault();
    setPage(1);
    void list.refetch();
  }

  if (code) {
    return (
      <div className="stack">
        <p><Link to="/control/ads">← Ads</Link></p>
        <h1>Ad {code}</h1>
        <Async query={detail}>
          {(ad) => (
            <>
              <table className="table inspect-table">
                <tbody>
                  {([
                    "code", "status", "title", "brand_slug", "brand_name", "model_id", "model_name",
                    "variant_name", "year", "year_jalali", "year_gregorian", "year_calendar",
                    "mileage", "current_price", "price_type", "category", "trim", "city_name",
                    "dealer_id", "first_seen_at", "last_seen_at", "publish_at", "removed_at",
                    "quality_flags", "cohort_flags", "observation_count", "version_count",
                    "url", "canonical_path", "source_modified_at",
                  ] as const).map((k) => (
                    <tr key={k}><th>{k}</th><td>{cell(ad[k])}</td></tr>
                  ))}
                </tbody>
              </table>
              <h2>raw_payload</h2>
              <pre className="card code-block">{JSON.stringify(ad.raw_payload, null, 2)}</pre>
            </>
          )}
        </Async>
      </div>
    );
  }

  return (
    <div className="stack">
      <h1>Ads</h1>
      <p className="muted">catalog_ad rows. Not filtered by verified().</p>
      <form className="row" onSubmit={onSearch}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="code or title" />
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
          <option value="">status=any</option>
          <option value="active">active</option>
          <option value="removed">removed</option>
        </select>
        <label>
          <input type="checkbox" checked={flagged} onChange={(e) => { setFlagged(e.target.checked); setPage(1); }} />
          {" "}flagged
        </label>
        <button className="btn" type="submit">filter</button>
      </form>
      <Async query={list}>
        {(data) => (
          <>
            <p className="muted">{data.count.toLocaleString("en-US")} rows</p>
            <table className="table inspect-table">
              <thead>
                <tr>
                  <th>code</th><th>status</th><th>brand</th><th>model</th>
                  <th>year_jalali</th><th>price</th><th>last_seen_at</th><th>quality_flags</th>
                </tr>
              </thead>
              <tbody>
                {data.results.map((ad) => (
                  <tr key={ad.code} className="clickable" onClick={() => nav(`/control/ads/${ad.code}`)}>
                    <td><code>{ad.code}</code></td>
                    <td>{ad.status}</td>
                    <td>{ad.brand_name ?? ad.brand_slug}</td>
                    <td>{ad.model_name}</td>
                    <td>{cell(ad.year_jalali)}</td>
                    <td>{cell(ad.current_price)}</td>
                    <td>{cell(ad.last_seen_at)}</td>
                    <td>{cell(ad.quality_flags)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row">
              <button className="btn" disabled={page <= 1} onClick={() => setPage(page - 1)}>prev</button>
              <span>page {data.page}</span>
              <button className="btn" disabled={page * data.page_size >= data.count} onClick={() => setPage(page + 1)}>next</button>
            </div>
          </>
        )}
      </Async>
    </div>
  );
}
