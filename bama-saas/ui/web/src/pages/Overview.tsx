/**
 * Market Overview — headline counts plus the composition-controlled index.
 *
 * The index, not the raw median, is the number that answers "did prices move".
 * A request of 30 days is clamped to whatever history actually exists; the
 * window on the card is that real span, not the number we asked for.
 */
import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { Envelope } from "../api";
import { Async, Card, Fa, Provenance, Stat, Table, pct } from "../ui";

const Chart = lazy(() => import("../Chart").then((m) => ({ default: m.Chart })));

interface OverviewData extends Envelope {
  active_listings: number;
  priced_listings: number;
  brands: number;
  models: number;
  top_brands: { brand__name_fa: string; n: number }[];
}

interface IndexPoint {
  date: string;
  index_value: number | null;
  return_pct: number | null;
  cohort_count: number;
  ad_count: number;
}

interface MarketIndex extends Partial<Envelope> {
  scope: string;
  base_value: number;
  latest_index: number | null;
  change_pct: number | null;
  window: {
    requested_days: number;
    days: number;
    clamped: boolean;
    first_date: string | null;
    last_date: string | null;
  };
  series: IndexPoint[];
}

function windowLabel(w: MarketIndex["window"]): string {
  const span =
    w.first_date && w.last_date
      ? `${w.first_date} → ${w.last_date}`
      : "No history recorded yet";
  const asked = `${w.days}d of ${w.requested_days}d requested`;
  return w.clamped
    ? `${span} · ${asked} (history is shorter than the requested window)`
    : `${span} · ${asked}`;
}

export function Overview() {
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.get<OverviewData>("/api/analytics/overview/", signal),
  });

  const index = useQuery({
    queryKey: ["market-index", 30],
    queryFn: ({ signal }) =>
      api.get<MarketIndex>("/api/analytics/market-index/?days=30", signal),
  });

  return (
    <div className="stack" dir="rtl">
      <Async query={overview}>
        {(data) => (
          <>
            {/* Three tiles, not four. The fourth repeated `priced_listings`,
                which is already the subtitle of the first. */}
            <div className="grid cols-3">
              <Stat
                label="Active listings"
                value={data.active_listings.toLocaleString("en-US")}
                sub={`${data.priced_listings.toLocaleString("en-US")} priced`}
              />
              <Stat label="Brands" value={data.brands.toLocaleString("en-US")} />
              <Stat label="Models" value={data.models.toLocaleString("en-US")} />
            </div>
            <Card title="Top brands">
              {/* A bar next to each count. The share is the question being asked
                  of this table, and a column of numbers makes the reader do the
                  division. */}
              <Table head={["Brand", "Listings", "Share"]}>
                {data.top_brands.map((b) => (
                  <tr key={b.brand__name_fa}>
                    <td>
                      <Fa>{b.brand__name_fa}</Fa>
                    </td>
                    <td className="num">{b.n.toLocaleString("en-US")}</td>
                    <td style={{ width: "45%" }}>
                      <span
                        className="bar"
                        style={{
                          width: `${(b.n / data.top_brands[0].n) * 100}%`,
                        }}
                      />
                    </td>
                  </tr>
                ))}
              </Table>
              <Provenance envelope={data} />
            </Card>
          </>
        )}
      </Async>

      <Card title="Composition-controlled price index">
        <p className="stat-sub" style={{ marginTop: 0 }}>
          Each cohort is only compared with itself, so a change in listing
          mix doesn't look like a price move.
        </p>
        <Async query={index} empty="No index history yet." shape="chart">
          {(data) => {
            const points = data.series ?? [];
            const last = points[points.length - 1];
            return (
              <>
                <p className="stat-sub">Actual window: {windowLabel(data.window)}</p>
                {/* The index's own sample size. The Provenance strip below
                    reports the *sweep's* coverage (~33k ads), which is a much
                    larger and unrelated number — sitting next to the index it
                    read as if the index were built on all of it. */}
                {last && (
                  <p className="stat-sub">
                    Built from {last.cohort_count.toLocaleString("en-US")} cohorts
                    and {last.ad_count.toLocaleString("en-US")} listings on the
                    latest day
                  </p>
                )}
                <div className="grid cols-2" style={{ marginBottom: 10 }}>
                  <Stat
                    label="Index"
                    value={
                      data.latest_index != null ? data.latest_index.toFixed(1) : "—"
                    }
                    sub={`Base ${data.base_value}`}
                  />
                  <Stat
                    label="Window change"
                    value={pct(data.change_pct)}
                    tone={
                      data.change_pct == null
                        ? undefined
                        : data.change_pct >= 0
                          ? "up"
                          : "down"
                    }
                  />
                </div>
                {points.length ? (
                  <Suspense fallback={<p className="muted">…</p>}>
                    <Chart
                      x={points.map((p) => p.date)}
                      series={[
                        {
                          name: "Index",
                          data: points.map((p) => p.index_value),
                          area: true,
                        },
                      ]}
                      yFormatter={(v) => v.toFixed(1)}
                    />
                  </Suspense>
                ) : (
                  <div className="state">No index history yet.</div>
                )}
                <Provenance envelope={data} />
              </>
            );
          }}
        </Async>
      </Card>
    </div>
  );
}
