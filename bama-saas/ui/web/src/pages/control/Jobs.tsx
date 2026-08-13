import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";
import { Async } from "../../ui";

type JobRow = {
  name: string;
  status: string;
  triggered_by: string;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
  detail: string;
  error: string;
};

type Overview = { latest_per_job: JobRow[]; recent: JobRow[] };

async function getCrawlHealth(signal?: AbortSignal) {
  try {
    return await api.get<{ ok: boolean; checks: { name: string; ok: boolean; detail: string }[] }>(
      "/api/admin/jobs/crawl-health/",
      signal,
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 503 && err.body && typeof err.body === "object") {
      return err.body as { ok: boolean; checks: { name: string; ok: boolean; detail: string }[] };
    }
    throw err;
  }
}

export function ControlJobs() {
  const overview = useQuery({
    queryKey: ["jobs-overview"],
    queryFn: ({ signal }) => api.get<Overview>("/api/admin/jobs/overview/", signal),
    refetchInterval: 15_000,
  });
  const health = useQuery({
    queryKey: ["crawl-health"],
    queryFn: ({ signal }) => getCrawlHealth(signal),
    refetchInterval: 30_000,
  });

  async function trigger(path: string) {
    if (!confirm("Run this job?")) return;
    await api.post(path);
    await overview.refetch();
  }

  return (
    <div className="stack">
      <h1>Jobs</h1>
      <p className="muted">history_jobrun. Triggers spawn the same commands the worker runs.</p>
      <div className="row">
        <button className="btn" onClick={() => trigger("/api/admin/jobs/fetch/")}>fetch_live</button>
        <button className="btn" onClick={() => trigger("/api/admin/jobs/refresh-analytics/")}>run_pipeline (skip fetch)</button>
        <button className="btn" onClick={() => trigger("/api/admin/jobs/deal-scores/")}>compute_deal_scores</button>
        <button className="btn" onClick={() => trigger("/api/admin/jobs/evaluate-alerts/")}>evaluate_alerts</button>
      </div>
      <h2>latest per name</h2>
      <Async query={overview}>
        {(data) => (
          <table className="table inspect-table">
            <thead>
              <tr><th>name</th><th>status</th><th>triggered_by</th><th>started_at</th><th>duration_s</th><th>error</th></tr>
            </thead>
            <tbody>
              {data.latest_per_job.map((r) => (
                <tr key={r.name}>
                  <td><code>{r.name}</code></td>
                  <td><span className={`badge ${r.status === "ok" ? "ok" : r.status === "failed" ? "fail" : ""}`}>{r.status}</span></td>
                  <td>{r.triggered_by}</td>
                  <td>{r.started_at}</td>
                  <td>{r.duration_s ?? "—"}</td>
                  <td>{r.error || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      <h2>recent</h2>
      <Async query={overview}>
        {(data) => (
          <table className="table inspect-table">
            <thead>
              <tr><th>started_at</th><th>name</th><th>status</th><th>detail</th></tr>
            </thead>
            <tbody>
              {data.recent.map((r, i) => (
                <tr key={`${r.name}-${r.started_at}-${i}`}>
                  <td>{r.started_at}</td>
                  <td><code>{r.name}</code></td>
                  <td>{r.status}</td>
                  <td>{(r.detail || r.error || "—").slice(0, 160)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      <h2>crawl_health</h2>
      <Async query={health}>
        {(data) => (
          <table className="table inspect-table">
            <thead><tr><th>name</th><th>ok</th><th>detail</th></tr></thead>
            <tbody>
              {data.checks.map((c) => (
                <tr key={c.name}>
                  <td><code>{c.name}</code></td>
                  <td><span className={`badge ${c.ok ? "ok" : "fail"}`}>{c.ok ? "OK" : "FAIL"}</span></td>
                  <td>{c.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
    </div>
  );
}
