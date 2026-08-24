import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Async, FLAG_LABEL, Fa, ListingActions, Provenance, toman } from "../ui";

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
  // Not yet in the generated schema (another agent is adding these to the API);
  // kept optional/local so this page renders fine against an old API response too.
  seller_type?: "dealer" | "private" | null;
  dealer_name?: string | null;
};

type FairPrice = {
  available?: boolean;
  reason?: string;
  fair_value?: number | null;
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
        We have lost sight of this listing — our last full sweep of Bama was
        incomplete, so we cannot tell whether it is still for sale.
        {lastSeen && <> Last seen {new Date(lastSeen).toLocaleDateString("en-US")}.</>}
      </p>
    );
  }

  const guess: Record<string, string> = {
    likely_sold: "probably sold",
    likely_expired: "the listing probably just expired",
    reposted: "relisted under a new ad",
    unknown: "we cannot tell why",
  };
  return (
    <p className="badge warn">
      No longer listed on Bama
      {lastSeen && <> — last seen {new Date(lastSeen).toLocaleDateString("en-US")}</>}
      {reason && guess[reason] && (
        <>
          {". "}
          {confidence === "low" ? "Best guess: " : "Most likely: "}
          {guess[reason]}
          {repostedFrom && reason === "reposted" && "."}
        </>
      )}
      {repostedFrom && (
        <>
          {" "}
          <Link to={`/listing/${repostedFrom}`}>See the earlier listing</Link>.
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
    <div className="stack" dir="rtl">
      <Async query={ad}>
        {(data) => (
          <>
            <div className="breadcrumb">
              <Link to="/">Home</Link> / <Link to="/explore">Explore</Link> / <Fa>{data.title}</Fa>
            </div>
            <div className="detail-layout">
              <div className="gallery card">
                {(data.image_urls?.length ? data.image_urls : data.primary_image_url ? [data.primary_image_url] : []).map((src) => (
                  <img key={src} src={src} alt="" loading="lazy" onError={(e) => { (e.target as HTMLImageElement).style.opacity = "0.3"; }} />
                ))}
                {!data.primary_image_url && <div className="thumb-fallback">No photo</div>}
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
                      This number is likely a down payment or installment, not
                      the full car price — that's why it's excluded from the
                      deal board.
                    </p>
                  )}
                  {data.condition_flagged && (
                    <p className="badge warn">
                      The listing description mentions the car's condition
                      (accident, free-zone plate, or similar) — read it before
                      comparing price.
                    </p>
                  )}
                  <ul className="spec-list">
                    <li>Year: {data.year_jalali ?? "—"}</li>
                    <li>Mileage: {data.mileage?.toLocaleString("en-US") ?? "—"}</li>
                    <li>Transmission: {data.transmission || "—"}</li>
                    <li>Body: {data.body_type || "—"}</li>
                    <li>Fuel: {data.fuel || "—"}</li>
                    <li>City: <Fa>{data.city_name || "—"}</Fa></li>
                    <li>Verified seller: {data.seller_authenticated == null ? "—" : data.seller_authenticated ? "Yes" : "No"}</li>
                    {data.seller_type && (
                      <li>
                        {data.seller_type === "dealer"
                          ? `Dealership: ${data.dealer_name || "—"}`
                          : "Private seller"}
                      </li>
                    )}
                  </ul>
                  {data.cohort_flags?.map((f) => (
                    <p key={f} className="badge warn">
                      {FLAG_LABEL[f] ?? f}
                    </p>
                  ))}
                  {data.url && (
                    <a className="btn" href={data.url} target="_blank" rel="noreferrer">View on Bama</a>
                  )}
                  <ListingActions code={data.code} />
                </div>
                <div className="card">
                  <h2>Description</h2>
                  <p><Fa>{data.description || "No description provided."}</Fa></p>
                </div>
              </div>
            </div>
          </>
        )}
      </Async>

      <div className="card">
        <h2>Price assessment</h2>
        <Async query={fair}>
          {(fp) => fp.available === false ? (
            <p className="muted">Not enough data: {fp.reason}</p>
          ) : (
            <>
              <p>Fair price: {fp.fair_value != null ? toman(fp.fair_value) : "—"}</p>
              {fp.as_of && <Provenance envelope={fp as never} />}
            </>
          )}
        </Async>
      </div>

      <div className="card">
        <h2>Price history</h2>
        <Async query={history}>
          {(rows) => {
            const list = Array.isArray(rows) ? rows : rows.results ?? [];
            if (!list.length) return <p className="muted">No price changes recorded.</p>;
            return (
              <table className="table">
                <thead><tr><th>Time</th><th>Price</th></tr></thead>
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
