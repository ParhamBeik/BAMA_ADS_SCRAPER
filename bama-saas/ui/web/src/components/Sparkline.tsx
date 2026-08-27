/**
 * A movement shape, at table-row size.
 *
 * Hand-drawn SVG rather than a chart component: these appear once per row on a
 * leaderboard, and mounting ten ECharts instances to draw ten polylines costs
 * more than the whole rest of the page. There are no axes, no tooltip and no
 * interaction by design — the number beside it is the fact, this is only the
 * shape of how it got there.
 *
 * `direction` is passed rather than inferred so it always matches the change
 * figure the row prints, including the case where a series ends level with where
 * it started after a round trip.
 */
export function Sparkline({
  values,
  direction,
  width = 88,
  height = 24,
  label,
}: {
  values: (number | null)[];
  direction: "up" | "down" | "flat";
  width?: number;
  height?: number;
  label?: string;
}) {
  const points = values.filter((v): v is number => v != null);
  if (points.length < 2) return null;

  const low = Math.min(...points);
  const high = Math.max(...points);
  // A perfectly flat series has no range to scale into; draw it down the middle
  // rather than dividing by zero and collapsing every point onto the top edge.
  const span = high - low || 1;
  const step = width / (points.length - 1);
  const path = points
    .map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(1)},${(height - ((v - low) / span) * height).toFixed(1)}`)
    .join(" ");

  const stroke =
    direction === "up" ? "var(--up)" : direction === "down" ? "var(--down)" : "var(--muted)";

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      // The chart is decoration for a number that is already in the row; only
      // give it a label when it is standing on its own.
      role={label ? "img" : "presentation"}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      // Sparklines read left-to-right — time runs forward — even inside an RTL
      // page, the same way the Latin-digit prices do.
      style={{ direction: "ltr", overflow: "visible" }}
      className="flex-none"
    >
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.5}
            strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
