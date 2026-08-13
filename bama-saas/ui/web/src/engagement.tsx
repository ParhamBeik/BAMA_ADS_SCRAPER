import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Heart } from "lucide-react";
import { Link } from "react-router-dom";
import { api } from "./api/client";
import type { Paginated } from "./api/client";
import { useAuth } from "./auth";

interface Favorite {
  code: string;
}

export function ListingActions({ code }: { code: string }) {
  const { me } = useAuth();
  const client = useQueryClient();
  const favorites = useQuery({
    queryKey: ["favorites"],
    enabled: Boolean(me),
    queryFn: ({ signal }) => api.get<Paginated<Favorite>>("/api/favorites/", signal),
  });
  const saved = Boolean(favorites.data?.results.some((f) => f.code === code));

  const toggleFav = useMutation({
    mutationFn: async () => {
      if (saved) await api.del(`/api/favorites/${code}/`);
      else await api.post("/api/favorites/", { code });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["favorites"] }),
  });

  const addAlert = useMutation({
    mutationFn: () =>
      api.post("/api/alerts/", { alert_type: "price_drop", ad: code, threshold: 1 }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["alerts"] }),
  });

  if (!me) {
    return (
      <p className="muted">
        برای ذخیره یا هشدار قیمت <Link to="/login">وارد شوید</Link>.
      </p>
    );
  }

  return (
    <div className="segmented" role="group" aria-label="عملیات آگهی">
      <button
        className={saved ? "on" : ""}
        onClick={() => toggleFav.mutate()}
        disabled={toggleFav.isPending}
      >
        <Heart size={14} /> {saved ? "ذخیره شده" : "ذخیره"}
      </button>
      <button onClick={() => addAlert.mutate()} disabled={addAlert.isPending}>
        <Bell size={14} /> هشدار کاهش قیمت
      </button>
    </div>
  );
}
