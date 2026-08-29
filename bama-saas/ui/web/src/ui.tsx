/**
 * Shared presentation pieces.
 *
 * Two of these carry product decisions rather than styling:
 *
 * `Provenance` renders the envelope every research answer ships with. It is not
 * optional chrome — these numbers come from a crawl that can be incomplete, and a
 * survival curve computed across a coverage hole reads crawler downtime as cars
 * leaving the market. If the data is stale the user sees that next to the number.
 *
 * `Async` makes "we do not have enough clean data for this" a first-class result
 * rather than an error or, worse, an empty chart that looks like zero.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, ExternalLink, Heart, X } from "lucide-react";
import { ApiError, api, type Envelope, type Paginated } from "./api";

/**
 * What a cohort flag means, in the language the rest of the page is in.
 *
 * Shared because Explorer and ListingDetail both render the same flags, and the
 * detail page used to print the raw `price_outlier_low` string.
 */
export const FLAG_LABEL: Record<string, string> = {
  price_outlier_low: "خیلی پایین‌تر از آگهی‌های مشابه — دلیلش را بررسی کنید",
  price_outlier_high: "خیلی بالاتر از آگهی‌های مشابه",
};

/**
 * The link out to the real ad on bama.ir.
 *
 * `Ad.url` holds a site-relative path, so anything rendering it straight into
 * an href resolved against our own origin and dead-ended inside the SPA — the
 * Telegram alerts had a working link and the website never did. The backend now
 * serves `bama_url` absolute; this is the one place that renders it, so the
 * "opens elsewhere" affordance cannot go missing on one screen.
 */
export function BamaLink({
  href,
  className = "btn",
  children = "مشاهده در باما",
}: {
  href?: string;
  className?: string;
  children?: ReactNode;
}) {
  if (!href) return null;
  return (
    <a
      className={className}
      href={href}
      target="_blank"
      rel="noreferrer"
      // Stops the click from also selecting the card it sits on.
      onClick={(e) => e.stopPropagation()}
    >
      <ExternalLink size={13} aria-hidden /> {children}
    </a>
  );
}

/**
 * A listing photo, or a graceful gap where one should be.
 *
 * Bama's CDN 500s on a small fraction of its own images, and a card-first layout
 * turns that into a grid of broken-image icons. `onError` drops the element so
 * the thumb's own background shows instead. Shared by both card grids because
 * they hit the same CDN.
 */
export function Thumb({ src, children }: { src?: string; children?: ReactNode }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="thumb">
      {src && !failed ? (
        <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} />
      ) : (
        <div className="thumb-fallback">بدون تصویر</div>
      )}
      {children}
    </div>
  );
}

/** Persian source text inside English chrome. */
export function Fa({ children }: { children: ReactNode }) {
  return (
    <span className="fa" dir="auto">
      {children}
    </span>
  );
}

export function toman(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)}M`;
  return value.toLocaleString("en-US");
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

/**
 * A number set in Persian digits, for prose.
 *
 * `toman`, `pct` and `km` stay Latin on purpose — they sit in `tabular-nums`
 * columns beside Latin magnitude suffixes ("3.90B"). Sentences are the other
 * case, and mixing the two inside one card is what put «۷ روز» on a chip and
 * "30 روز" in the label directly beneath it.
 */
export function fa(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString("fa-IR", { useGrouping: true });
}

/**
 * "0.1 ساعت پیش" is not how anyone says four minutes.
 *
 * Hours below one are read in minutes and anything under a minute is "just
 * now"; the API reports a decimal hour count because that is the natural unit
 * for staleness, not for prose.
 */
export function since(hours: number | null | undefined): string {
  if (hours == null) return "—";
  const minutes = Math.round(hours * 60);
  if (minutes < 1) return "همین الان";
  if (minutes < 60) return `${fa(minutes)} دقیقه پیش`;
  if (hours < 24) return `${fa(Math.round(hours))} ساعت پیش`;
  return `${fa(Math.round(hours / 24))} روز پیش`;
}

/**
 * Odometer, at card density.
 *
 * Rounding to thousands rendered every new car as "0k km", which read as
 * missing data rather than as a zero-kilometre car — so the two stay distinct
 * strings ("0 km" vs "—").
 *
 * Western digits throughout, like `toman` and `pct`: numbers on this site sit
 * in `tabular-nums` columns and mix with Latin magnitude suffixes ("3.90B"),
 * so digits stay Latin rather than switching numeral systems mid-line.
 */
export function km(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value === 0) return "0 km";
  if (value < 1000) return `${value.toLocaleString("en-US")} km`;
  return `${Math.round(value / 1000).toLocaleString("en-US")}k km`;
}

export function Card({
  title,
  children,
  action,
}: {
  title?: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <section className="card">
      {(title || action) && (
        <header className="card-head">
          {title && <h3 className="card-title">{title}</h3>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "up" | "down" | "warn";
}) {
  return (
    <Card title={label}>
      <div className={`stat ${tone ?? ""}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </Card>
  );
}

/**
 * @param compact Drop the coverage half and keep only when-and-how.
 *
 * Coverage describes the crawl, not the panel, so every panel on a page
 * repeats the identical sentence — the home page printed the same warning
 * strip five times. One panel per page carries the full version and the rest
 * are compact, which keeps each panel's own vintage visible without turning a
 * warning into wallpaper.
 */
export function Provenance({
  envelope, compact = false,
}: { envelope?: Partial<Envelope>; compact?: boolean }) {
  if (!envelope?.coverage) return null;
  const { coverage, as_of, methodology_version } = envelope;
  if (compact) {
    return (
      <div className="provenance">
        {as_of && <span>تا {new Date(as_of).toLocaleString("fa-IR")}</span>}
        {methodology_version != null && <span>· روش محاسبه نسخه {methodology_version}</span>}
      </div>
    );
  }
  return (
    <div className="provenance">
      {/* The two facts that change how the numbers above should be read, before
          the facts about how they were computed. During the 2026-08-16 block the
          catalog was frozen for six hours and this strip still only reported a
          cheerful listing count. */}
      {coverage.source_blocked && (
        <span className="badge warn">
          <AlertTriangle size={11} /> باما در حال حاضر پاسخ نمی‌دهد — این اعداد
          به‌روز نمی‌شوند
        </span>
      )}
      {coverage.removal_detection_paused && (
        <span className="badge warn">
          <AlertTriangle size={11} /> تشخیص حذف آگهی متوقف است — ممکن است آگهی
          فروخته‌شده هنوز فعال نشان داده شود
        </span>
      )}
      <Database size={13} />
      {coverage.complete_sweep ? (
        <span>
          {coverage.ads_covered?.toLocaleString("en-US")} آگهی، آخرین بررسی{" "}
          {since(coverage.age_hours)}
        </span>
      ) : (
        <span className="warn">
          هیچ بررسی کاملی ثبت نشده — این اعداد ممکن است تنها بخشی از بازار را
          پوشش دهند
        </span>
      )}
      {coverage.stale && (
        <span className="badge warn">
          <AlertTriangle size={11} /> قدیمی
        </span>
      )}
      {as_of && <span>· تا {new Date(as_of).toLocaleString("fa-IR")}</span>}
      {methodology_version != null && <span>· روش محاسبه نسخه {methodology_version}</span>}
    </div>
  );
}

/** The real span an index answer covers, which is not always the one asked for. */
export interface IndexWindow {
  requested_days: number;
  days: number;
  clamped: boolean;
}

/** What an index series stands on, and whether that is enough to say so. */
export interface IndexSample {
  cohort_count: number;
  ad_count: number;
  thin: boolean;
  min_cohorts: number;
  low_coverage_days: number;
  max_gap_days: number;
}

/**
 * Everything about this series that changes how its number should be read.
 *
 * Not decoration and not a footnote: the front page's headline move was mostly
 * one step across a four-day crawler hole, and the same request for "one year"
 * and "90 days" returned the identical 44 points and the identical figure with
 * nothing on screen saying so. The Analyse page disclosed its short window and
 * this one did not — the same data at two levels of honesty.
 */
export function SeriesCaveats({
  window, sample,
}: { window: IndexWindow; sample?: IndexSample }) {
  const notes: string[] = [];
  if (window.clamped) {
    notes.push(
      `سابقه شاخص فقط ${fa(window.days)} روز است، نه ${fa(window.requested_days)} روز — ` +
      "بازه‌های بلندتر همین عدد را برمی‌گردانند.",
    );
  }
  if (sample?.low_coverage_days) {
    notes.push(
      `${fa(sample.low_coverage_days)} روز از این بازه کم‌پوشش بوده و در محاسبه تغییر ` +
      "قیمت شرکت داده نشده است.",
    );
  }
  if (sample && sample.max_gap_days > 1) {
    notes.push(
      `بزرگ‌ترین فاصله میان دو روز پیاپی این نمودار ${fa(sample.max_gap_days)} روز است؛ ` +
      "حرکت آن گام، حاصل چند روز است نه یک روز.",
    );
  }
  if (sample?.thin) {
    notes.push(
      `این شاخص روی ${fa(sample.cohort_count)} دسته ساخته شده — کمتر از ` +
      `${fa(sample.min_cohorts)} دسته‌ای که برای یک عدد قابل اتکا لازم است.`,
    );
  }
  if (!notes.length) return null;
  return (
    <p className="badge warn" style={{ display: "block", lineHeight: 1.7 }}>
      <AlertTriangle size={11} /> {notes.join(" ")}
    </p>
  );
}

/**
 * Sample size as three dots.
 *
 * The confidence tier is the difference between "12% off, forty peers" and
 * "40% off, three peers", and it was rendered as an English word in a Persian
 * table. Filled dots are read without reading.
 */
export function ConfidenceDots({ tier }: { tier?: string | null }) {
  const filled = tier === "high" ? 3 : tier === "medium" ? 2 : tier === "low" ? 1 : 0;
  if (!filled) return <span className="dots">—</span>;
  const label = {
    high: "اعتبار زیاد — بر پایه ۴۰ آگهی مشابه یا بیشتر",
    medium: "اعتبار متوسط — بر پایه ۱۵ تا ۳۹ آگهی مشابه",
    low: "اعتبار کم — بر پایه ۸ تا ۱۴ آگهی مشابه",
  }[tier as "high" | "medium" | "low"];
  return (
    <span className={`dots ${tier}`} title={label} aria-label={label}>
      {"●".repeat(filled)}
      {"○".repeat(3 - filled)}
    </span>
  );
}

/**
 * Loading, error, empty, unavailable and stale — all explicit.
 *
 * `unavailable` is the interesting one: the backend refuses to compute a number
 * from too little data and says why. Rendering that as an error would be wrong
 * (nothing failed) and rendering an empty chart would be worse (it reads as
 * zero).
 */
export function Async<T>({
  query,
  children,
  empty,
  shape = "block",
}: {
  query: { isLoading: boolean; error: unknown; data: T | undefined };
  children: (data: T) => ReactNode;
  empty?: string;
  /** Match the placeholder to what will land, so the page stops jumping. */
  shape?: "block" | "table" | "chart" | "cards";
}) {
  if (query.isLoading) {
    if (shape === "cards") {
      return (
        <div className="card-grid" aria-busy="true">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="skeleton" style={{ height: 210 }} />
          ))}
        </div>
      );
    }
    const height = shape === "table" ? 260 : shape === "chart" ? 240 : 120;
    return <div className="skeleton" style={{ height }} aria-busy="true" />;
  }
  if (query.error) {
    return <div className="state error">{humanError(query.error)}</div>;
  }
  const data = query.data as (T & { available?: boolean; reason?: string }) | undefined;
  if (!data) return <div className="state">{empty ?? "چیزی برای نمایش نیست."}</div>;

  if (data.available === false) {
    return (
      <div className="state">
        <AlertTriangle size={16} />{" "}
        <strong>هنوز داده کافی برای این محاسبه وجود ندارد.</strong>
        <div style={{ marginTop: 4 }}>{humanReason(data.reason)}</div>
      </div>
    );
  }
  return <>{children(data)}</>;
}

/**
 * A failed request, in the language the rest of the screen is in.
 *
 * DRF's `detail` is English written for an API client — "No Ad matches the
 * given query.", "A live fetch is already running." — and it was being rendered
 * straight into the page, twice on the listing screen. Status code first,
 * because that is the part with a reliable meaning; the server's own sentence
 * is only shown when there is nothing better to say, and never on a 5xx, where
 * it is a stack-trace fragment.
 */
export function humanError(error: unknown): string {
  const status = error instanceof ApiError ? error.status : 0;
  if (status === 404) return "این مورد پیدا نشد.";
  if (status === 400) return "درخواست نامعتبر بود.";
  if (status === 401 || status === 403) return "برای دیدن این بخش باید وارد شوید.";
  if (status === 409) return "این کار همین حالا در حال اجراست.";
  if (status === 429) return "درخواست‌ها بیش از حد مجاز بود — کمی بعد دوباره تلاش کنید.";
  if (status >= 500) return "خطایی در سرور رخ داد. کمی بعد دوباره تلاش کنید.";
  if (error instanceof ApiError && /^[؀-ۿ\s]/.test(error.detail)) {
    return error.detail;  // already Persian: the backend meant it for a reader
  }
  return "ارتباط با سرور برقرار نشد.";
}

function humanReason(reason?: string): string {
  switch (reason) {
    case "insufficient_episodes":
      return "آگهی‌های پایان‌یافته این دسته برای تخمین مدت فروش کم است.";
    case "insufficient_clean_history":
      // Not an error and not an empty cohort: removal dates recorded before the
      // crawl was reliable measured the sweep schedule rather than the market,
      // so they are excluded until enough trustworthy history accumulates.
      return "سابقه قابل اتکا هنوز کافی نیست — مدت ماندن در بازار تنها برای آگهی‌هایی شمرده می‌شود که پس از اصلاح خزنده دیده شده‌اند.";
    case "insufficient_peers":
      return "آگهی‌های مشابه برای قیمت‌گذاری این خودرو کم است.";
    case "insufficient_years":
      return "سال‌های ساخت دارای داده، برای رسم نمودار افت قیمت کافی نیست.";
    case "insufficient_listings":
      return "تعداد آگهی‌های این دسته کم است.";
    case "window_exceeds_clean_history":
      // The crawler's removal detection only became trustworthy on a known
      // date; a window reaching back past it would measure the sweep schedule
      // rather than the market.
      return "بازه انتخاب‌شده از سابقه قابل اتکای ما بلندتر است — بازه کوتاه‌تری را انتخاب کنید.";
    case "insufficient_index_history":
      // Not "no data": the series exists, it is just younger than the window
      // being asked about, and it fills in on its own each day.
      return "سابقه شاخص هنوز به اندازه بازه انتخاب‌شده نیست — بازه کوتاه‌تری را امتحان کنید.";
    case "unknown_or_unverified_ad":
      return "این آگهی ناشناخته است یا از بررسی صحت داده عبور نکرده است.";
    case "no_price_baseline":
      return "مبنای قیمتی قابل استفاده‌ای برای این دسته وجود ندارد.";
    default:
      return reason ?? "این دسته برای گزارش‌دهی کوچک است.";
  }
}

/**
 * Prev/next plus a typed page number.
 *
 * The deal board and the explorer both run into the hundreds of pages, and
 * prev/next alone made "page 40" a forty-click walk. Shared here rather than
 * duplicated because both screens define "last page" the same way
 * (`ceil(total / pageSize)`) and drifted the moment that math was ever written
 * twice.
 */
export function Pager({
  page,
  lastPage,
  total,
  label = "آگهی",
  onChange,
}: {
  page: number;
  lastPage: number;
  total: number;
  label?: string;
  onChange: (page: number) => void;
}) {
  const [draft, setDraft] = useState(String(page));
  useEffect(() => setDraft(String(page)), [page]);

  const commit = () => {
    const parsed = Math.round(Number(draft));
    const next = Number.isFinite(parsed) ? Math.min(lastPage, Math.max(1, parsed)) : page;
    setDraft(String(next));
    if (next !== page) onChange(next);
  };

  return (
    <div className="pager">
      <button disabled={page <= 1} onClick={() => onChange(page - 1)}>
        قبلی
      </button>
      <span className="stat-sub pager-mid">
        صفحه
        <input
          className="pager-input"
          value={draft}
          inputMode="numeric"
          aria-label="شماره صفحه"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && commit()}
        />
        از {lastPage.toLocaleString("en-US")} · {total.toLocaleString("en-US")} {label}
      </span>
      <button disabled={page >= lastPage} onClick={() => onChange(page + 1)}>
        بعدی
      </button>
    </div>
  );
}

export function Table({
  head,
  children,
}: {
  head: ReactNode[];
  children: ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className={i > 0 ? "num" : undefined} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/** The wordless logo, in the header and on the login card. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      className="brand-mark"
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="رادار باما — هوش بازار خودرو"
    >
      {/* Aerodynamic car silhouette roofline & nose */}
      <path
        d="M4.5 21C5 18.8 6.6 17.6 8.8 17.2L12 11.8C13 10.2 14.8 9.2 16.8 9.2H20.6C22.4 9.2 24 10.1 24.9 11.6L27.2 15.6C29.2 16.1 30.5 17.4 30.8 19.6"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {/* Rising price & analytics momentum sparkline */}
      <path
        d="M3.5 22.5L10 18L14.5 21.5L21.5 13L26.5 17"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.9"
      />
      {/* Pulse peak point */}
      <circle cx="21.5" cy="13" r="2" fill="currentColor" />
      {/* Sleek ground track */}
      <path
        d="M3 26H29"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        opacity="0.35"
      />
    </svg>
  );
}


interface Favorite {
  code: string;
}

/** Save / unsave one ad. Saving is the only write the product has left. */
export function ListingActions({ code }: { code: string }) {
  const client = useQueryClient();
  const favorites = useQuery({
    queryKey: ["favorites"],
    queryFn: ({ signal }) => api.get<Paginated<Favorite>>("/api/favorites/", signal),
  });
  const saved = Boolean(favorites.data?.results.some((f) => f.code === code));

  const toggle = useMutation({
    mutationFn: async () => {
      if (saved) await api.del(`/api/favorites/${code}/`);
      else await api.post("/api/favorites/", { code });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["favorites"] }),
  });

  return (
    <button
      className={saved ? "on" : ""}
      onClick={(e) => { e.stopPropagation(); toggle.mutate(); }}
      disabled={toggle.isPending}
      aria-pressed={saved}
    >
      <Heart size={14} /> {saved ? "ذخیره شد" : "ذخیره"}
    </button>
  );
}

/**
 * A right-side panel for a secondary answer about a chosen row.
 *
 * Exists because the Explore page gave half its width permanently to a
 * fair-price panel that was empty until something was clicked. Escape and a
 * backdrop click both close, and focus returns to whatever opened it — a modal
 * that traps the keyboard is worse than the layout it replaced.
 */
export function Sheet({
  title,
  onClose,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);

  useEffect(() => {
    opener.current = document.activeElement;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      (opener.current as HTMLElement | null)?.focus?.();
    };
  }, [onClose]);

  return (
    <>
      <div className="sheet-backdrop" onClick={onClose} />
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        ref={panel}
      >
        <div className="sheet-head">
          <h2>{title}</h2>
          <button className="sheet-close" onClick={onClose} aria-label="بستن">
            <X size={18} />
          </button>
        </div>
        {children}
      </div>
    </>
  );
}

export interface Distribution {
  min: number;
  p10: number;
  p25: number;
  median: number;
  p75: number;
  p90: number;
  max: number;
  count: number;
}

/**
 * Where one asking price sits among its peers.
 *
 * The components table answers "how was this number built"; this answers "is
 * this car cheap", which is the question people actually arrive with. The drawn
 * band is p10-p90, not min-max: one 5.8-trillion-toman typo listing would
 * otherwise squash every real car into the leftmost pixel.
 */
export function PriceBar({
  distribution,
  asking,
}: {
  distribution?: Distribution;
  asking?: number | null;
}) {
  if (!distribution?.count || asking == null) return null;
  const { p10, p25, median, p75, p90 } = distribution;
  const span = p90 - p10 || 1;
  // Clamped, so a listing outside the drawn band still renders on the bar
  // (pinned to its edge) rather than escaping the container.
  const at = (v: number) => `${Math.min(100, Math.max(0, ((v - p10) / span) * 100))}%`;
  const cheaper = asking < median;

  return (
    <div className="price-bar">
      <div
        className="price-bar-track"
        role="img"
        aria-label={
          `قیمت این خودرو ${toman(asking)} تومان است، در برابر میانه ` +
          `${toman(median)} تومان در ${distribution.count} آگهی مشابه`
        }
      >
        <div
          className="price-bar-iqr"
          style={{ insetInlineStart: at(p25), width: `calc(${at(p75)} - ${at(p25)})` }}
        />
        <div className="price-bar-median" style={{ insetInlineStart: at(median) }} />
        <div
          className={`price-bar-you${cheaper ? " good" : ""}`}
          style={{ insetInlineStart: at(asking) }}
        />
      </div>
      <div className="price-bar-scale">
        <span>{toman(p10)}</span>
        <span>{toman(p90)}</span>
      </div>
      <div className="price-bar-legend">
        <span className={cheaper ? "up" : ""}>
          این آگهی <b>{toman(asking)}</b>
        </span>
        <span className="muted">
          میانه <b>{toman(median)}</b>
        </span>
        <span className="muted">
          نیمه میانی <b>{toman(p25)}</b> تا <b>{toman(p75)}</b>
        </span>
      </div>
    </div>
  );
}
