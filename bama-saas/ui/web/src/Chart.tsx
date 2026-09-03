/**
 * One chart component, so every chart in the product shares an axis style, a
 * palette and a tooltip.
 *
 * Colours come from the CSS variables rather than being hardcoded, which is what
 * makes light and dark work without a second chart theme. The old app duplicated
 * hex values in JS next to the same values in CSS, and they had already drifted.
 *
 * Series colours are deliberately semantic: blue is the market series, green and
 * red mean direction, amber means a confidence warning. Reusing them decoratively
 * would make a legend meaningless.
 */
// Modular, not the umbrella package. `echarts` imported whole is 1.1 MB of
// JavaScript for a product that draws lines and bars: the whole build is
// pulled in — maps, treemaps, graphs, the WebGL renderer, every component —
// and none of it is reachable. Registering exactly what the option below uses
// drops the chart chunk by roughly 80% and makes adding a chart type a
// deliberate act rather than a free one.
// `esm/core`, not `lib/core`. The package has no `exports` map, so a deep
// specifier bypasses the `module` field and resolves to `lib/`, which is
// CommonJS — and the interop synthesised `default` as the whole exports object
// rather than honouring its `__esModule` marker, so this binding was
// `{default: EChartsReactCore, __esModule: true}`. Rendering that is React error
// #130, "element type is invalid: got object", and it took down every
// authenticated screen from the moment the chart first rendered. The bare
// `echarts-for-react` this replaced resolved through `module` to real ESM,
// which is why the deep path was the change that broke it.
import ReactEChartsCore from "echarts-for-react/esm/core";
import { BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  LineChart, BarChart,
  GridComponent, TooltipComponent, LegendComponent,
  // Canvas, not SVG: these charts redraw on theme change and on every filter,
  // and the series here are long enough that SVG node counts matter.
  CanvasRenderer,
]);
import { useMemo } from "react";
import { useTheme } from "./theme";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface Series {
  name: string;
  data: (number | null)[];
  type?: "line" | "bar";
  color?: string;
  area?: boolean;
}

/**
 * How the horizontal axis should read the values it is given.
 *
 * `category` is the default and the right answer for named buckets. It is the
 * wrong answer for anything measured, because it spaces every entry equally:
 * a 44-point daily series covering 55 calendar days drew its four multi-day
 * holes as ordinary one-day steps, and a survival curve's event times
 * (0.26, 0.27, 0.36, 1.05, … 9.98 days) were drawn as if evenly spaced, which
 * is a picture of the sort order rather than of time.
 */
export type AxisType = "category" | "time" | "value";

/** Gregorian ISO in, Jalali out — the calendar the rest of the app writes in. */
const JALALI = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
  month: "numeric",
  day: "numeric",
});

export function faDate(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : JALALI.format(date);
}

export function Chart({
  x,
  series,
  height = 260,
  yFormatter,
  stack,
  xType = "category",
  yMax,
}: {
  x: (string | number)[];
  series: Series[];
  height?: number;
  yFormatter?: (v: number) => string;
  stack?: boolean;
  xType?: AxisType;
  /** Hard ceiling for the value axis. A survival curve is a probability, and
   *  ECharts' padding ran the axis to 102% — a share of listings that cannot
   *  exist. */
  yMax?: number;
}) {
  const { resolved } = useTheme();

  const option = useMemo(() => {
    const text = cssVar("--text");
    const muted = cssVar("--muted");
    const border = cssVar("--border");
    const panel = cssVar("--panel");
    const palette = [cssVar("--accent"), cssVar("--up"), cssVar("--warn"), cssVar("--down")];

    return {
      grid: { left: 8, right: 12, top: 24, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        backgroundColor: panel,
        borderColor: border,
        textStyle: { color: text },
        valueFormatter: yFormatter,
      },
      legend: {
        show: series.length > 1,
        textStyle: { color: muted },
        top: 0,
        icon: "roundRect",
        itemHeight: 8,
      },
      xAxis: {
        type: xType,
        // A time or value axis positions each point by what it *is*, so it must
        // be handed pairs rather than a separate label list — the label list is
        // what made a four-day hole look like one day.
        data: xType === "category" ? x : undefined,
        axisLine: { lineStyle: { color: border } },
        axisLabel: {
          color: muted,
          // ISO dates are Gregorian, and this app writes ۱۴۰۵/۶/۶ everywhere
          // else; one chart axis in the other calendar makes the two
          // uncomparable by eye.
          formatter: xType === "time" ? (v: number) => faDate(v) : undefined,
        },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        // Bars encode magnitude by length, so their baseline must be zero or the
        // picture lies. Lines encode *change*, and forcing zero on an index
        // based at 100 flattened every real move into a straight line across the
        // top of the chart — a 3% shift and a 0.1% one drew identically.
        scale: yMax == null && series.every((s) => (s.type ?? "line") === "line"),
        max: yMax,
        splitLine: { lineStyle: { color: border, type: "dashed" } },
        axisLabel: { color: muted, formatter: yFormatter },
      },
      series: series.map((s, i) => ({
        name: s.name,
        type: s.type ?? "line",
        data: xType === "category"
          ? s.data
          : s.data.map((v, j) => [xType === "time" ? new Date(x[j]) : x[j], v]),
        stack: stack ? "total" : undefined,
        smooth: false,
        showSymbol: false,
        // A gap in the data is drawn as a gap. Connecting across it would invent
        // a trend through days the crawler never covered.
        connectNulls: false,
        lineStyle: { width: 2 },
        itemStyle: { color: s.color ?? palette[i % palette.length] },
        areaStyle: s.area ? { opacity: resolved === "dark" ? 0.16 : 0.08 } : undefined,
      })),
    };
  }, [x, series, yFormatter, stack, resolved, xType, yMax]);

  return (
    <ReactEChartsCore echarts={echarts}
      option={option}
      // Fixed height: charts that size themselves from their container reflow
      // when data arrives and shove the page around.
      style={{ height, width: "100%" }}
      notMerge
      // No `opts={{renderer:"svg"}}`. It was correct when this imported the
      // umbrella `echarts`, which registers both renderers; the modular
      // registration above declares Canvas only, so asking for SVG now names a
      // renderer that is not loaded. Canvas is the choice the registration above
      // argues for anyway — dropping the option is what makes the two agree.
    />
  );
}
