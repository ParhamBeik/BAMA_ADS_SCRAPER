import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, type Paginated } from "../api/client";
import { Async, Fa, toman } from "../ui";

export function ModelPage() {
  const { id = "" } = useParams();
  const ads = useQuery({
    queryKey: ["model-ads", id],
    queryFn: ({ signal }) => api.get<Paginated<{ code: string; title: string; current_price: number | null; primary_image_url?: string }>>(`/api/ads/?model=${id}`, signal),
  });

  return (
    <div className="stack" dir="rtl">
      <h1>مدل #{id}</h1>
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
