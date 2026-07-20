// Minimal Chart.js wrappers. Each render destroys the previous instance on the
// same canvas to avoid the "canvas already in use" leak during re-renders.
const INSTANCES = new WeakMap();

function destroy(canvas) {
  const c = INSTANCES.get(canvas);
  if (c) c.destroy();
}

const BASE = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { labels: { color: "#8b97a5", boxWidth: 12 } } },
  scales: {
    x: { ticks: { color: "#8b97a5", maxRotation: 0, autoSkip: true }, grid: { color: "rgba(255,255,255,.05)" } },
    y: { ticks: { color: "#8b97a5" }, grid: { color: "rgba(255,255,255,.05)" } },
  },
};

const COLORS = {
  blue: "#3b82f6", green: "#22c55e", amber: "#f59e0b", red: "#ef4444", purple: "#a855f7",
};

/** Toman axis: show "میلیارد" for billions so labels stay readable. */
function tomanAxis(scale) {
  return {
    ...scale,
    ticks: {
      ...scale.ticks,
      color: "#8b97a5",
      callback(v) {
        const n = Number(v);
        if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
        if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(0)}M`;
        return n;
      },
    },
  };
}

export function lineChart(canvas, { labels, datasets, yToman = false, title }) {
  destroy(canvas);
  const scales = { ...BASE.scales };
  if (yToman) scales.y = tomanAxis(scales.y);
  const chart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets: datasets.map((d, i) => ({ fill: false, tension: .3, borderWidth: 2, pointRadius: 2, ...d, borderColor: d.borderColor || Object.values(COLORS)[i % 5], backgroundColor: d.backgroundColor || Object.values(COLORS)[i % 5] })) },
    options: { ...BASE, scales, plugins: { ...BASE.plugins, tooltip: { callbacks: yToman ? { label: ctx => Number(ctx.raw).toLocaleString("fa-IR") + " تومان" } : {} }, title: title ? { display: true, text: title, color: "#e6edf3" } : {} } },
  });
  INSTANCES.set(canvas, chart);
  return chart;
}

export function barChart(canvas, { labels, datasets, yToman = false, stacked = false }) {
  destroy(canvas);
  const scales = { ...BASE.scales, ...(stacked ? { x: { ...BASE.scales.x, stacked: true }, y: { ...BASE.scales.y, stacked: true } } : {}) };
  if (yToman) scales.y = tomanAxis(BASE.scales.y);
  const chart = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: datasets.map((d, i) => ({ borderRadius: 4, maxBarThickness: 42, ...d, backgroundColor: d.backgroundColor || Object.values(COLORS)[i % 5] })) },
    options: { ...BASE, scales },
  });
  INSTANCES.set(canvas, chart);
  return chart;
}

export const chartColors = COLORS;
