import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { Async, Fa, toman } from "../ui";
import { useAuth } from "../auth";

export function Compare() {
  const { me } = useAuth();
  const [ids, setIds] = useState("");
  const [queryIds, setQueryIds] = useState("");
  const q = useQuery({
    queryKey: ["compare", queryIds],
    queryFn: ({ signal }) => api.get<{ models: { model_id: number; name_fa: string; brand: string; inventory: number; median_price: number | null; available: boolean; reason?: string }[] }>(`/api/research/compare/?ids=${queryIds}`, signal),
    enabled: !!queryIds && !!me?.verified,
  });

  if (!me) return <p dir="rtl">برای مقایسه وارد شوید.</p>;
  if (!me.verified) return <p dir="rtl">ابتدا ایمیل را تأیید کنید.</p>;
  if (!me.limits.model_comparison) return <p dir="rtl">مقایسه مدل نیاز به طرح Pro دارد.</p>;

  return (
    <div className="stack" dir="rtl">
      <h1>مقایسه مدل‌ها</h1>
      <p className="muted">حداکثر سه شناسه مدل را با ویرگول وارد کنید.</p>
      <div className="row">
        <input value={ids} onChange={(e) => setIds(e.target.value)} placeholder="مثلاً 12,34,56" />
        <button className="btn primary" onClick={() => setQueryIds(ids)}>مقایسه</button>
      </div>
      {queryIds && (
        <Async query={q}>
          {(data) => (
            <table className="table">
              <thead>
                <tr>
                  <th>مدل</th><th>برند</th><th>موجودی</th><th>میانه قیمت</th><th>وضعیت</th>
                </tr>
              </thead>
              <tbody>
                {data.models.map((m) => (
                  <tr key={m.model_id}>
                    <td><Fa>{m.name_fa}</Fa></td>
                    <td><Fa>{m.brand}</Fa></td>
                    <td>{m.inventory}</td>
                    <td>{m.median_price != null ? toman(m.median_price) : "—"}</td>
                    <td>{m.available ? "آماده" : m.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      )}
    </div>
  );
}
