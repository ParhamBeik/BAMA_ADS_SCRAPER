/**
 * My Market — favorites, alerts, and notifications.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth";
import type { Paginated } from "../api/client";
import { Async, Card, Fa, Table, toman } from "../ui";

interface Favorite {
  id: number;
  code: string;
  ad_title?: string;
  ad_price?: number | null;
}

interface Alert {
  id: string;
  alert_type: string;
  enabled: boolean;
}

interface Notification {
  id: string;
  subject: string;
  body: string;
  created_at: string;
}

export function MyMarket() {
  const client = useQueryClient();
  const { me } = useAuth();

  const favorites = useQuery({
    queryKey: ["favorites"],
    enabled: Boolean(me),
    queryFn: ({ signal }) => api.get<Paginated<Favorite>>("/api/favorites/", signal),
  });
  const alerts = useQuery({
    queryKey: ["alerts"],
    enabled: Boolean(me),
    queryFn: ({ signal }) => api.get<Paginated<Alert>>("/api/alerts/", signal),
  });
  const notifications = useQuery({
    queryKey: ["notifications"],
    enabled: Boolean(me),
    queryFn: ({ signal }) =>
      api.get<Paginated<Notification>>("/api/notifications/", signal),
  });

  const removeFavorite = useMutation({
    mutationFn: (code: string) => api.del(`/api/favorites/${code}/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["favorites"] }),
  });

  if (!me) {
    return (
      <Card title="بازار من">
        <div className="state" dir="rtl">برای مشاهده علاقه‌مندی‌ها وارد شوید.</div>
      </Card>
    );
  }

  return (
    <div className="grid cols-2">
      <Card title="آگهی‌های ذخیره‌شده">
        <Async query={favorites} empty="هنوز چیزی ذخیره نشده.">
          {(data) =>
            data.results.length ? (
              <Table head={["آگهی", "قیمت", ""]}>
                {data.results.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <Link to={`/listing/${f.code}`}><Fa>{f.ad_title ?? f.code}</Fa></Link>
                    </td>
                    <td className="num">{toman(f.ad_price ?? null)}</td>
                    <td className="num">
                      <button
                        onClick={() => removeFavorite.mutate(f.code)}
                        aria-label="حذف"
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">هنوز چیزی ذخیره نشده.</div>
            )
          }
        </Async>
      </Card>

      <Card title="هشدارها">
        <Async query={alerts} empty="هشداری تنظیم نشده.">
          {(data) =>
            data.results.length ? (
              <Table head={["نوع", "وضعیت"]}>
                {data.results.map((a) => (
                  <tr key={a.id}>
                    <td>{a.alert_type}</td>
                    <td className="num">
                      <span className={`badge ${a.enabled ? "accent" : ""}`}>
                        {a.enabled ? "فعال" : "خاموش"}
                      </span>
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">هشداری تنظیم نشده.</div>
            )
          }
        </Async>
      </Card>

      <Card title="اعلان‌ها">
        <Async query={notifications} empty="اعلان تازه‌ای نیست.">
          {(data) =>
            data.results.length ? (
              <Table head={["پیام", "زمان"]}>
                {data.results.map((n) => (
                  <tr key={n.id}>
                    <td>
                      <strong>{n.subject}</strong>
                      <div className="stat-sub">{n.body}</div>
                    </td>
                    <td className="num">
                      {new Date(n.created_at).toLocaleDateString("fa-IR")}
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">اعلان تازه‌ای نیست.</div>
            )
          }
        </Async>
      </Card>
    </div>
  );
}
