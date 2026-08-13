import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Paginated } from "../api/client";
import { Async, Fa, toman } from "../ui";

type Brand = { slug: string; name_fa: string };
type Ad = {
  code: string;
  title: string;
  brand_name: string;
  model_name: string;
  current_price: number | null;
  primary_image_url?: string;
  year_jalali?: number | null;
};

type Overview = {
  active_listings?: number;
  brands?: number;
  models?: number;
};

export function Landing() {
  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) => api.get<Brand[]>("/api/brands/", signal),
  });
  const ads = useQuery({
    queryKey: ["ads", { page: 1 }],
    queryFn: ({ signal }) => api.get<Paginated<Ad>>("/api/ads/?page=1", signal),
  });
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => api.get<Overview>("/api/analytics/overview/", signal),
  });

  return (
    <div className="landing" dir="rtl">
      <section className="hero card">
        <h1>بازار خودروی ایران، با شواهد</h1>
        <p className="muted">جستجو، مقایسه، و ارزیابی قیمت روی داده‌ی زنده باما.</p>
        <form className="search-bar" action="/explore" method="get">
          <input name="q" placeholder="برند، مدل یا ویژگی…" aria-label="جستجو" />
          <button className="btn primary" type="submit">جستجو</button>
        </form>
        <div className="stat-row">
          <span>{overview.data?.active_listings?.toLocaleString("en-US") ?? "—"} آگهی</span>
          <span>{overview.data?.brands ?? "—"} برند</span>
          <span>{overview.data?.models ?? "—"} مدل</span>
        </div>
      </section>

      <section>
        <h2>برندهای محبوب</h2>
        <Async query={brands}>
          {(list) => (
            <div className="brand-grid">
              {list.slice(0, 12).map((b) => (
                <Link key={b.slug} to={`/brand/${b.slug}`} className="brand-chip">
                  <Fa>{b.name_fa}</Fa>
                </Link>
              ))}
            </div>
          )}
        </Async>
      </section>

      <section>
        <h2>تازه‌ترین آگهی‌ها</h2>
        <Async query={ads}>
          {(page) => (
            <div className="card-grid">
              {page.results.slice(0, 8).map((ad) => (
                <Link key={ad.code} to={`/listing/${ad.code}`} className="listing-card">
                  <div className="thumb">
                    {ad.primary_image_url ? (
                      <img src={ad.primary_image_url} alt="" loading="lazy" onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                    ) : (
                      <div className="thumb-fallback">بدون تصویر</div>
                    )}
                  </div>
                  <div className="listing-meta">
                    <strong><Fa>{ad.title || `${ad.brand_name} ${ad.model_name}`}</Fa></strong>
                    <span>{ad.current_price != null ? toman(ad.current_price) : "—"}</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Async>
      </section>
    </div>
  );
}
