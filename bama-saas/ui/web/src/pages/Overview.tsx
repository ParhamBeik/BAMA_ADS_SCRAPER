/**
 * Market Overview — the public workspace.
 *
 * Leads with the composition-controlled index rather than the raw median,
 * because the raw median mostly measures which cars happened to be listed. On
 * this dataset the two disagreed by 7 percentage points over one month while the
 * market barely moved, and the raw median was the one that looked dramatic.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Envelope } from "../api/client";
import { Chart } from "../Chart";
import { Async, Card, Fa, Provenance, Stat, Table } from "../ui";

interface Overview extends Envelope {
  active_listings: number;
  priced_listings: number;
  distinct_vehicles_identified: number;
  brands: number;
  models: number;
  top_brands: { brand__name_fa: string; n: number }[];
}

interface MarketIndex {
  scope: string;
  base_value: number;
  latest_index: number | null;
  change_pct: number | null;
  series: IndexPoint[];
}

interface IndexPoint {
  date: string;
  index_value: number | null;
  return_pct: number | null;
  cohort_count: number;
  ad_count: number;
}

export function Overview() {
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.get<Overview>("/api/analytics/overview/", signal),
  });

  const index = useQuery({
    queryKey: ["market-index"],
    queryFn: ({ signal }) =>
      api.get<MarketIndex>("/api/analytics/market-index/?days=90", signal),
  });

  const points: IndexPoint[] = index.data?.series ?? [];

  return (
    <>
      <Async query={overview}>
        {(data) => (
          <>
            <div className="grid cols-4">
              <Stat
                label="Active listings"
                value={data.active_listings.toLocaleString("en-US")}
                sub={`${data.priced_listings.toLocaleString("en-US")} with a price`}
              />
              <Stat
                label="Vehicles identified"
                value={data.distinct_vehicles_identified.toLocaleString("en-US")}
                sub="all time, matched by shared photos"
              />
              <Stat label="Brands" value={data.brands} />
              <Stat label="Models" value={data.models} />
            </div>
            <div style={{ height: 14 }} />
            <Card title="Largest brands by active listings">
              <Table head={["Brand", "Listings"]}>
                {data.top_brands.map((b) => (
                  <tr key={b.brand__name_fa}>
                    <td>
                      <Fa>{b.brand__name_fa}</Fa>
                    </td>
                    <td className="num">{b.n.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </Table>
              <Provenance envelope={data} />
            </Card>
          </>
        )}
      </Async>

      <div style={{ height: 14 }} />

      <Card title="Composition-controlled price index">
        <p className="stat-sub" style={{ marginTop: 0 }}>
          Compares each cohort only against itself, so a change in which cars are
          listed cannot masquerade as a change in price.
        </p>
        <Async query={index} empty="No index history yet.">
          {() =>
            points.length ? (
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
            ) : (
              <div className="state">No index history yet.</div>
            )
          }
        </Async>
      </Card>
    </>
  );
}
