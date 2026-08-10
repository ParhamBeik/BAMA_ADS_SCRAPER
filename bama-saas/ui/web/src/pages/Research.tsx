/**
 * Research — the premium workspace.
 *
 * The headline panel is time-on-market by price position, because it is the one
 * chart that answers the question both sides of a transaction actually have:
 * what does asking above the going rate cost you in time. It is stated as an
 * association throughout; an overpriced car and a slow car may share a cause
 * rather than one producing the other.
 *
 * The liquidity panel deliberately shows the naive average next to the censored
 * estimate. The gap between them is not a curiosity — it is the size of the
 * error every simpler version of this product ships with.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Envelope, Paginated } from "../api/client";
import { Chart } from "../Chart";
import { useFilters } from "../filters";
import { Async, Card, Provenance as Prov, Table } from "../ui";

/** Shape of /api/markets/ — one row per model that actually has listings. */
interface ModelRow {
  model_id: number;
  model_name: string;
  brand_name: string;
  ad_count: number;
}

interface Survival extends Envelope {
  n: number;
  delisted: number;
  censored: number;
  median_days: number | null;
  naive_mean_days_finished_only: number | null;
  curve: { day: number; still_listed: number; at_risk: number }[];
  still_listed_at_30d?: number;
}

interface PricePosition extends Envelope {
  cohort_median_price: number;
  bands: {
    price_band: string;
    n: number;
    median_days: number | null;
    still_listed_at_30d: number;
  }[];
}

interface Negotiation extends Envelope {
  listings: number;
  share_that_cut: number;
  median_cut_pct: number | null;
  p90_cut_pct?: number;
  median_days_to_first_cut: number | null;
}

interface Depreciation extends Envelope {
  reference_year: number;
  span_years: number;
  retained_over_span_pct: number;
  avg_annual_decline_pct: number | null;
  points: { year_jalali: number; n: number; median_price: number; pct_of_newest: number }[];
}

export function Research() {
  const filters = useFilters();
  const modelId = filters.getInt("model");

  const models = useQuery({
    queryKey: ["markets"],
    queryFn: ({ signal }) =>
      api.get<Paginated<ModelRow> | ModelRow[]>("/api/markets/", signal),
  });
  const modelList: ModelRow[] = Array.isArray(models.data)
    ? models.data
    : (models.data?.results ?? []);

  const survival = useQuery({
    queryKey: ["survival", modelId],
    enabled: Boolean(modelId),
    queryFn: ({ signal }) => api.get<Survival>(`/api/research/liquidity/${modelId}/`, signal),
  });
  const position = useQuery({
    queryKey: ["position", modelId],
    enabled: Boolean(modelId),
    queryFn: ({ signal }) =>
      api.get<PricePosition>(`/api/research/price-position/${modelId}/`, signal),
  });
  const negotiation = useQuery({
    queryKey: ["negotiation", modelId],
    enabled: Boolean(modelId),
    queryFn: ({ signal }) =>
      api.get<Negotiation>(`/api/research/negotiation/${modelId}/`, signal),
  });
  const depreciation = useQuery({
    queryKey: ["depreciation", modelId],
    enabled: Boolean(modelId),
    queryFn: ({ signal }) =>
      api.get<Depreciation>(`/api/research/depreciation/${modelId}/`, signal),
  });

  if (!modelId) {
    return (
      <ModelPicker models={modelList} onPick={(id) => filters.set({ model: id })} />
    );
  }

  return (
    <>
      <div className="filters">
        <select
          value={modelId}
          onChange={(e) => filters.set({ model: e.target.value })}
          aria-label="Model"
        >
          {modelList.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.brand_name} {m.model_name} ({m.ad_count})
            </option>
          ))}
        </select>
        <button onClick={() => filters.set({ model: null })}>Change model</button>
      </div>

      <Card title="Time on market by price position">
        <p className="stat-sub" style={{ marginTop: 0 }}>
          Each band is priced relative to this cohort&rsquo;s own median, so the
          comparison means the same thing for a hatchback and an SUV. Association,
          not causation.
        </p>
        <Async query={position}>
          {(data) => (
            <>
              <Chart
                x={data.bands.map((b) => b.price_band)}
                series={[
                  {
                    name: "Still listed after 30 days",
                    type: "bar",
                    data: data.bands.map((b) => Math.round(b.still_listed_at_30d * 1000) / 10),
                  },
                ]}
                yFormatter={(v) => `${v}%`}
                height={240}
              />
              <Table head={["Price band", "Listings", "Still listed at 30d"]}>
                {data.bands.map((b) => (
                  <tr key={b.price_band}>
                    <td>{b.price_band}</td>
                    <td className="num">{b.n}</td>
                    <td className="num">{(b.still_listed_at_30d * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </Table>
              <Prov envelope={data} />
            </>
          )}
        </Async>
      </Card>

      <div style={{ height: 14 }} />

      <div className="grid cols-2">
        <Card title="How long listings last">
          <Async query={survival}>
            {(data) => (
              <>
                <Chart
                  x={data.curve.map((p) => p.day)}
                  series={[
                    {
                      name: "Still listed",
                      data: data.curve.map((p) => Math.round(p.still_listed * 1000) / 10),
                      area: true,
                    },
                  ]}
                  yFormatter={(v) => `${v}%`}
                  height={220}
                />
                <div className="grid cols-2" style={{ marginTop: 10 }}>
                  <div>
                    <div className="card-title">Median (censored)</div>
                    <div className="stat">
                      {data.median_days != null ? `${data.median_days.toFixed(0)}d` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="card-title">Naive average</div>
                    <div className="stat warn">
                      {data.naive_mean_days_finished_only != null
                        ? `${data.naive_mean_days_finished_only.toFixed(0)}d`
                        : "—"}
                    </div>
                    <div className="stat-sub">
                      counts only the {data.delisted} that finished, ignoring{" "}
                      {data.censored} still listed
                    </div>
                  </div>
                </div>
                <Prov envelope={data} />
              </>
            )}
          </Async>
        </Card>

        <Card title="Negotiation room">
          <Async query={negotiation}>
            {(data) => (
              <>
                <div className="grid cols-2">
                  <div>
                    <div className="card-title">Sellers who cut</div>
                    <div className="stat">{(data.share_that_cut * 100).toFixed(1)}%</div>
                    <div className="stat-sub">of {data.listings} listings</div>
                  </div>
                  <div>
                    <div className="card-title">Typical cut</div>
                    <div className="stat">
                      {data.median_cut_pct != null ? `${data.median_cut_pct}%` : "—"}
                    </div>
                    <div className="stat-sub">
                      {data.median_days_to_first_cut != null
                        ? `after ~${data.median_days_to_first_cut} days`
                        : "no timing data"}
                    </div>
                  </div>
                </div>
                <Prov envelope={data} />
              </>
            )}
          </Async>
        </Card>
      </div>

      <div style={{ height: 14 }} />

      <Card title="Value by model year">
        <Async query={depreciation}>
          {(data) => (
            <>
              <Chart
                x={data.points.map((p) => p.year_jalali)}
                series={[
                  {
                    name: "% of newest year",
                    data: data.points.map((p) => p.pct_of_newest),
                    area: true,
                  },
                ]}
                yFormatter={(v) => `${v}%`}
                height={220}
              />
              <p className="stat-sub">
                Retained {data.retained_over_span_pct}% across {data.span_years} years
                {data.avg_annual_decline_pct != null &&
                  ` (~${data.avg_annual_decline_pct}% a year)`}
                . Measured against {data.reference_year}, the newest year with enough
                listings — not against an original sale price, which is never observed.
              </p>
              <Prov envelope={data} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}

function ModelPicker({
  models,
  onPick,
}: {
  models: ModelRow[];
  onPick: (id: number) => void;
}) {
  return (
    <Card title="Pick a model to research">
      {models.length === 0 ? (
        <div className="state">No models available.</div>
      ) : (
        <div className="filters">
          <select defaultValue="" onChange={(e) => e.target.value && onPick(Number(e.target.value))}>
            <option value="">Choose…</option>
            {models.map((m) => (
              <option key={m.model_id} value={m.model_id}>
                {m.brand_name} {m.model_name} ({m.ad_count} listings)
              </option>
            ))}
          </select>
        </div>
      )}
    </Card>
  );
}
