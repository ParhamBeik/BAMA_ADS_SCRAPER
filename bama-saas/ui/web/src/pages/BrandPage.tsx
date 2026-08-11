import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, type Paginated } from "../api/client";
import { Async, Fa, toman } from "../ui";

export function BrandPage() {
  const { slug = "" } = useParams();
  const brand = useQuery({
    queryKey: ["brand", slug],
    queryFn: ({ signal }) => api.get<{ slug: string; name_fa: string }>(`/api/brands/${slug}/`, signal),
  });
  const models = useQuery({
    queryKey: ["brand-models", slug],
    queryFn: ({ signal }) => api.get<{ id: number; name_fa: string }[]>(`/api/brands/${slug}/models/`, signal),
  });
  const ads = useQuery({
    queryKey: ["brand-ads", slug],
    queryFn: ({ signal }) => api.get<Paginated<{ code: string; title: string; current_price: number | null; primary_image_url?: string }>>(`/api/ads/?brand=${encodeURIComponent(slug)}`, signal),
  });

  return (
    <div className="stack" dir="rtl">
      <Async query={brand}>{(b) => <h1><Fa>{b.name_fa}</Fa></h1>}</Async>
      <h2>مدل‌ها</h2>
      <Async query={models}>
        {(list) => (
          <div className="brand-grid">
            {list.map((m) => (
              <Link key={m.id} className="brand-chip" to={`/model/${m.id}`}><Fa>{m.name_fa}</Fa></Link>
            ))}
          </div>
        )}
      </Async>
      <h2>آگهی‌ها</h2>
      <Async query={ads}>
        {(page) => (
          <div className="card-grid">
            {page.results.map((ad) => (
              <Link key={ad.code} to={`/listing/${ad.code}`} className="listing-card">
                <div className="thumb">
                  {ad.primary_image_url ? <img src={ad.primary_image_url} alt="" loading="lazy" /> : <div className="thumb-fallback">—</div>}
                </div>
                <div className="listing-meta">
                  <strong><Fa>{ad.title}</Fa></strong>
                  <span>{ad.current_price != null ? toman(ad.current_price) : "—"}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </Async>
    </div>
  );
}
