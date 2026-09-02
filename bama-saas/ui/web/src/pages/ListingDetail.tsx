import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import {
  Async, BamaLink, FLAG_LABEL, Fa, ListingActions, PriceBar, PriceVerdict, Provenance,
  fa, pct, toman,
} from "../ui";
import type { Basis, Distribution, Verdict } from "../ui";

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
  verdict?: Verdict;
  basis?: Basis;
  confidence?: string;
  position_pct?: number | null;
  peer_count?: number;
  cohort_stale?: boolean;
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
              {/* The one line a reader can act on, before any arithmetic. */}
              <PriceVerdict
                verdict={fp.verdict}
                gapPct={fp.gap_pct}
                basis={fp.basis}
                peerCount={fp.peer_count}
                confidence={fp.confidence}
              />
              <table className="table mini-table">
                <tbody>
                  <tr>
                    <th>این آگهی</th>
                    <td className="num">{toman(fp.asking)}</td>
                  </tr>
                  <tr>
                    <th>قیمت منصفانه</th>
                    <td className="num">
                      {fp.fair_value != null ? toman(fp.fair_value) : "—"}
                    </td>
                  </tr>
                  {fp.distribution?.median != null && (
                    <tr>
                      <th>میانه‌ی گروه</th>
                      <td className="num">{toman(fp.distribution.median)}</td>
                    </tr>
                  )}
                  {/* Mode beside median: in a market that quotes round numbers
                      "what people actually ask" and "the middle" differ, and
                      both are worth showing. */}
                  {fp.distribution?.mode?.value != null && (
                    <tr>
                      <th>پرتکرارترین قیمت</th>
                      <td className="num">
                        {toman(fp.distribution.mode.value)}
                        <span className="muted">
                          {" "}({fa(fp.distribution.mode.count)} آگهی)
                        </span>
                      </td>
                    </tr>
                  )}
                  {fp.distribution?.p10 != null && (
                    <tr>
                      <th>بازه‌ی رایج</th>
                      <td className="num">
                        {toman(fp.distribution.p10)} — {toman(fp.distribution.p90)}
                      </td>
                    </tr>
                  )}
                  {fp.position_pct != null && (
                    <tr>
                      <th>جایگاه در گروه</th>
                      <td className="num">
                        گران‌تر از {pct(fp.position_pct, 0)} آگهی‌های مشابه
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {/* Where this car sits among its peers, before the arithmetic. */}
              <PriceBar distribution={fp.distribution} asking={fp.asking} />
              {/* Why the confidence may read lower than the peer count alone
                  suggests. Without this the badge appears to contradict the
                  number printed beside it. */}
              {fp.cohort_stale && (
                <p className="badge warn">
                  آگهی‌های مشابه این خودرو مدتی است دوباره دیده نشده‌اند، پس این
                  برآورد با احتیاط بیشتری خوانده شود.
                </p>
              )}
              {fp.as_of && <Provenance envelope={fp as never} />}
            </>
          )}
        </Async>
      </div>

      {/* The deal board's own verdict on this listing.
          `/api/analytics/deal-scores/<code>/` exists so a detail card can never
          disagree with the row the reader clicked — and until now nothing
          called it, so the listing page showed a fair-price estimate with no
          way to tell whether the board had picked this car out at all. A 404
          is the ordinary case (most listings are not on the board), so it
          renders nothing rather than an error. */}
      <DealVerdict code={code} />

      {/* The learned estimate, deliberately *below* the statistical one and
          never in place of it. Two independent accounts of the same car, and
          the reader gets to see where they disagree. */}
      <ModelEstimate code={code} />

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

interface DealRow {
  discount_pct: number | null;
  peer_median: number | null;
  peer_count: number | null;
  confidence: string | null;
  days_listed: number | null;
  liquidity?: { left_pct: number; n: number; window_days: number } | null;
  components?: { cohort_stale?: boolean };
}

function DealVerdict({ code }: { code: string }) {
  const verdict = useQuery({
    queryKey: ["deal-score", code],
    enabled: !!code,
    // Not on the board is the common case and not an error, so a 404 must not
    // be retried and must not surface as a failed panel.
    retry: false,
    queryFn: ({ signal }) =>
      api.get<DealRow>(`/api/analytics/deal-scores/${code}/`, signal),
  });

  if (!verdict.data) return null;
  const d = verdict.data;
  return (
    <div className="card">
      <h2>در فهرست معامله‌ها</h2>
      <p className="stat-sub" style={{ marginTop: 0 }}>
        این آگهی روی تابلوی معامله‌ها هست — یعنی زیر میانه قیمت آگهی‌های مشابه
        خودش قیمت خورده است.
      </p>
      <ul className="spec-list">
        <li>
          فاصله تا میانه مشابه‌ها: {d.discount_pct != null ? pct(d.discount_pct) : "—"}
        </li>
        <li>میانه قیمت مشابه‌ها: {toman(d.peer_median)}</li>
        <li>
          تعداد آگهی مشابه: {d.peer_count ?? "—"}
          {d.components?.cohort_stale && " (مدتی است دوباره دیده نشده‌اند)"}
        </li>
        {d.liquidity && (
          <li>
            {Math.round(d.liquidity.left_pct)}٪ از این مدل ظرف{" "}
            {d.liquidity.window_days} روز از باما برداشته می‌شوند — از{" "}
            {d.liquidity.n} آگهی
          </li>
        )}
      </ul>
      <Link className="btn" to="/deals">همه معامله‌ها</Link>
    </div>
  );
}

/** What the learned models say about this car — the band, and what moved it. */
interface Prediction {
  available: boolean;
  reason?: string;
  price_p10: number;
  price_p50: number;
  price_p90: number;
  residual_pct: number | null;
  contributions: { feature: string; effect_pct: number | null; base_price?: number }[];
  anomaly_kind: string | null;
  sell_fast_prob: number | null;
  sell_fast_horizon_days: number | null;
  value_tier: string | null;
  value_tier_rank: number | null;
}

/**
 * Feature names, in the language of the car rather than of the column.
 *
 * The API returns machine keys — that is the house rule and it is the right one
 * — so the mapping to Persian lives here, on the screen that draws it.
 */
const FEATURE_LABEL: Record<string, string> = {
  mileage: "کارکرد",
  log_mileage: "کارکرد",
  year_jalali: "سال ساخت",
  age_years: "سن خودرو",
  condition_ordinal: "وضعیت بدنه",
  days_listed: "مدت حضور آگهی",
  image_count: "تعداد عکس",
  description_length: "طول توضیحات",
  seller_authenticated: "احراز هویت فروشنده",
  is_dealer: "فروشنده نمایشگاهی",
  brand_id: "برند",
  model_id: "مدل",
  variant_id: "تیپ",
  city_id: "شهر",
  body_type: "نوع بدنه",
  fuel: "سوخت",
  transmission: "گیربکس",
};

/** Tier 1 of 4 is the cheap, high-mileage end; the last is the clean end. */
function tierLabel(tier: string | null, rank: number | null): string | null {
  if (!tier || rank == null) return null;
  const total = Number(tier.split("_of_")[1] ?? 0);
  if (!total) return null;
  if (rank === 0) return "ارزان‌ترین لایه‌ی این تیپ";
  if (rank === total - 1) return "گران‌ترین و معمولاً تمیزترین لایه‌ی این تیپ";
  return `لایه‌ی ${rank + 1} از ${total} در این تیپ`;
}

function ModelEstimate({ code }: { code: string }) {
  const prediction = useQuery({
    queryKey: ["ml-prediction", code],
    enabled: !!code,
    // An unscored listing is the ordinary case on a fresh database or right
    // after a rollback, not an error worth retrying.
    retry: false,
    queryFn: ({ signal }) =>
      api.get<Prediction>(`/api/ads/${code}/prediction/`, signal),
  });

  const p = prediction.data;
  if (!p?.available) return null;
  const base = p.contributions.find((c) => c.feature === "_base");
  const drivers = p.contributions.filter((c) => c.effect_pct != null);
  const tier = tierLabel(p.value_tier, p.value_tier_rank);

  return (
    <div className="card">
      <h2>برآورد مدل یادگیرنده</h2>
      <p className="stat-sub" style={{ marginTop: 0 }}>
        این برآورد جای میانه‌ی آگهی‌های مشابه را نمی‌گیرد — کنارش می‌نشیند.
        تفاوتش این است که کارکرد، وضعیت بدنه، شهر و نوع فروشنده را هم می‌بیند،
        چیزهایی که کلید «مدل، تیپ، سال» درباره‌شان چیزی نمی‌داند.
      </p>

      <ul className="spec-list">
        <li>برآورد میانی: {toman(p.price_p50)}</li>
        <li>
          بازه‌ی محتمل: {toman(p.price_p10)} تا {toman(p.price_p90)}
        </li>
        {p.residual_pct != null && (
          <li className={p.residual_pct > 0 ? "up" : undefined}>
            فاصله‌ی قیمت آگهی تا برآورد: {pct(p.residual_pct)}
          </li>
        )}
        {p.sell_fast_prob != null && p.sell_fast_horizon_days != null && (
          <li>
            احتمال برداشته‌شدن ظرف {p.sell_fast_horizon_days} روز:{" "}
            {pct(p.sell_fast_prob * 100, 0)}
          </li>
        )}
        {tier && <li>{tier}</li>}
      </ul>

      {p.anomaly_kind === "data_anomaly" && (
        <p className="badge warn" style={{ marginTop: 8 }}>
          این آگهی در فضای ویژگی‌ها غیرعادی است — پیش از هر نتیجه‌گیری، خود
          اطلاعات آگهی را بررسی کنید.
        </p>
      )}

      {drivers.length > 0 && (
        <>
          <h3 className="card-title" style={{ marginTop: 12 }}>
            چه چیزی این برآورد را ساخت
          </h3>
          <table className="mini-table">
            <tbody>
              {base?.base_price != null && (
                <tr>
                  <td>نقطه‌ی شروع (میانگین بازار)</td>
                  <td className="num">{toman(base.base_price)}</td>
                </tr>
              )}
              {drivers.map((c) => (
                <tr key={c.feature}>
                  <td>{FEATURE_LABEL[c.feature] ?? c.feature}</td>
                  <td className={`num ${(c.effect_pct ?? 0) > 0 ? "up" : "down"}`}>
                    {pct(c.effect_pct, 1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {/* Said out loud because the arithmetic on screen would not otherwise
              reconcile, and an unreconcilable table is the failure this
              codebase rebuilt the discount badge to avoid. */}
          <p className="muted text-[11px]">
            سهم‌ها ضرب‌شونده‌اند، نه جمع‌شونده — جمعشان عمداً برابر اختلاف
            نهایی نمی‌شود. <Link to="/methodology">روش محاسبه</Link>
          </p>
        </>
      )}
    </div>
  );
}
