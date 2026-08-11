import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async } from "../../ui";

export function ControlJobs() {
  const overview = useQuery({
    queryKey: ["jobs-overview"],
    queryFn: ({ signal }) => api.get<unknown>("/api/admin/jobs/overview/", signal),
    refetchInterval: 60_000,
  });
  const health = useQuery({
    queryKey: ["crawl-health"],
    queryFn: ({ signal }) => api.get<unknown>("/api/admin/jobs/crawl-health/", signal),
    refetchInterval: 30_000,
  });

  async function trigger(path: string) {
    if (!confirm("اجرای این کار را تأیید می‌کنید؟")) return;
    await api.post(path);
    alert("درخواست ارسال شد");
  }

  return (
    <div className="stack">
      <h1>کارهای پس‌زمینه</h1>
      <div className="row">
        <button className="btn" onClick={() => trigger("/api/admin/jobs/evaluate-alerts/")}>ارزیابی هشدارها</button>
        <button className="btn" onClick={() => trigger("/api/admin/jobs/deal-scores/")}>محاسبه امتیاز معامله</button>
      </div>
      <Async query={overview}>{(data) => <pre className="card code-block">{JSON.stringify(data, null, 2)}</pre>}</Async>
      <Async query={health}>{(data) => <pre className="card code-block">{JSON.stringify(data, null, 2)}</pre>}</Async>
    </div>
  );
}
