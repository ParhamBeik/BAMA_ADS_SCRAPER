/**
 * Operations — staff only.
 *
 * Exists because "did last night's work actually run?" used to be answerable
 * only by reading container logs, and a step skipped because its prerequisite
 * failed was indistinguishable from one that succeeded. Skipped is rendered as
 * its own state here for exactly that reason.
 */
import type { ReactElement } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleSlash, Loader2, XCircle } from "lucide-react";
import { api } from "../api/client";
import { Async, Card, Table } from "../ui";

interface JobRow {
  name: string;
  status: "ok" | "failed" | "skipped" | "running";
  triggered_by: string;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
  detail: string;
  error: string;
}

interface Health {
  ok: boolean;
  checks: { name: string; ok: boolean; detail: string }[];
}

const ICON: Record<JobRow["status"], ReactElement> = {
  ok: <CheckCircle2 size={14} className="up" />,
  failed: <XCircle size={14} className="down" />,
  skipped: <CircleSlash size={14} className="warn" />,
  running: <Loader2 size={14} />,
};

export function Operations() {
  const jobs = useQuery({
    queryKey: ["jobs"],
    refetchInterval: 30_000,
    queryFn: ({ signal }) =>
      api.get<{ latest_per_job: JobRow[]; recent: JobRow[] }>(
        "/api/admin/jobs/overview/",
        signal,
      ),
  });

  const health = useQuery({
    queryKey: ["crawl-health"],
    refetchInterval: 60_000,
    retry: false,
    queryFn: ({ signal }) => api.get<Health>("/api/admin/jobs/crawl-health/", signal),
  });

  return (
    <>
      <Card title="Crawl health">
        <Async query={health}>
          {(data) => (
            <Table head={["Check", "Status"]}>
              {data.checks?.map((c) => (
                <tr key={c.name}>
                  <td>
                    {c.name.replace(/_/g, " ")}
                    <div className="stat-sub">{c.detail}</div>
                  </td>
                  <td className="num">
                    {c.ok ? (
                      <span className="badge accent">OK</span>
                    ) : (
                      <span className="badge warn">Failing</span>
                    )}
                  </td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Card>

      <div style={{ height: 14 }} />

      <Card title="Scheduled jobs — latest run of each">
        <Async query={jobs}>
          {(data) => (
            <Table head={["Job", "Status", "Duration", "When"]}>
              {data.latest_per_job.map((j) => (
                <tr key={j.name}>
                  <td>
                    {j.name}
                    {j.error && <div className="stat-sub down">{j.error.slice(0, 120)}</div>}
                    {j.status === "skipped" && <div className="stat-sub warn">{j.detail}</div>}
                  </td>
                  <td className="num">
                    <span
                      className={`badge ${
                        j.status === "ok" ? "accent" : j.status === "running" ? "" : "warn"
                      }`}
                    >
                      {ICON[j.status]} {j.status}
                    </span>
                  </td>
                  <td className="num">
                    {j.duration_s != null ? `${j.duration_s.toFixed(1)}s` : "—"}
                  </td>
                  <td className="num">{new Date(j.started_at).toLocaleString("en-GB")}</td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Card>
    </>
  );
}
