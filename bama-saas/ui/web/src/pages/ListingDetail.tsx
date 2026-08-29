import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  Async, BamaLink, FLAG_LABEL, Fa, ListingActions, PriceBar, Provenance, toman,
} from "../ui";
import type { Distribution } from "../ui";

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
  district?: string;
  body_status?: string;
  description: string;
  // Proxied through our own origin (apps/core/images.py), not hotlinked.
  image_url: string;
  image_urls: string[];
  // Absolute, unlike the raw `url` column it is derived from — that one is a
  // site-relative path and resolved against this app, not bama.ir.
  bama_url: string;
  seller_authenticated: boolean | null;
  cohort_flags: string[];
  // The browse list is ACTIVE-only, but this route is deliberately not: a saved
  // ad that has since been delisted must still open. It has to say so.
  status?: string;
  last_seen_at?: string | null;
  removed_at?: string | null;
  likely_reason?: string;
  reason_confidence?: string;
  reposted_from?: string | null;
  price_basis_unclear?: boolean;
  condition_flagged?: boolean;
  mileage_implausible?: boolean;
  // Not yet in the generated schema (another agent is adding these to the API);
  // kept optional/local so this page renders fine against an old API response too.
  seller_type?: "dealer" | "private" | null;
  dealer_name?: string | null;
};

type FairPrice = {
  available?: boolean;
  reason?: string;
  fair_value?: number | null;
  asking?: number | null;
  gap_pct?: number | null;
  peer_count?: number;
  distribution?: Distribution;
  as_of?: string;
  methodology_version?: number;
  coverage?: Record<string, unknown>;
};

/**
 * What we know about a listing that is no longer on the feed, and how sure we
 * are. Status and reason are shown separately on purpose: "absent" is something
 * we observed, "probably sold" is something we inferred, and collapsing the two
 * would have the page assert a sale that Bama never reported.
 */
function ListingState({
  status,
  reason,
  confidence,
  repostedFrom,
  lastSeen,
}: {
  status: string;
  reason?: string;
  confidence?: string;
  repostedFrom?: string | null;
  lastSeen?: string | null;
}) {
  if (status === "unverified") {
    return (
      <p className="badge warn">
        این آگهی را از دست داده‌ایم — آخرین بررسی کامل ما از باما ناقص بوده، پس
        نمی‌توانیم بگوییم هنوز برای فروش هست یا نه.
        {lastSeen && <> آخرین بار {new Date(lastSeen).toLocaleDateString("fa-IR")} دیده شد.</>}
      </p>
    );
  }

  const guess: Record<string, string> = {
    likely_sold: "احتمالاً فروخته شده",
    likely_expired: "احتمالاً اعتبار آگهی تمام شده",
    reposted: "با آگهی تازه‌ای دوباره ثبت شده",
    unknown: "دلیلش را نمی‌دانیم",
  };
  return (
    <p className="badge warn">
      دیگر در باما فهرست نشده است
      {lastSeen && <> — آخرین بار {new Date(lastSeen).toLocaleDateString("fa-IR")} دیده شد</>}
      {reason && guess[reason] && (
        <>
          {"، "}
          {confidence === "low" ? "حدس ما: " : "به احتمال زیاد: "}
          {guess[reason]}
        </>
      )}
      {repostedFrom && (
        <>
          {" "}
          <Link to={`/listing/${repostedFrom}`}>آگهی پیشین را ببینید</Link>.
        </>
      )}
    </p>
  );
}

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
    <div className="stack">
      <Async query={ad}>
        {(data) => (
          <>
            {/* One parent, and the right one. This read
                «معامله‌ها / جست‌وجو / ...» — two crumbs at the same level, the
                first of them pointing at the home page and labelled as the
                deal board. A listing is reached from the Explorer. */}
            <div className="breadcrumb">
              <Link to="/explore">جست‌وجوی آگهی‌ها</Link> / <Fa>{data.title}</Fa>
            </div>
            <div className="detail-layout">
              <div className="gallery card">
                {(data.image_urls?.length ? data.image_urls : data.image_url ? [data.image_url] : []).map((src) => (
                  <img key={src} src={src} alt="" loading="lazy" onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.3"; }} />
                ))}
                {!data.image_url && <div className="thumb-fallback">بدون تصویر</div>}
              </div>
              <div className="stack">
                <div className="card">
                  <h1><Fa>{data.title}</Fa></h1>
                  {data.status && data.status !== "active" && (
                    <ListingState
                      status={data.status}
                      reason={data.likely_reason}
                      confidence={data.reason_confidence}
                      repostedFrom={data.reposted_from}
                      lastSeen={data.last_seen_at}
                    />
                  )}
                  <p className="price">{data.current_price != null ? toman(data.current_price) : "—"}</p>
                  {data.price_basis_unclear && (
                    <p className="badge warn">
                      این عدد به احتمال زیاد پیش‌پرداخت یا قسط است، نه قیمت کامل
                      خودرو — به همین دلیل از فهرست معامله‌ها کنار گذاشته شده.
                    </p>
                  )}
                  {/* Name the field the flag came from. This always blamed the
                      description, but the flag fires first on Bama's structured
                      body-status column — so an ad reading «ماشین فوق العاده
                      سالم» was captioned as describing its own crash. */}
                  {data.condition_flagged && (
                    <p className="badge warn">
                      {data.body_status
                        ? `فروشنده وضعیت بدنه را «${data.body_status}» ثبت کرده — پیش از مقایسه قیمت آن را در نظر بگیرید.`
                        : "توضیحات آگهی به وضعیت خودرو اشاره کرده (تصادف، پلاک منطقه آزاد یا مشابه) — پیش از مقایسه قیمت آن را بخوانید."}
                    </p>
                  )}
                  <ul className="spec-list">
                    <li>سال ساخت: {data.year_jalali ?? "—"}</li>
                    {/* The unit is not decoration: "262,000" alone is a
                        number the reader has to guess the meaning of. */}
                    <li>
                      کارکرد: {data.mileage != null
                        ? `${data.mileage.toLocaleString("en-US")} کیلومتر`
                        : "—"}
                      {data.mileage_implausible && (
                        <span className="badge warn" style={{ marginInlineStart: 6 }}>
                          باورپذیر نیست — در محاسبه قیمت نادیده گرفته شده
                        </span>
                      )}
                    </li>
                    <li>وضعیت بدنه: <Fa>{data.body_status || "—"}</Fa></li>
                    <li>گیربکس: {data.transmission || "—"}</li>
                    <li>بدنه: {data.body_type || "—"}</li>
                    <li>سوخت: {data.fuel || "—"}</li>
                    <li>شهر: <Fa>{data.city_name || "—"}</Fa>{data.district ? ` / ${data.district}` : ""}</li>
                    <li>فروشنده احراز شده: {data.seller_authenticated == null ? "—" : data.seller_authenticated ? "بله" : "خیر"}</li>
                    {data.seller_type && (
                      <li>
                        {data.seller_type === "dealer"
                          ? `نمایشگاه: ${data.dealer_name || "—"}`
                          : "فروشنده شخصی"}
                      </li>
                    )}
                  </ul>
                  {data.cohort_flags?.map((f) => (
                    <p key={f} className="badge warn">
                      {FLAG_LABEL[f] ?? f}
                    </p>
                  ))}
                  <div className="row">
                    <BamaLink href={data.bama_url} />
                    <ListingActions code={data.code} />
                  </div>
                </div>
                <div className="card">
                  <h2>توضیحات</h2>
                  <p><Fa>{data.description || "توضیحی ثبت نشده است."}</Fa></p>
                </div>
              </div>
            </div>
          </>
        )}
      </Async>

      <div className="card">
        <h2>ارزیابی قیمت</h2>
        <Async query={fair}>
          {(fp) => (
            <>
              <p>
                قیمت منصفانه: {fp.fair_value != null ? toman(fp.fair_value) : "—"} تومان
              </p>
              {/* Where this car sits among its peers, before the arithmetic. */}
              <PriceBar distribution={fp.distribution} asking={fp.asking} />
              {fp.peer_count != null && (
                <p className="empty-hint">
                  بر پایه {fp.peer_count} آگهی مشابه با همان مدل، تیپ و سال ساخت.
                </p>
              )}
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
            if (!list.length) return <p className="muted">تغییر قیمتی ثبت نشده است.</p>;
            return (
              <div className="table-wrap">
              <table className="table">
                <thead><tr><th>زمان</th><th>قیمت</th></tr></thead>
                <tbody>
                  {list.slice(0, 20).map((r, i) => (
                    <tr key={i}>
                      <td>{new Date(r.observed_at).toLocaleDateString("fa-IR")}</td>
                      <td>{toman(r.price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            );
          }}
        </Async>
      </div>
    </div>
  );
}
