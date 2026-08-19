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
import { Async, Card, Fa, Provenance, Table, toman } from "../ui";

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

function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: ModelRow[];
  value?: string;
  onChange: (id: string) => void;
}) {
  const brands = new Map<string, ModelRow[]>();
  for (const model of models) {
    brands.set(model.brand_name, [...(brands.get(model.brand_name) ?? []), model]);
  }

  return (
    <select
      value={value ?? ""}
      onChange={(e) => e.target.value && onChange(e.target.value)}
      aria-label="Car model"
    >
      {!value && <option value="">Select a model…</option>}
      {[...brands].map(([brand, rows]) => (
        <optgroup key={brand} label={brand}>
          {rows.map((model) => (
            <option key={model.model_id} value={model.model_id}>
              {model.model_name} ({model.ad_count.toLocaleString("en-US")} listings)
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

/**
 * The picker plus a gallery of the most-listed models.
 *
 * A dropdown with nothing selected is a dead end — the model with the deepest
 * listing history is also the one most likely to have a clean survival curve
 * and enough retention years to plot, so it goes straight in front of the user
 * instead of waiting to be found in a long `<select>`.
 */
function ModelPickerSection({
  models,
  navigate,
}: {
  models: ModelRow[];
  navigate: (path: string) => void;
}) {
  if (models.length === 0) {
    return <div className="state">No models available to research yet.</div>;
  }
  const featured = [...models].sort((a, b) => b.ad_count - a.ad_count).slice(0, 12);
  return (
    <div className="stack">
      <div className="filters">
        <ModelPicker models={models} onChange={(id) => navigate(`/research/${id}`)} />
      </div>
      <div className="chip-grid">
        {featured.map((m) => (
          <button
            key={m.model_id}
            className="model-chip"
            onClick={() => navigate(`/research/${m.model_id}`)}
          >
            <strong>
              <Fa>{m.model_name}</Fa>
            </strong>
            <span className="stat-sub">
              <Fa>{m.brand_name}</Fa> · {m.ad_count.toLocaleString("en-US")} listings
            </span>
          </button>
        ))}
      </div>
    </div>
  );
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
      <div className="stack" dir="rtl">
        <Card title="Model research">
          <Async query={models} empty="No models available to research yet.">
            {() => <ModelPickerSection models={modelList} navigate={navigate} />}
          </Async>
        </Card>
      </div>
    );
  }

  return (
    <div className="stack" dir="rtl">
      <div className="filters">
        <ModelPicker
          models={modelList}
          value={modelId}
          onChange={(id) => navigate(`/research/${id}`)}
        />
        <button onClick={() => navigate("/research")}>Change model</button>
      </div>

      <Card title="Time on market">
        <Async query={survival} shape="chart">
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
                  <div className="card-title">Median (including active listings)</div>
                  <div className="stat">
                    {data.median_days != null ? `${data.median_days.toFixed(0)}d` : "—"}
                  </div>
                  <div className="stat-sub">
                    from {data.n.toLocaleString("en-US")} listings
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
                    Counts only {data.delisted.toLocaleString("en-US")} completed
                    listings and ignores {data.censored.toLocaleString("en-US")}{" "}
                    still-active ones
                  </div>
                </div>
              </div>
              <Provenance envelope={data} />
            </>
          )}
        </Async>
      </Card>

      <Card title="Value by model year">
        <Async query={retention} shape="chart">
          {(data) => (
            <>
              <Chart
                x={data.points.map((p) => p.year_jalali)}
                series={[
                  {
                    name: "% of newest year's value",
                    data: data.points.map((p) => p.pct_of_newest),
                    area: true,
                  },
                ]}
                yFormatter={(v) => `${v}%`}
                height={220}
              />
              <Table head={["Model year", "Listings", "Median price", "% of newest year"]}>
                {data.points.map((p) => (
                  <tr key={p.year_jalali}>
                    <td>{p.year_jalali}</td>
                    <td className="num">{p.n.toLocaleString("en-US")}</td>
                    <td className="num">{toman(p.median_price)}</td>
                    <td className="num">{p.pct_of_newest}%</td>
                  </tr>
                ))}
              </Table>
              <p className="stat-sub">
                Over {data.span_years.toLocaleString("en-US")} years,{" "}
                {data.retained_over_span_pct}% of value has been retained
                {data.avg_annual_decline_pct != null &&
                  ` (about ${data.avg_annual_decline_pct}%/year)`}
                . The baseline is {data.reference_year}: the newest year with
                enough listings, not a factory price, which doesn't exist in
                this data.
              </p>
              <Provenance envelope={data} />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}
