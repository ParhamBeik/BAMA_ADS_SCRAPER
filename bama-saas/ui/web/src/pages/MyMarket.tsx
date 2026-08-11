/**
 * My Market — the signed-in user's own saved work.
 *
 * Kept deliberately thin: favorites, watchlists and alerts already exist on the
 * backend and this is a surface for them, not a redesign of them.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
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
  id: number;
  name: string;
  is_active: boolean;
}

interface Notification {
  id: number;
  title: string;
  body: string;
  created_at: string;
  is_read: boolean;
}

export function MyMarket() {
  const client = useQueryClient();
  const { me } = useAuth();

  const favorites = useQuery({
    queryKey: ["favorites"],
    queryFn: ({ signal }) => api.get<Paginated<Favorite>>("/api/favorites/", signal),
  });
  const alerts = useQuery({
    queryKey: ["alerts"],
    queryFn: ({ signal }) => api.get<Paginated<Alert>>("/api/alerts/", signal),
  });
  const notifications = useQuery({
    queryKey: ["notifications"],
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
      <Card title="Saved cars">
        <Async query={favorites} empty="Nothing saved yet.">
          {(data) =>
            data.results.length ? (
              <Table head={["Car", "Price", ""]}>
                {data.results.map((f) => (
                  <tr key={f.id}>
                    <td>
                      <Fa>{f.ad_title ?? f.code}</Fa>
                    </td>
                    <td className="num">{toman(f.ad_price ?? null)}</td>
                    <td className="num">
                      <button
                        onClick={() => removeFavorite.mutate(f.code)}
                        aria-label="Remove"
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">Nothing saved yet.</div>
            )
          }
        </Async>
      </Card>

      <Card title="Alerts">
        <Async query={alerts} empty="No alerts configured.">
          {(data) =>
            data.results.length ? (
              <Table head={["Alert", "Status"]}>
                {data.results.map((a) => (
                  <tr key={a.id}>
                    <td>{a.name}</td>
                    <td className="num">
                      <span className={`badge ${a.is_active ? "accent" : ""}`}>
                        {a.is_active ? "on" : "off"}
                      </span>
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">No alerts configured.</div>
            )
          }
        </Async>
      </Card>

      <Card title="Notifications">
        <Async query={notifications} empty="Nothing new.">
          {(data) =>
            data.results.length ? (
              <Table head={["Message", "When"]}>
                {data.results.map((n) => (
                  <tr key={n.id}>
                    <td>
                      <strong>{n.title}</strong>
                      <div className="stat-sub">{n.body}</div>
                    </td>
                    <td className="num">
                      {new Date(n.created_at).toLocaleDateString("en-GB")}
                    </td>
                  </tr>
                ))}
              </Table>
            ) : (
              <div className="state">Nothing new.</div>
            )
          }
        </Async>
      </Card>
    </div>
  );
}
