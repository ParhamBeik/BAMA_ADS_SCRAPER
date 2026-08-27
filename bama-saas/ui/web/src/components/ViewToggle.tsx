/**
 * Cards or table, for the two screens that list cars.
 *
 * Shared because the deal board and the explorer had two copies that had already
 * drifted — one rendered icons, the other rendered the words "کارتی" and
 * "جدولی", and only one of them labelled its buttons for a screen reader.
 *
 * The choice lives in the URL like every other view setting, so a shared link
 * arrives showing what the sender was looking at.
 */
import { LayoutGrid, List } from "lucide-react";
import { useFilters } from "@/filters";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

export type ListView = "cards" | "table";

export function useListView(): ListView {
  return useFilters().get("view") === "table" ? "table" : "cards";
}

export function ViewToggle() {
  const filters = useFilters();
  const view = useListView();
  return (
    <ToggleGroup
      type="single"
      value={view}
      // Cards is the default, so it is stored as the absence of a parameter
      // rather than as `view=cards` — and an empty value here would otherwise
      // leave the list with no view at all.
      onValueChange={(next) => next && filters.set({ view: next === "cards" ? null : next })}
      variant="outline"
      size="sm"
      aria-label="نحوه نمایش"
    >
      <ToggleGroupItem value="cards" aria-label="نمایش کارتی" title="نمایش کارتی">
        <LayoutGrid className="size-4" />
      </ToggleGroupItem>
      <ToggleGroupItem value="table" aria-label="نمایش جدولی" title="نمایش جدولی">
        <List className="size-4" />
      </ToggleGroupItem>
    </ToggleGroup>
  );
}
