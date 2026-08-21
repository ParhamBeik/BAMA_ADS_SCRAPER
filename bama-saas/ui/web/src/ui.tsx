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
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, Heart } from "lucide-react";
import { api, type Envelope, type Paginated } from "./api";

/**
 * What a cohort flag means, in the language the rest of the page is in.
 *
 * Shared because Explorer and ListingDetail both render the same flags, and the
 * detail page used to print the raw `price_outlier_low` string.
 */
export const FLAG_LABEL: Record<string, string> = {
  price_outlier_low: "Far below peer group — check why",
  price_outlier_high: "Far above peer group",
};

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
        <div className="thumb-fallback">No photo</div>
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
        <header
          style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}
        >
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

export function Provenance({ envelope }: { envelope?: Partial<Envelope> }) {
  if (!envelope?.coverage) return null;
  const { coverage, as_of, methodology_version } = envelope;
  return (
    <div className="provenance">
      {/* The two facts that change how the numbers above should be read, before
          the facts about how they were computed. During the 2026-08-16 block the
          catalog was frozen for six hours and this strip still only reported a
          cheerful listing count. */}
      {coverage.source_blocked && (
        <span className="badge warn">
          <AlertTriangle size={11} /> Bama isn't responding right now — these
          numbers aren't refreshing
        </span>
      )}
      {coverage.removal_detection_paused && (
        <span className="badge warn">
          <AlertTriangle size={11} /> Removal detection is paused — a sold
          listing may still show as active
        </span>
      )}
      <Database size={13} />
      {coverage.complete_sweep ? (
        <span>
          {coverage.ads_covered?.toLocaleString("en-US")} listings, last swept{" "}
          {coverage.age_hours}h ago
        </span>
      ) : (
        <span className="warn">
          No complete sweep recorded — these numbers may cover only part of
          the market
        </span>
      )}
      {coverage.stale && (
        <span className="badge warn">
          <AlertTriangle size={11} /> Stale
        </span>
      )}
      {as_of && <span>· as of {new Date(as_of).toLocaleString("en-US")}</span>}
      {methodology_version != null && <span>· methodology v{methodology_version}</span>}
    </div>
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
  const label = { high: "High confidence", medium: "Medium confidence", low: "Low confidence" }[
    tier as "high" | "medium" | "low"
  ];
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
    return (
      <div className="state error">
        {query.error instanceof Error ? query.error.message : "Something went wrong."}
      </div>
    );
  }
  const data = query.data as (T & { available?: boolean; reason?: string }) | undefined;
  if (!data) return <div className="state">{empty ?? "Nothing to show."}</div>;

  if (data.available === false) {
    return (
      <div className="state">
        <AlertTriangle size={16} />{" "}
        <strong>Not enough clean data yet for this calculation.</strong>
        <div style={{ marginTop: 4 }}>{humanReason(data.reason)}</div>
      </div>
    );
  }
  return <>{children(data)}</>;
}

function humanReason(reason?: string): string {
  switch (reason) {
    case "insufficient_episodes":
      return "Too few completed listings in this cohort to estimate time-to-sell.";
    case "insufficient_clean_history":
      // Not an error and not an empty cohort: removal dates recorded before the
      // crawl was reliable measured the sweep schedule rather than the market,
      // so they are excluded until enough trustworthy history accumulates.
      return "Not enough reliable history yet — time-on-market only counts listings seen after the crawler fix.";
    case "insufficient_peers":
      return "Too few peer listings to price this car.";
    case "insufficient_years":
      return "Not enough model years with data to plot a depreciation curve.";
    case "insufficient_listings":
      return "Too few listings in this cohort.";
    case "unknown_or_unverified_ad":
      return "This listing is unknown or hasn't passed data verification.";
    case "no_price_baseline":
      return "No usable price baseline exists for this cohort.";
    default:
      return reason ?? "This cohort is too small to report on.";
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
  label = "listings",
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
        Prev
      </button>
      <span className="stat-sub pager-mid">
        Page
        <input
          className="pager-input"
          value={draft}
          inputMode="numeric"
          aria-label="Page number"
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === "Enter" && commit()}
        />
        of {lastPage.toLocaleString("en-US")} · {total.toLocaleString("en-US")} {label}
      </span>
      <button disabled={page >= lastPage} onClick={() => onChange(page + 1)}>
        Next
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
      role="img"
      aria-label="Bama Market"
    >
      <path
        d="M6 23.5 12.5 10l4.25 8 3.5-5.5L26 23.5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="3"
      />
      <path
        d="M6 26h20"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="3"
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
      onClick={() => toggle.mutate()}
      disabled={toggle.isPending}
      aria-pressed={saved}
    >
      <Heart size={14} /> {saved ? "Saved" : "Save"}
    </button>
  );
}
