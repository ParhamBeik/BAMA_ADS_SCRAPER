import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Heart } from "lucide-react";
import { api } from "./api/client";
import type { Paginated } from "./api/client";

interface Favorite {
  code: string;
}

/** Save / unsave one ad. Saving is the only write the product has left. */
export function ListingActions({ code }: { code: string }) {
  const client = useQueryClient();
  const favorites = useQuery({
    queryKey: ["favorites"],
    queryFn: ({ signal }) => api.get<Paginated<Favorite>>("/api/favorites/", signal),
  });
  const saved = Boolean(favorites.data?.results.some((f) => f.code === code));

  const toggle = useMutation({
    mutationFn: async () => {
      if (saved) await api.del(`/api/favorites/${code}/`);
      else await api.post("/api/favorites/", { code });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["favorites"] }),
  });

  return (
    <button
      className={saved ? "on" : ""}
      onClick={() => toggle.mutate()}
      disabled={toggle.isPending}
      aria-pressed={saved}
    >
      <Heart size={14} /> {saved ? "Saved" : "Save"}
    </button>
  );
}
