import { useState } from "react";
import { useMlEnabled } from "../auth";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Async, BamaLink, FLAG_LABEL, Fa, ListingActions, PriceBar, PriceVerdict, Provenance, RangeNotes, HowWeKnow } from "../ui";
import { bothDates, fa, faDate, modelYear, num, pct, toman } from "../format";
import type { Basis, Distribution, Verdict } from "../ui";
import { verdictNote, type Deal } from "../components/DealCard";
import { FollowButton } from "../components/FollowButton";
import { qs } from "../filters";

type Ad = {
  code: string;
  title: string;
  brand_name: string;
  model_id: number | null;
  model_name: string;
  variant_id: number | null;
  variant_name: string;
  year_jalali: number | null;
  year_calendar?: string;
  mileage: number | null;
  current_price: number | null;
  first_seen_at?: string | null;
  publish_at?: string | null;
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
  /** Codes of the peers, sent only for a thin group so it can be checked. */
  peers?: string[];
  as_of?: string;
  methodology_version?: number;
  coverage?: Record<string, unknown>;
};

/**
 * One large photo and a strip of the rest.
 *
 * It was a three-column grid of equal tiles, so the most common case — a
 * listing with one photo — drew that photo at a third of the card's width
 * beside two empty cells, and on a slow image proxy the card read as blank.
 */
function Gallery({ title, urls }: { title: string; urls: string[] }) {
  const [active, setActive] = useState(0);
  if (!urls.length) {
    return (
      <div className="gallery card">
        <div className="gallery-hero"><div className="thumb-fallback">بدون تصویر</div></div>
      </div>
    );
  }
  const current = urls[Math.min(active, urls.length - 1)];
  return (
    <div className="gallery card">
      <div className="gallery-hero">
        <img
          key={current}
          src={current}
          alt={`${title} — تصویر ${active + 1} از ${urls.length}`}
          onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.3"; }}
        />
      </div>
      {urls.length > 1 && (
        <div className="gallery-strip" role="list">
          {urls.map((src, index) => (
            // The listitem wraps the button: role="listitem" on the button
            // itself replaced its button role for screen readers.
            <div key={src} role="listitem">
              <button
                type="button"
                aria-label={`تصویر ${index + 1}`}
                aria-current={index === active ? "true" : undefined}
                onClick={() => setActive(index)}
              >
                <img src={src} alt="" loading="lazy" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Spec({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

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
        {lastSeen && <> آخرین بار {faDate(lastSeen)} دیده شد.</>}
      </p>
    );
  }

  const guess: Record<string, string> = {
    // Plain facts: the prefix below already says how sure the guess is, and
    // «به احتمال زیاد: احتمالاً فروخته شده» hedged twice.
    likely_sold: "فروخته شده",
    likely_expired: "اعتبار آگهی تمام شده",
    reposted: "با آگهی تازه‌ای دوباره ثبت شده",
    unknown: "دلیلش را نمی‌دانیم",
  };
  return (
    <p className="badge warn">
      دیگر در باما فهرست نشده است
      {lastSeen && <> — آخرین بار {faDate(lastSeen)} دیده شد</>}
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
  const mlEnabled = useMlEnabled();
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
              <Link to="/cars">خودروها</Link> / <Fa>{data.title}</Fa>
            </div>
            <div className="detail-layout">
              <Gallery
                // Keyed so moving to another listing opens on its first photo.
                key={code}
                title={data.title}
                urls={data.image_urls?.length ? data.image_urls : data.image_url ? [data.image_url] : []}
              />
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
                  {/* From one ad to the question behind it: what this car
                      costs in general, and news when that changes. */}
                  {data.model_id != null && (
                    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                      <Link
                        className="btn"
                        to={`/price${qs({
                          model: data.model_id,
                          variant: data.variant_id ?? undefined,
                          year: data.year_jalali ?? undefined,
                        })}`}
                      >
                        قیمت این خودرو در بازار
                      </Link>
                      <FollowButton scope={{
                        model: String(data.model_id),
                        variant: data.variant_id != null ? String(data.variant_id) : undefined,
                        year: data.year_jalali != null ? String(data.year_jalali) : undefined,
                      }} />
                    </div>
                  )}
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
                  {/* Label above value, two per row: a stack of "key: value"
                      sentences made the reader parse nine lines to find the
                      one fact they came for. */}
                  <dl className="spec-grid">
                    <Spec label="سال ساخت">{modelYear(data.year_jalali, data.year_calendar)}</Spec>
                    {/* Bama's date resets whenever the seller bumps the ad,
                        so the first sighting is the honest "listed on". */}
                    <Spec label="اولین بار دیده‌شده">{bothDates(data.first_seen_at)}</Spec>
                    <Spec label="آخرین به‌روزرسانی در باما">{bothDates(data.publish_at)}</Spec>
                    {/* The unit is not decoration: "262,000" alone is a
                        number the reader has to guess the meaning of. */}
                    <Spec label="کارکرد">
                      {data.mileage != null ? `${num(data.mileage)} کیلومتر` : "—"}
                      {data.mileage_implausible && (
                        <span className="badge warn" style={{ marginInlineStart: 6 }}>
                          باورپذیر نیست — در محاسبه قیمت نادیده گرفته شده
                        </span>
                      )}
                    </Spec>
                    <Spec label="وضعیت بدنه"><Fa>{data.body_status || "—"}</Fa></Spec>
                    <Spec label="گیربکس">{data.transmission || "—"}</Spec>
                    <Spec label="نوع بدنه">{data.body_type || "—"}</Spec>
                    <Spec label="سوخت">{data.fuel || "—"}</Spec>
                    <Spec label="شهر">
                      <Fa>{data.city_name || "—"}</Fa>{data.district ? ` / ${data.district}` : ""}
                    </Spec>
                    <Spec label="فروشنده">
                      {data.seller_type === "dealer"
                        ? <>نمایشگاه <Fa>{data.dealer_name || ""}</Fa></>
                        : data.seller_type === "private" ? "شخصی" : "—"}
                      {data.seller_authenticated && (
                        <span className="badge" style={{ marginInlineStart: 6 }}>احراز شده</span>
                      )}
                    </Spec>
                  </dl>
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

      {/* Held back until the listing itself has rendered, and that ordering is
          the whole point. These three panels resolve independently of `ad`, so
          they used to paint their own skeletons at the very top of an otherwise
          empty page — and then the gallery, the title and the description
          arrived *above* them and shoved them down by about a thousand pixels.
          That single reflow was a 0.47 layout shift on a phone. Rendering them
          only once there is something above them to sit under costs nothing:
          the queries are already in flight either way, and a price verdict with
          no car above it was not readable anyway. It also stops a bad listing
          code printing "این مورد پیدا نشد." four times over. */}
      {ad.isSuccess && (
        <>
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
              {/* Where this car sits among its peers: the bar's legend already
                  carries this ad, the median and the middle half, so they are
                  not repeated in a table. The rest is behind the tap. */}
              <PriceBar distribution={fp.distribution} asking={fp.asking} />
              {fp.cohort_stale && <span className="badge warn">مشابه‌ها قدیمی</span>}
              <RangeNotes distribution={fp.distribution}>
                {fp.position_pct != null && (
                  <p>گران‌تر از {pct(fp.position_pct, 0)} آگهی‌های مشابه.</p>
                )}
                {fp.fair_value != null && (
                  <p>قیمت منصفانه با در نظر گرفتن کارکرد: {toman(fp.fair_value)}.</p>
                )}
                {fp.distribution?.mode?.value != null && (
                  <p>
                    پرتکرارترین قیمت: {toman(fp.distribution.mode.value)} ({fa(fp.distribution.mode.count)} آگهی).
                  </p>
                )}
                {fp.cohort_stale && (
                  <p>آگهی‌های مشابه این خودرو مدتی است دوباره دیده نشده‌اند.</p>
                )}
                {fp.peers && fp.peers.length > 0 && (
                  <p>
                    آگهی‌های هم‌گروه:{" "}
                    {fp.peers.map((c, i) => (
                      <span key={c}>{i > 0 && " · "}<Link to={`/listing/${c}`}>{c}</Link></span>
                    ))}
                  </p>
                )}
              </RangeNotes>
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
      <DealVerdict code={code} fairVerdict={fair.data?.verdict} />

      {/* The learned estimate, deliberately *below* the statistical one and
          never in place of it. Two independent accounts of the same car, and
          the reader gets to see where they disagree. */}
      {mlEnabled && <ModelEstimate code={code} />}

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
                      <td>{faDate(r.observed_at)}</td>
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
        </>
      )}
    </div>
  );
}

interface DealRow extends Pick<Deal, "deal_status" | "deal_reasons" | "cheaper_than_pct"
  | "fresh_peers" | "price_age_days"> {
  discount_pct: number | null;
  peer_median: number | null;
  peer_count: number | null;
  confidence: string | null;
  days_listed: number | null;
  liquidity?: { left_pct: number; n: number; window_days: number } | null;
  components?: { cohort_stale?: boolean };
}

function DealVerdict({ code, fairVerdict }: { code: string; fairVerdict?: Verdict }) {
  const verdict = useQuery({
    queryKey: ["deal-score", code],
    enabled: !!code,
    // Not on the board is the common case and not an error, so a 404 must not
    // be retried and must not surface as a failed panel.
    retry: false,
    queryFn: ({ signal }) =>
      api.get<DealRow>(`/api/analytics/deal-scores/${code}/`, signal),
  });

  const d = verdict.data;
  // A car the board passed over has nothing to add to the valuation above;
  // a card reading "not on the board" only looked like a second opinion.
  if (!d || (d.deal_status !== "deal" && d.deal_status !== "check")) return null;
  // The two cards ask different peers: this one only cars priced in the last
  // month, the valuation every live listing. Said, not left to look like a
  // contradiction.
  const differs = d.deal_status === "deal" && (fairVerdict === "above" || fairVerdict === "inline");
  // `pricing` writes a row whatever the sign (a negative discount is a car
  // priced *above* its peers), and the board filters on the sign at query
  // time. This card used to assume every row it received was on the board, so
  // a car 4.6% over the median was captioned "priced below its peers".
  // The valuation card above already prints the gap and the peer median, so
  // this card says only what the deal board decided and why.
  const note = verdictNote(d);
  return (
    <div className="card">
      <h2>تابلوی معامله‌ها</h2>
      <p>
        {d.deal_status === "deal"
          ? <span className="badge ok">معامله‌ی خوب</span>
          : <span className="badge warn">نیازمند بررسی</span>}
        {differs && <span className="badge">در مقایسه با قیمت‌های تازه</span>}
      </p>
      {note && <p className="stat-sub">{note}</p>}
      {d.liquidity && (
        <p className="stat-sub">
          {Math.round(d.liquidity.left_pct)}% از این مدل ظرف {d.liquidity.window_days} روز برداشته می‌شوند
        </p>
      )}
      <HowWeKnow>
        <p>
          معامله‌ی خوب یعنی قیمتی که در ۱۴ روز اخیر ثبت شده، از بیشتر آگهی‌های هم‌گروهِ ماه
          اخیر ارزان‌تر است، کارکرد عادی دارد و در متن آگهی دلیلی برای ارزانی دیده نمی‌شود.
        </p>
        {differs && (
          <p>
            ارزیابی قیمتِ بالا همه‌ی آگهی‌های فعال مشابه را می‌سنجد، از جمله قیمت‌هایی که
            هفته‌ها پیش ثبت شده‌اند. این تابلو فقط قیمت‌های ماه اخیر را؛ در بازاری که
            قیمت‌ها بالا می‌رود، این دو می‌توانند متفاوت باشند.
          </p>
        )}
      </HowWeKnow>
      <Link className="btn" to="/cars?deals=1">همه معامله‌ها</Link>
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
      <HowWeKnow><p>
        این برآورد جای میانه‌ی آگهی‌های مشابه را نمی‌گیرد — کنارش می‌نشیند.
        تفاوتش این است که کارکرد، وضعیت بدنه، شهر و نوع فروشنده را هم می‌بیند،
        چیزهایی که کلید «مدل، تیپ، سال» درباره‌شان چیزی نمی‌داند.
      </p></HowWeKnow>

      <ul className="spec-list">
        <li>برآورد میانی: {toman(p.price_p50)}</li>
        <li>
          بازه‌ی محتمل: {toman(p.price_p10)} تا {toman(p.price_p90)}
        </li>
        {/* residual = (p50 − price) / p50, so positive means the asking price
            sits *under* the estimate. A bare "+8.8%" read as "8.8% too dear". */}
        {p.residual_pct != null && (
          <li>
            {p.residual_pct >= 0 ? "ارزان‌تر از برآورد: " : "گران‌تر از برآورد: "}
            <span className={p.residual_pct >= 0 ? "up" : "down"}>
              {pct(Math.abs(p.residual_pct))}
            </span>
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
              {/* Below a toman figure any car could have, the "starting
                  point" is not a price — production printed «1». Hidden until
                  `ml.inference` reports it in toman. */}
              {base?.base_price != null && base.base_price >= 1_000_000 && (
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
