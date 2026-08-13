/**
 * Crawl health and jobs, on one page.
 *
 * These were two screens that answered the same question from opposite ends:
 * `/api/admin/health/` returns the crawl checks alongside the catalog counts, and
 * the jobs screen fetched those same checks again from
 * `/api/admin/jobs/crawl-health/`. One request now feeds both halves — a check
 * that fails and the run that failed it belong next to each other anyway.
 *
 * Inspecting individual records is Django admin's job, not this page's.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Async, Card } from "../ui";

type Check = { name: string; ok: boolean; detail: string };

type Health = {
  database: { size_bytes: number; connections: number; migrations_applied: number };
  catalog: {
    ads: number;
    active_ads: number;
    removed_ads: number;
    brands: number;
    models: number;
    unconfirmed_brands: number;
    unconfirmed_models: number;
    rejects_24h: number;
    reject_rules_24h: { rule: string; n: number }[];
  };
  crawl: Check[];
};

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

type Jobs = { latest_per_job: JobRow[]; recent: JobRow[] };

const TRIGGERS = [
  { path: "/api/admin/jobs/fetch/", label: "fetch_live" },
  { path: "/api/admin/jobs/refresh-analytics/", label: "run_pipeline (skip fetch)" },
  { path: "/api/admin/jobs/deal-scores/", label: "compute_deal_scores" },
];

function n(value: number | undefined) {
  return (value ?? 0).toLocaleString("en-US");
}

function statusBadge(status: string) {
  const tone = status === "ok" ? "ok" : status === "failed" ? "fail" : "";
  return <span className={`badge ${tone}`}>{status}</span>;
}

export function Control() {
  const health = useQuery({
    queryKey: ["admin-health"],
    queryFn: ({ signal }) => api.get<Health>("/api/admin/health/", signal),
    refetchInterval: 30_000,
  });
  const jobs = useQuery({
    queryKey: ["jobs-overview"],
    queryFn: ({ signal }) => api.get<Jobs>("/api/admin/jobs/overview/", signal),
    refetchInterval: 15_000,
  });

  async function trigger(path: string) {
    if (!confirm("Run this job?")) return;
    await api.post(path);
    await jobs.refetch();
  }

  return (
    <div className="stack" dir="ltr">
      <p className="muted">
        Crawl checks from FetchRun / PageCoverage / IngestReject; counts from
        Postgres. Individual records live in <a href="/admin/">Django admin</a>.
      </p>

      <Card title="Crawl checks">
        <Async query={health}>
          {(data) => (
            <table className="table inspect-table">
              <thead>
                <tr><th>name</th><th>ok</th><th>detail</th></tr>
              </thead>
              <tbody>
                {data.crawl.map((c) => (
                  <tr key={c.name}>
                    <td><code>{c.name}</code></td>
                    <td>
                      <span className={`badge ${c.ok ? "ok" : "fail"}`}>
                        {c.ok ? "OK" : "FAIL"}
                      </span>
                    </td>
                    <td>{c.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      </Card>

      <Card title="Run a job">
        <div className="row">
          {TRIGGERS.map((t) => (
            <button key={t.path} className="btn" onClick={() => trigger(t.path)}>
              {t.label}
            </button>
          ))}
        </div>
      </Card>

      <Card title="Latest run per job">
        <Async query={jobs}>
          {(data) => (
            <table className="table inspect-table">
              <thead>
                <tr>
                  <th>name</th><th>status</th><th>triggered_by</th>
                  <th>started_at</th><th>duration_s</th><th>error</th>
                </tr>
              </thead>
              <tbody>
                {data.latest_per_job.map((r) => (
                  <tr key={r.name}>
                    <td><code>{r.name}</code></td>
                    <td>{statusBadge(r.status)}</td>
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
      </Card>

      <Card title="Recent runs">
        <Async query={jobs}>
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
      </Card>

      <Card title="Catalog and database">
        <Async query={health}>
          {(data) => (
            <>
              <table className="table inspect-table">
                <tbody>
                  <tr><th>ads</th><td>{n(data.catalog.ads)}</td></tr>
                  <tr><th>active_ads</th><td>{n(data.catalog.active_ads)}</td></tr>
                  <tr><th>removed_ads</th><td>{n(data.catalog.removed_ads)}</td></tr>
                  <tr><th>brands</th><td>{n(data.catalog.brands)}</td></tr>
                  <tr><th>models</th><td>{n(data.catalog.models)}</td></tr>
                  <tr><th>unconfirmed_brands</th><td>{n(data.catalog.unconfirmed_brands)}</td></tr>
                  <tr><th>unconfirmed_models</th><td>{n(data.catalog.unconfirmed_models)}</td></tr>
                  <tr><th>rejects_24h</th><td>{n(data.catalog.rejects_24h)}</td></tr>
                  <tr><th>db_size_bytes</th><td>{n(data.database.size_bytes)}</td></tr>
                  <tr><th>db_connections</th><td>{n(data.database.connections)}</td></tr>
                  <tr><th>migrations_applied</th><td>{n(data.database.migrations_applied)}</td></tr>
                </tbody>
              </table>
              {data.catalog.reject_rules_24h?.length > 0 && (
                <table className="table inspect-table">
                  <thead><tr><th>reject rule (24h)</th><th>n</th></tr></thead>
                  <tbody>
                    {data.catalog.reject_rules_24h.map((r) => (
                      <tr key={r.rule}>
                        <td><code>{r.rule}</code></td>
                        <td>{n(r.n)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}
