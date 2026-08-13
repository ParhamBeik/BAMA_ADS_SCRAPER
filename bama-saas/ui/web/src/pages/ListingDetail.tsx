import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { Async, Fa, Provenance, toman } from "../ui";
import { ListingActions } from "../engagement";

type Ad = {
  code: string;
  title: string;
  brand_name: string;
  model_name: string;
  variant_name: string;
  year_jalali: number | null;
  mileage: number | null;
  current_price: number | null;
  transmission: string;
  body_type: string;
  fuel: string;
  city_name: string;
  description: string;
  primary_image_url: string;
  image_urls: string[];
  url: string;
  seller_authenticated: boolean | null;
  cohort_flags: string[];
};

type FairPrice = {
  available?: boolean;
  reason?: string;
  fair_value?: number | null;
  as_of?: string;
  methodology_version?: number;
  coverage?: Record<string, unknown>;
};

export function ListingDetail() {
  const { code = "" } = useParams();
  const ad = useQuery({
    queryKey: ["ad", code],
    queryFn: ({ signal }) => api.get<Ad>(`/api/ads/${code}/`, signal),
    enabled: !!code,
  });
  const fair = useQuery({
    queryKey: ["fair-price", code],
    queryFn: ({ signal }) => api.get<FairPrice>(`/api/ads/${code}/fair-price/`, signal),
    enabled: !!code,
  });
  const history = useQuery({
    queryKey: ["price-history", code],
    queryFn: ({ signal }) => api.get<{ results?: { price: number; observed_at: string }[] } | { price: number; observed_at: string }[]>(`/api/ads/${code}/price-history/`, signal),
    enabled: !!code,
  });

  return (
    <div className="stack" dir="rtl">
      <Async query={ad}>
        {(data) => (
          <>
            <div className="breadcrumb">
              <Link to="/">خانه</Link> / <Link to="/explore">کاوش</Link> / <Fa>{data.title}</Fa>
            </div>
            <div className="detail-layout">
              <div className="gallery card">
                {(data.image_urls?.length ? data.image_urls : data.primary_image_url ? [data.primary_image_url] : []).map((src) => (
                  <img key={src} src={src} alt="" loading="lazy" onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.3"; }} />
                ))}
                {!data.primary_image_url && <div className="thumb-fallback">بدون تصویر</div>}
              </div>
              <div className="stack">
                <div className="card">
                  <h1><Fa>{data.title}</Fa></h1>
                  <p className="price">{data.current_price != null ? toman(data.current_price) : "—"}</p>
                  <ul className="spec-list">
                    <li>سال: {data.year_jalali ?? "—"}</li>
                    <li>کارکرد: {data.mileage?.toLocaleString("en-US") ?? "—"}</li>
                    <li>گیربکس: {data.transmission || "—"}</li>
                    <li>بدنه: {data.body_type || "—"}</li>
                    <li>سوخت: {data.fuel || "—"}</li>
                    <li>شهر: <Fa>{data.city_name || "—"}</Fa></li>
                    <li>فروشنده تأییدشده: {data.seller_authenticated == null ? "—" : data.seller_authenticated ? "بله" : "خیر"}</li>
                  </ul>
                  {data.cohort_flags?.length > 0 && (
                    <p className="warn">پرچم‌های کیفیت: {data.cohort_flags.join(", ")}</p>
                  )}
                  {data.url && (
                    <a className="btn" href={data.url} target="_blank" rel="noreferrer">مشاهده در باما</a>
                  )}
                  <ListingActions code={data.code} />
                </div>
                <div className="card">
                  <h2>توضیحات</h2>
                  <p><Fa>{data.description || "توضیحی ثبت نشده."}</Fa></p>
                </div>
              </div>
            </div>
          </>
        )}
      </Async>

      <div className="card">
        <h2>ارزیابی قیمت</h2>
        <Async query={fair}>
          {(fp) => fp.available === false ? (
            <p className="muted">داده کافی نیست: {fp.reason}</p>
          ) : (
            <>
              <p>قیمت منصفانه: {fp.fair_value != null ? toman(fp.fair_value) : "—"}</p>
              {fp.as_of && <Provenance envelope={fp as never} />}
            </>
          )}
        </Async>
      </div>

      <div className="card">
        <h2>تاریخچه قیمت</h2>
        <Async query={history}>
          {(rows) => {
            const list = Array.isArray(rows) ? rows : rows.results ?? [];
            if (!list.length) return <p className="muted">تغییر قیمتی ثبت نشده.</p>;
            return (
              <table className="table">
                <thead><tr><th>زمان</th><th>قیمت</th></tr></thead>
                <tbody>
                  {list.slice(0, 20).map((r, i) => (
                    <tr key={i}><td>{r.observed_at}</td><td>{toman(r.price)}</td></tr>
                  ))}
                </tbody>
              </table>
            );
          }}
        </Async>
      </div>
    </div>
  );
}
