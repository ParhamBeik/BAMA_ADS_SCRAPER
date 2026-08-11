import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async, Fa } from "../../ui";

export function ControlReview() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["admin-review"],
    queryFn: ({ signal }) => api.get<{
      unconfirmed_brands: { slug: string; name_fa: string }[];
      unconfirmed_models: { id: number; name_fa: string }[];
      recent_rejects: { code: string; rule: string }[];
    }>("/api/admin/review/", signal),
  });

  async function confirm(kind: string, id: string | number) {
    await api.post("/api/admin/review/confirm/", { kind, id });
    await qc.invalidateQueries({ queryKey: ["admin-review"] });
  }

  return (
    <div className="stack">
      <h1>صف بازبینی</h1>
      <Async query={q}>
        {(data) => (
          <>
            <h2>برندهای تأییدنشده</h2>
            <ul>{data.unconfirmed_brands.map((b) => (
              <li key={b.slug}><Fa>{b.name_fa}</Fa> <button className="btn" onClick={() => confirm("brand", b.slug)}>تأیید</button></li>
            ))}</ul>
            <h2>مدل‌های تأییدنشده</h2>
            <ul>{data.unconfirmed_models.map((m) => (
              <li key={m.id}><Fa>{m.name_fa}</Fa> <button className="btn" onClick={() => confirm("model", m.id)}>تأیید</button></li>
            ))}</ul>
            <h2>ردهای اخیر</h2>
            <ul>{data.recent_rejects.map((r, i) => (
              <li key={i}>{r.code}: {r.rule}</li>
            ))}</ul>
          </>
        )}
      </Async>
    </div>
  );
}
