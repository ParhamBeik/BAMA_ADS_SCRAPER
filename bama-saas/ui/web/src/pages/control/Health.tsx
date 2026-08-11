import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Async } from "../../ui";

export function ControlHealth() {
  const q = useQuery({
    queryKey: ["admin-health"],
    queryFn: ({ signal }) => api.get<Record<string, unknown>>("/api/admin/health/", signal),
    refetchInterval: 30_000,
  });
  return (
    <div className="stack">
      <h1>سلامت سامانه</h1>
      <Async query={q}>
        {(data) => <pre className="card code-block">{JSON.stringify(data, null, 2)}</pre>}
      </Async>
    </div>
  );
}
