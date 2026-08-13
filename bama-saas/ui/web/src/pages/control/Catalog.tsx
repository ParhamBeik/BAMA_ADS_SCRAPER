import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async, Fa } from "../../ui";

type Page<T> = { count: number; page: number; results: T[] };
type Brand = {
  slug: string; name_fa: string; is_confirmed: boolean;
  ad_count: number; active_ad_count: number;
};
type ModelRow = {
  id: number; brand_slug: string; brand_name: string; name_fa: string;
  is_confirmed: boolean; ad_count: number; active_ad_count: number;
};
type VariantRow = {
  id: number; model_id: number; model_name: string; name_fa: string;
  ad_count: number; active_ad_count: number;
};

export function ControlCatalog() {
  const [brand, setBrand] = useState<string | null>(null);
  const [model, setModel] = useState<number | null>(null);

  const brands = useQuery({
    queryKey: ["inspect-brands"],
    queryFn: ({ signal }) => api.get<Page<Brand>>("/api/admin/inspect/brands/?page_size=200", signal),
  });
  const models = useQuery({
    queryKey: ["inspect-models", brand],
    queryFn: ({ signal }) => api.get<Page<ModelRow>>(`/api/admin/inspect/models/?brand=${brand}&page_size=200`, signal),
    enabled: !!brand,
  });
  const variants = useQuery({
    queryKey: ["inspect-variants", model],
    queryFn: ({ signal }) => api.get<Page<VariantRow>>(`/api/admin/inspect/variants/?model=${model}&page_size=200`, signal),
    enabled: model != null,
  });

  return (
    <div className="stack">
      <h1>Catalog</h1>
      <p className="muted">Brand → Model → Variant. Counts are catalog_ad rows including removed.</p>
      <h2>Brand</h2>
      <Async query={brands}>
        {(data) => (
          <table className="table inspect-table">
            <thead>
              <tr><th>slug</th><th>name_fa</th><th>is_confirmed</th><th>ad_count</th><th>active_ad_count</th></tr>
            </thead>
            <tbody>
              {data.results.map((b) => (
                <tr key={b.slug} className={`clickable${brand === b.slug ? " selected" : ""}`} onClick={() => { setBrand(b.slug); setModel(null); }}>
                  <td><code>{b.slug}</code></td>
                  <td><Fa>{b.name_fa}</Fa></td>
                  <td>{String(b.is_confirmed)}</td>
                  <td>{b.ad_count}</td>
                  <td>{b.active_ad_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Async>
      {brand && (
        <>
          <h2>Model <code>{brand}</code> <Link to={`/control/ads?brand=${brand}`}>ads</Link></h2>
          <Async query={models}>
            {(data) => (
              <table className="table inspect-table">
                <thead>
                  <tr><th>id</th><th>name_fa</th><th>is_confirmed</th><th>ad_count</th><th>active_ad_count</th></tr>
                </thead>
                <tbody>
                  {data.results.map((m) => (
                    <tr key={m.id} className={`clickable${model === m.id ? " selected" : ""}`} onClick={() => setModel(m.id)}>
                      <td>{m.id}</td>
                      <td><Fa>{m.name_fa}</Fa></td>
                      <td>{String(m.is_confirmed)}</td>
                      <td>{m.ad_count}</td>
                      <td>{m.active_ad_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Async>
        </>
      )}
      {model != null && (
        <>
          <h2>Variant model_id={model} <Link to={`/control/ads?model=${model}`}>ads</Link></h2>
          <Async query={variants}>
            {(data) => (
              <table className="table inspect-table">
                <thead>
                  <tr><th>id</th><th>name_fa</th><th>ad_count</th><th>active_ad_count</th></tr>
                </thead>
                <tbody>
                  {data.results.map((v) => (
                    <tr key={v.id}>
                      <td>{v.id}</td>
                      <td><Fa>{v.name_fa}</Fa></td>
                      <td>{v.ad_count}</td>
                      <td>{v.active_ad_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Async>
        </>
      )}
    </div>
  );
}
