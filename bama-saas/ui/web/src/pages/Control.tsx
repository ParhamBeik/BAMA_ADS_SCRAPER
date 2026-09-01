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
import { Loader2 } from "lucide-react";
import { api } from "../api";
import { Async, Card, Stat, humanError } from "../ui";
import { Button } from "../components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "../components/ui/dialog";

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

type Jobs = {
  latest_per_job: JobRow[];
  recent: JobRow[];
  /** JobRun names in flight right now, so a button can say so. */
  running: string[];
  fetch_running: boolean;
};

/**
 * One name per action, everywhere.
 *
 * A single button used to be called three different things on the way through:
 * it read `run_pipeline (skip fetch)`, it posted to `refresh-analytics`, and
 * the response came back saying `command: "refresh-analytics"` — so the run it
 * produced could not be found in the table underneath by the name on the
 * button. `job` is the identifier the API and the JobRun table both use;
 * `steps` is what to watch for a pipeline that records itself under its parts.
 */
const TRIGGERS = [
  { path: "/api/admin/jobs/fetch/", job: "fetch",
    about: "برداشت زنده از باما" },
  { path: "/api/admin/jobs/refresh-analytics/", job: "refresh-analytics",
    steps: ["snapshot", "market_index", "deal_scores"],
    about: "بازسازی تحلیل‌ها بدون برداشت تازه" },
  { path: "/api/admin/jobs/deal-scores/", job: "deal_scores",
    about: "بازسازی تابلوی معامله‌ها" },
  { path: "/api/admin/jobs/backfill-images/", job: "backfill_images",
    about: "پر کردن عکس‌ها از payload ذخیره‌شده" },
];

function n(value: number | undefined) {
  return (value ?? 0).toLocaleString("en-US");
}

/**
 * A stored timestamp, read in the timezone the rest of this app lives in.
 *
 * These are raw UTC ISO strings in the database and were being printed
 * verbatim onto a Jalali/Tehran page — a 3.5-hour trap next to a "0.2 hours
 * ago" in the same table.
 */
function when(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("fa-IR", {
    timeZone: "Asia/Tehran", dateStyle: "short", timeStyle: "medium",
  });
}

/** Seconds, at the precision anyone reads. Stored rounded now; old rows are not. */
function seconds(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(1)}s`;
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
  const [pending, setPending] = useState<(typeof TRIGGERS)[number] | null>(null);
  const health = useQuery({
    queryKey: ["admin-health"],
    queryFn: ({ signal }) => api.get<Health>("/api/admin/health/", signal),
    refetchInterval: 30_000,
  });
  const jobs = useQuery({
    queryKey: ["jobs-overview"],
    queryFn: ({ signal }) => api.get<Jobs>("/api/admin/jobs/overview/", signal),
    // Poll hard while something is in flight, idle otherwise. The API returns a
    // `poll` hint with every 202 and the UI used to ignore it entirely, so a
    // job's outcome appeared up to fifteen seconds after it finished.
    refetchInterval: (query) =>
      query.state.data?.running.length || query.state.data?.fetch_running ? 3_000 : 15_000,
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
        {/* Every button now knows whether its own job is in flight. The
            endpoints return 202 the moment the thread starts, which the UI was
            reading as "finished": nothing disabled, nothing span, and firing
            `deal_scores` three times in one second was possible — and did
            happen, producing three threads rebuilding the same tables. */}
        <div className="row">
          {TRIGGERS.map((t) => {
            const running = t.job === "fetch"
              ? Boolean(jobs.data?.fetch_running)
              : (t.steps ?? [t.job]).some((s) => jobs.data?.running.includes(s));
            const starting = trigger.isPending && trigger.variables === t.path;
            return (
              <button
                key={t.path}
                className="btn"
                disabled={running || trigger.isPending}
                aria-busy={running || starting}
                title={t.about}
                onClick={() => setPending(t)}
              >
                {running ? <><Loader2 className="size-4 animate-spin" /> در حال اجرا…</>
                  : starting ? "در حال شروع…" : t.job}
              </button>
            );
          })}
        </div>
        <p className="stat-sub">
          {TRIGGERS.map((t) => `${t.job}: ${t.about}`).join(" · ")}
        </p>
        {trigger.isSuccess && (
          <p className="stat-sub">
            کار پذیرفته شد. وضعیت نهایی آن پس از تازه‌سازی بعدی در جدول زیر می‌آید.
          </p>
        )}
        {trigger.isError && <p className="warn">{humanError(trigger.error)}</p>}
      </Card>

      {/* The app's own dialog, not the browser's. `confirm()` is unstyled, sits
          outside the RTL document, blocks the whole tab, and reads the raw job
          id with no explanation of what the job does. */}
      <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>اجرای «{pending?.job}»</DialogTitle>
            <DialogDescription>{pending?.about}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPending(null)}>انصراف</Button>
            <Button
              onClick={() => {
                if (pending) trigger.mutate(pending.path);
                setPending(null);
              }}
            >
              اجرا کن
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Card title="آخرین اجرای هر کار">
        <Async query={jobs}>
          {(data) => (
            <table className="table inspect-table">
              <thead>
                <tr>
                  <th>name</th><th>status</th><th>triggered_by</th>
                  <th>started_at (تهران)</th><th>duration_s</th><th>error</th>
                </tr>
              </thead>
              <tbody>
                {data.latest_per_job.map((r) => (
                  <tr key={r.name}>
                    <td><code>{r.name}</code></td>
                    <td>{statusBadge(r.status)}</td>
                    <td>{r.triggered_by}</td>
                    <td>{when(r.started_at)}</td>
                    <td>{seconds(r.duration_s)}</td>
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
                <div className="table-wrap">
                <table className="table inspect-table">
                  <thead>
                    <tr><th>started_at (تهران)</th><th>name</th><th>status</th><th>detail</th></tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={`${r.name}-${r.started_at}-${i}`}>
                        <td>{when(r.started_at)}</td>
                        <td><code>{r.name}</code></td>
                        <td>{r.status}</td>
                        <td>{(r.detail || r.error || "—").slice(0, 160)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
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
              <div className="table-wrap">
              <table className="table inspect-table">
                <tbody>
                  <tr><th>ads</th><td>{n(data.catalog.ads)}</td></tr>
                  <tr><th>active_ads</th><td>{n(data.catalog.active_ads)}</td></tr>
                  <tr><th>removed_ads</th><td>{n(data.catalog.removed_ads)}</td></tr>
                  <tr><th>unverified_ads</th><td>{n(data.catalog.unverified_ads)}</td></tr>
                  <tr><th>brands</th><td>{n(data.catalog.brands)}</td></tr>
                  <tr><th>models</th><td>{n(data.catalog.models)}</td></tr>
                  {/* Linked, because these are a queue: a row minted by
                      ingestion is an unproven catalog entry and every cohort
                      keyed on it is unproven with it. The count alone gave no
                      way to act on them. */}
                  <tr>
                    <th>unconfirmed_brands</th>
                    <td>
                      <a href="/admin/core/brand/?is_confirmed__exact=0">
                        {n(data.catalog.unconfirmed_brands)}
                      </a>
                    </td>
                  </tr>
                  <tr>
                    <th>unconfirmed_models</th>
                    <td>
                      <a href="/admin/core/model/?is_confirmed__exact=0">
                        {n(data.catalog.unconfirmed_models)}
                      </a>
                    </td>
                  </tr>
                  <tr><th>rejects_24h</th><td>{n(data.catalog.rejects_24h)}</td></tr>
                  <tr><th>database size</th><td>{bytes(data.database.size_bytes)}</td></tr>
                  <tr><th>db_connections</th><td>{n(data.database.connections)}</td></tr>
                  <tr><th>migrations_applied</th><td>{n(data.database.migrations_applied)}</td></tr>
                </tbody>
              </table>
              </div>
              {data.catalog.reject_rules_24h?.length > 0 && (
                <div className="table-wrap">
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
                </div>
              )}
            </>
          )}
        </Async>
      </Card>

      <MlHealth />
    </div>
  );
}

/**
 * The learned layer, beside the crawl health rather than on its own screen.
 *
 * The three readings fail differently and that is why there are three. Input
 * drift needs no outcomes at all and is the earliest warning; prediction drift
 * catches an output distribution moving while the inputs look unchanged; live
 * error is the ground truth and the slowest. None of them fires anything
 * automatically — "retrain now" is a judgement about whether the new data is
 * better, and a threshold that retrains on a drift number alone will happily
 * refit on a week when the crawler was broken.
 */
interface MlReport {
  available: boolean;
  reason?: string | null;
  active: Record<string, number>;
  shadow: string[];
  scored_ads: number;
  models: {
    name: string; version: number; status: string; training_rows: number;
    trained_at: string;
  }[];
  input_drift: {
    available: boolean; reason?: string; verdict?: string | null;
    train_rows?: number; live_rows?: number;
    features?: { feature: string; psi: number | null; band: string | null }[];
  };
  prediction_drift: {
    available: boolean; reason?: string; scored_rows?: number;
    live_median_abs_residual_pct?: number; holdout_mape?: number;
    ratio?: number | null; signed_median_pct?: number;
  };
}

const PSI_TONE: Record<string, string> = {
  stable: "ok", watch: "warn", unstable: "fail",
};

function MlHealth() {
  const ml = useQuery({
    queryKey: ["ml-monitoring"],
    queryFn: ({ signal }) => api.get<MlReport>("/api/ml/monitoring/", signal),
    refetchInterval: 60_000,
  });

  return (
    <Card title="ml">
      <Async query={ml}>
        {(data) => {
          if (!data.available) {
            return <p className="muted">{data.reason ?? "ml layer unavailable"}</p>;
          }
          const drift = data.input_drift;
          const pred = data.prediction_drift;
          return (
            <div className="grid cols-2">
              <div className="table-wrap">
                <table className="table inspect-table">
                  <tbody>
                    <tr><th>scored_ads</th><td>{n(data.scored_ads)}</td></tr>
                    <tr>
                      <th>active</th>
                      <td>
                        {Object.entries(data.active).length
                          ? Object.entries(data.active)
                              .map(([k, v]) => `${k} v${v}`).join(", ")
                          : "—"}
                      </td>
                    </tr>
                    {/* Held in shadow is a result, not a gap. A challenger that
                        lost to the peer median is the outcome the gate exists
                        to produce, and it belongs on screen. */}
                    <tr>
                      <th>shadow</th>
                      <td>{data.shadow.length ? data.shadow.join(", ") : "—"}</td>
                    </tr>
                    <tr>
                      <th>live_median_abs_residual</th>
                      <td>
                        {pred.available
                          ? `${pred.live_median_abs_residual_pct}% vs holdout ${pred.holdout_mape}% (x${pred.ratio})`
                          : pred.reason}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="table-wrap">
                <table className="table inspect-table">
                  <thead>
                    <tr>
                      <th>input drift (PSI)</th>
                      <th>
                        {drift.available ? (
                          <span className={`badge ${PSI_TONE[drift.verdict ?? ""] ?? ""}`}>
                            {drift.verdict}
                          </span>
                        ) : drift.reason}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {(drift.features ?? []).map((f) => (
                      <tr key={f.feature}>
                        <td><code>{f.feature}</code></td>
                        <td>
                          {f.psi == null ? "—" : f.psi}{" "}
                          {f.band && (
                            <span className={`badge ${PSI_TONE[f.band] ?? ""}`}>
                              {f.band}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        }}
      </Async>
    </Card>
  );
}
