import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async } from "../../ui";

type Page<T> = { count: number; page: number; page_size: number; results: T[] };
type FetchRun = {
  id: string; source: string; status: string; mode: string;
  pages_fetched: number; deepest_rank: number | null; reached_end: boolean;
  stop_reason: string; fetched_count: number; created_count: number;
  updated_count: number; skipped_count: number; error: string;
  started_at: string | null; finished_at: string | null;
};
type Coverage = {
  page_index: number; rank_lo: number; rank_hi: number;
  ad_count: number; new_count: number; changed_count: number; fetched_at: string;
};
type Reject = {
  id: number; code: string; rule: string; detail: string;
  raw_payload: unknown; observed_at: string;
};
type Gaps = {
  since_hours: number; known_feed_depth: number | null; gap_count: number;
  gaps: { rank_lo: number; rank_hi: number }[];
};

export function ControlIngestion() {
  const [runId, setRunId] = useState<string | null>(null);
  const [reject, setReject] = useState<Reject | null>(null);

  const runs = useQuery({
    queryKey: ["inspect-fetch-runs"],
    queryFn: ({ signal }) => api.get<Page<FetchRun>>("/api/admin/inspect/fetch-runs/?page_size=50", signal),
    refetchInterval: 30_000,
  });
  const pages = useQuery({
    queryKey: ["inspect-pages", runId],
    queryFn: ({ signal }) => api.get<{ fetch_run: FetchRun; results: Coverage[]; count: number }>(
      `/api/admin/inspect/fetch-runs/${runId}/pages/?page_size=200`,
      signal,
    ),
    enabled: !!runId,
  });
  const gaps = useQuery({
    queryKey: ["inspect-gaps"],
    queryFn: ({ signal }) => api.get<Gaps>("/api/admin/inspect/gaps/", signal),
  });
  const rejects = useQuery({
    queryKey: ["inspect-rejects"],
    queryFn: ({ signal }) => api.get<Page<Reject>>("/api/admin/inspect/rejects/?page_size=50", signal),
  });

  return (
    <div className="stack">
      <h1>Ingestion</h1>
      <p className="muted">history_fetchrun, history_pagecoverage, history_ingestreject.</p>

      <h2>coverage gaps (24h)</h2>
      <Async query={gaps}>
        {(data) => data.gap_count === 0 ? (
          <p className="muted">none. known_feed_depth={data.known_feed_depth ?? "—"}</p>
        ) : (
          <table className="table inspect-table">
            <thead><tr><th>rank_lo</th><th>rank_hi</th></tr></thead>
            <tbody>
              {data.gaps.map((g) => (
                <tr key={`${g.rank_lo}-${g.rank_hi}`}><td>{g.rank_lo}</td><td>{g.rank_hi}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>

      <h2>FetchRun</h2>
      <Async query={runs}>
        {(data) => (
          <table className="table inspect-table">
            <thead>
              <tr>
                <th>started_at</th><th>mode</th><th>status</th><th>reached_end</th>
                <th>pages</th><th>deepest_rank</th><th>fetched</th><th>stop_reason</th>
              </tr>
            </thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={r.id} className={`clickable${runId === r.id ? " selected" : ""}`} onClick={() => setRunId(r.id)}>
                  <td>{r.started_at ?? r.id.slice(0, 8)}</td>
                  <td>{r.mode}</td>
                  <td>{r.status}</td>
                  <td>{String(r.reached_end)}</td>
                  <td>{r.pages_fetched}</td>
                  <td>{r.deepest_rank ?? "—"}</td>
                  <td>{r.fetched_count}</td>
                  <td>{r.stop_reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      {runId && (
        <>
          <h2>PageCoverage {runId.slice(0, 8)}</h2>
          <Async query={pages}>
            {(data) => (
              <table className="table inspect-table">
                <thead>
                  <tr><th>page_index</th><th>rank_lo</th><th>rank_hi</th><th>ad_count</th><th>new</th><th>changed</th></tr>
                </thead>
                <tbody>
                  {data.results.map((p) => (
                    <tr key={p.page_index}>
                      <td>{p.page_index}</td><td>{p.rank_lo}</td><td>{p.rank_hi}</td>
                      <td>{p.ad_count}</td><td>{p.new_count}</td><td>{p.changed_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Async>
        </>
      )}

      <h2>IngestReject</h2>
      <Async query={rejects}>
        {(data) => (
          <table className="table inspect-table">
            <thead><tr><th>observed_at</th><th>code</th><th>rule</th><th>detail</th></tr></thead>
            <tbody>
              {data.results.map((r) => (
                <tr key={r.id} className="clickable" onClick={() => setReject(r)}>
                  <td>{r.observed_at}</td>
                  <td><code>{r.code}</code></td>
                  <td><code>{r.rule}</code></td>
                  <td>{r.detail || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      {reject && (
        <>
          <h2>reject raw_payload {reject.code} / {reject.rule}</h2>
          <pre className="card code-block">{JSON.stringify(reject.raw_payload, null, 2)}</pre>
        </>
      )}
    </div>
  );
}
