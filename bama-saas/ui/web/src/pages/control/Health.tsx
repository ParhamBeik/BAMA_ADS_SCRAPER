import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async } from "../../ui";

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

function n(v: number | undefined) {
  return (v ?? 0).toLocaleString("en-US");
}

export function ControlHealth() {
  const q = useQuery({
    queryKey: ["admin-health"],
    queryFn: ({ signal }) => api.get<Health>("/api/admin/health/", signal),
    refetchInterval: 30_000,
  });
  return (
    <div className="stack">
      <h1>Status</h1>
      <p className="muted">Counts from Postgres. Crawl checks from FetchRun / PageCoverage / IngestReject.</p>
      <Async query={q}>
        {(data) => (
          <>
            <div className="stat-row">
              <span>ads {n(data.catalog.ads)}</span>
              <span>active {n(data.catalog.active_ads)}</span>
              <span>removed {n(data.catalog.removed_ads)}</span>
              <span>brands {n(data.catalog.brands)}</span>
              <span>models {n(data.catalog.models)}</span>
              <span>rejects_24h {n(data.catalog.rejects_24h)}</span>
            </div>
            <h2>crawl checks</h2>
            <table className="table inspect-table">
              <thead>
                <tr><th>name</th><th>ok</th><th>detail</th></tr>
              </thead>
              <tbody>
                {data.crawl.map((c) => (
                  <tr key={c.name}>
                    <td><code>{c.name}</code></td>
                    <td><span className={`badge ${c.ok ? "ok" : "fail"}`}>{c.ok ? "OK" : "FAIL"}</span></td>
                    <td>{c.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <h2>catalog</h2>
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
              </tbody>
            </table>
            {data.catalog.reject_rules_24h?.length > 0 && (
              <>
                <h2>reject_rules_24h</h2>
                <table className="table inspect-table">
                  <thead><tr><th>rule</th><th>n</th></tr></thead>
                  <tbody>
                    {data.catalog.reject_rules_24h.map((r) => (
                      <tr key={r.rule}><td><code>{r.rule}</code></td><td>{n(r.n)}</td></tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <h2>database</h2>
            <table className="table inspect-table">
              <tbody>
                <tr><th>size_bytes</th><td>{n(data.database.size_bytes)}</td></tr>
                <tr><th>connections</th><td>{n(data.database.connections)}</td></tr>
                <tr><th>migrations_applied</th><td>{n(data.database.migrations_applied)}</td></tr>
              </tbody>
            </table>
          </>
        )}
      </Async>
    </div>
  );
}
