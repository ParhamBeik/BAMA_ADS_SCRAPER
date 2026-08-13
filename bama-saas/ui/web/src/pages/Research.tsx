/**
 * Research — the two cohort analytics that survive their own audit.
 *
 * Time-to-sell is a Kaplan-Meier survival curve, so cars still listed count as
 * censored rather than being dropped. The naive average sits next to the censored
 * median deliberately: the gap between them is the size of the error every
 * simpler version of this number carries.
 *
 * Value retention is an order statistic across model years, measured against the
 * newest year that has enough listings — never against an original sale price,
 * which this data set never observes. It is not the price-vs-mileage regression
 * that used to sit here; that fit explained 18% of variance and is gone.
 */
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { Envelope, Paginated } from "../api/client";
import { Chart } from "../Chart";
import { Async, Card, Provenance, Table } from "../ui";

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

interface Retention extends Envelope {
  reference_year: number;
  span_years: number;
  retained_over_span_pct: number;
  avg_annual_decline_pct: number | null;
  points: { year_jalali: number; n: number; median_price: number; pct_of_newest: number }[];
}

export function Research() {
  const navigate = useNavigate();
  const { modelId } = useParams();

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
    queryFn: ({ signal }) =>
      api.get<Survival>(`/api/research/liquidity/${modelId}/`, signal),
  });
  const retention = useQuery({
    queryKey: ["retention", modelId],
    enabled: Boolean(modelId),
    queryFn: ({ signal }) =>
      api.get<Retention>(`/api/research/depreciation/${modelId}/`, signal),
  });

  if (!modelId) {
    return (
      <Card title="Pick a model to research">
        {modelList.length === 0 ? (
          <div className="state">No models available.</div>
        ) : (
          <div className="filters">
            <select
              defaultValue=""
              onChange={(e) => e.target.value && navigate(`/research/${e.target.value}`)}
            >
              <option value="">Choose…</option>
              {modelList.map((m) => (
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

  return (
    <>
      <div className="filters">
        <select
          value={modelId}
          onChange={(e) => navigate(`/research/${e.target.value}`)}
          aria-label="Model"
        >
          {modelList.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.brand_name} {m.model_name} ({m.ad_count})
            </option>
          ))}
        </select>
        <button onClick={() => navigate("/research")}>Change model</button>
      </div>

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
                height={240}
              />
              <div className="grid cols-2" style={{ marginTop: 10 }}>
                <div>
                  <div className="card-title">Median (censored)</div>
                  <div className="stat">
                    {data.median_days != null ? `${data.median_days.toFixed(0)}d` : "—"}
                  </div>
                  <div className="stat-sub">across {data.n} listings</div>
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
              <Provenance envelope={data} />
            </>
          )}
        </Async>
      </Card>

      <div style={{ height: 14 }} />

      <Card title="Value by model year">
        <Async query={retention}>
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
              <Table head={["Model year", "Listings", "Median price", "% of newest"]}>
                {data.points.map((p) => (
                  <tr key={p.year_jalali}>
                    <td>{p.year_jalali}</td>
                    <td className="num">{p.n}</td>
                    <td className="num">{p.median_price.toLocaleString("en-US")}</td>
                    <td className="num">{p.pct_of_newest}%</td>
                  </tr>
                ))}
              </Table>
              <p className="stat-sub">
                Retained {data.retained_over_span_pct}% across {data.span_years} years
                {data.avg_annual_decline_pct != null &&
                  ` (~${data.avg_annual_decline_pct}% a year)`}
                . Measured against {data.reference_year}, the newest year with enough
                listings — not against an original sale price, which is never observed.
              </p>
              <Provenance envelope={data} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
