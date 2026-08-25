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
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { Async, Card, Stat } from "../ui";

const RECENT_RUNS_COLLAPSED = 15;

type Check = { name: string; ok: boolean; detail: string };

type Health = {
  database: { size_bytes: number; connections: number; migrations_applied: number };
  catalog: {
    ads: number;
    active_ads: number;
    removed_ads: number;
    unverified_ads: number;
    brands: number;
    models: number;
    unconfirmed_brands: number;
    unconfirmed_models: number;
    rejects_24h: number;
    reject_rules_24h: { rule: string; n: number }[];
  };
  fetch: {
    latest: {
      mode: string;
      status: string;
      stop_reason: string;
      reached_end: boolean;
      pages_fetched: number;
      deepest_rank: number | null;
      finished_at: string | null;
    } | null;
    coverage_depth: number | null;
    coverage_gap_count: number;
    coverage_window_hours: number;
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
  { path: "/api/admin/jobs/backfill-images/", label: "backfill_images" },
];

function n(value: number | undefined) {
  return (value ?? 0).toLocaleString("en-US");
}

function bytes(value: number) {
  if (value < 1_024) return `${value} B`;
  if (value < 1_024 ** 2) return `${(value / 1_024).toFixed(1)} KB`;
  if (value < 1_024 ** 3) return `${(value / 1_024 ** 2).toFixed(1)} MB`;
  return `${(value / 1_024 ** 3).toFixed(1)} GB`;
}

function statusBadge(status: string) {
  const tone = status === "ok" ? "ok" : status === "failed" ? "fail" : "";
  return <span className={`badge ${tone}`}>{status}</span>;
}

export function Control() {
  const client = useQueryClient();
  const [showAllRuns, setShowAllRuns] = useState(false);
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
  const trigger = useMutation({
    mutationFn: (path: string) => api.post(path),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["jobs-overview"] }),
        client.invalidateQueries({ queryKey: ["admin-health"] }),
      ]);
    },
  });

  return (
    <div className="stack" dir="ltr">
      <p className="muted" dir="rtl">
        بررسی‌های خزنده از FetchRun / PageCoverage / IngestReject؛ شمارش‌ها از
        Postgres. رکوردهای منفرد در <a href="/admin/">پنل مدیریت جنگو</a> هستند.
        نام ستون‌ها در جدول‌های زیر عمداً ترجمه نشده‌اند: این‌ها دقیقاً همان
        شناسه‌های پایگاه داده‌اند.
      </p>

      <Async query={health}>
        {(data) => {
          const failed = data.crawl.filter((check) => !check.ok);
          return (
            <div className="grid cols-4">
              <Stat
                label="سلامت خزنده"
                value={failed.length ? `${failed.length} خطا` : "سالم"}
                tone={failed.length ? "warn" : "up"}
                sub={`${data.crawl.length} بررسی`}
              />
              <Stat
                label="آگهی‌های فعال"
                value={n(data.catalog.active_ads)}
                sub={`${n(data.catalog.removed_ads)} حذف‌شده، ${n(
                  data.catalog.unverified_ads,
                )} تأییدنشده`}
              />
              <Stat
                label="پایگاه داده"
                value={bytes(data.database.size_bytes)}
                sub={`${n(data.database.connections)} اتصال`}
              />
              <Stat
                label="آخرین برداشت"
                value={data.fetch.latest?.mode ?? "—"}
                tone={data.fetch.latest?.status === "failed" ? "warn" : "up"}
                sub={
                  data.fetch.latest
                    ? `${data.fetch.latest.stop_reason || "running"} · عمق ${n(data.fetch.latest.deepest_rank ?? 0)}`
                    : "برداشتی ثبت نشده"
                }
              />
            </div>
          );
        }}
      </Async>

      <Card title="بررسی‌های خزنده">
        <Async query={health}>
          {(data) => (
            <div className="check-grid">
              {data.crawl.map((c) => (
                <div key={c.name} className={`check-item${c.ok ? "" : " fail"}`}>
                  <div className="row between">
                    <code>{c.name}</code>
                    <span className={`badge ${c.ok ? "ok" : "fail"}`}>
                      {c.ok ? "OK" : "FAIL"}
                    </span>
                  </div>
                  <p className="stat-sub">{c.detail}</p>
                </div>
              ))}
            </div>
          )}
        </Async>
      </Card>

      <Card title="دفتر پوشش خزنده">
        <div className="row between wrap">
          <span>
            سقف عمق: <code>{n(health.data?.fetch.coverage_depth ?? 0)}</code>
          </span>
          <span>
            شکاف‌ها: <code>{n(health.data?.fetch.coverage_gap_count ?? 0)}</code>
          </span>
          <span className="stat-sub">
            پنجره چرخشی: {health.data?.fetch.coverage_window_hours ?? 24} ساعت
          </span>
        </div>
      </Card>

      <Card title="اجرای دستی کار">
        <div className="row">
          {TRIGGERS.map((t) => (
            <button
              key={t.path}
              className="btn"
              disabled={trigger.isPending}
              onClick={() => {
                if (confirm(`«${t.label}» اجرا شود؟`)) trigger.mutate(t.path);
              }}
            >
              {trigger.isPending ? "در حال شروع…" : t.label}
            </button>
          ))}
        </div>
        {trigger.isSuccess && (
          <p className="stat-sub">
            کار پذیرفته شد. وضعیت نهایی آن پس از تازه‌سازی بعدی در جدول زیر می‌آید.
          </p>
        )}
        {trigger.isError && (
          <p className="warn">
            {(trigger.error as Error).message || "کار شروع نشد."}
          </p>
        )}
      </Card>

      <Card title="آخرین اجرای هر کار">
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

      <Card
        title="اجراهای اخیر"
        action={
          <button className="ghost" onClick={() => setShowAllRuns((v) => !v)}>
            {showAllRuns ? "نمایش کمتر" : "نمایش همه"}
          </button>
        }
      >
        <Async query={jobs}>
          {(data) => {
            const rows = showAllRuns ? data.recent : data.recent.slice(0, RECENT_RUNS_COLLAPSED);
            return (
              <>
                <table className="table inspect-table">
                  <thead>
                    <tr><th>started_at</th><th>name</th><th>status</th><th>detail</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={`${r.name}-${r.started_at}-${i}`}>
                        <td>{r.started_at}</td>
                        <td><code>{r.name}</code></td>
                        <td>{r.status}</td>
                        <td>{(r.detail || r.error || "—").slice(0, 160)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!showAllRuns && data.recent.length > RECENT_RUNS_COLLAPSED && (
                  <p className="stat-sub" style={{ marginTop: 8 }}>
                    {data.recent.length - RECENT_RUNS_COLLAPSED} اجرای دیگر پنهان است.
                  </p>
                )}
              </>
            );
          }}
        </Async>
      </Card>

      <Card title="کاتالوگ و پایگاه داده">
        <Async query={health}>
          {(data) => (
            <>
              <table className="table inspect-table">
                <tbody>
                  <tr><th>ads</th><td>{n(data.catalog.ads)}</td></tr>
                  <tr><th>active_ads</th><td>{n(data.catalog.active_ads)}</td></tr>
                  <tr><th>removed_ads</th><td>{n(data.catalog.removed_ads)}</td></tr>
                  <tr><th>unverified_ads</th><td>{n(data.catalog.unverified_ads)}</td></tr>
                  <tr><th>brands</th><td>{n(data.catalog.brands)}</td></tr>
                  <tr><th>models</th><td>{n(data.catalog.models)}</td></tr>
                  <tr><th>unconfirmed_brands</th><td>{n(data.catalog.unconfirmed_brands)}</td></tr>
                  <tr><th>unconfirmed_models</th><td>{n(data.catalog.unconfirmed_models)}</td></tr>
                  <tr><th>rejects_24h</th><td>{n(data.catalog.rejects_24h)}</td></tr>
                  <tr><th>database size</th><td>{bytes(data.database.size_bytes)}</td></tr>
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
