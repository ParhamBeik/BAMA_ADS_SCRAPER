// Reusable per-model chart blocks. Each returns { node, render }: the caller
// appends `node` to the DOM, then awaits `render()` (Chart.js needs the canvas
// present in the document before it can draw). Used by the Model page and the
// dedicated Price-Charts / Inventory-Charts pages — single source of truth.
import { api } from "../api.js";
import { lineChart, chartColors } from "../charts.js";
import { $, el, card, date } from "../ui.js";

export function priceChartsBlock(modelId) {
  const node = el("div", { class: "grid cols-2" }, [
    card("روند قیمت (میانه ماهانه)", [el("div", { class: "chart-box" }, [el("canvas", { id: "pcTrend" })])]),
    card("نوار نوسان قیمت (Bollinger)", [el("div", { class: "chart-box" }, [el("canvas", { id: "pcBoll" })])]),
  ]);
  async function render() {
    const [trends, boll] = await Promise.all([
      api.get(`/api/markets/${modelId}/price-trends/`, { bucket: "month" }).catch(() => ({ series: [] })),
      api.get(`/api/markets/${modelId}/bollinger/`).catch(() => ({ series: [], bands: [] })),
    ]);
    const s = trends.series || [];
    lineChart($("#pcTrend"), { labels: s.map(x => x.bucket), yToman: true, datasets: [
      { label: "میانه", data: s.map(x => x.median) },
      { label: "میانگین", data: s.map(x => x.mean) },
    ] });
    const b = boll.series || boll.bands || [];
    lineChart($("#pcBoll"), { labels: b.map(x => x.bucket || x.date || x.t), yToman: true, datasets: [
      { label: "حد بالا", data: b.map(x => x.upper), borderColor: chartColors.amber },
      { label: "میانگین متحرک", data: b.map(x => x.mean ?? x.median ?? x.middle), borderColor: chartColors.blue },
      { label: "حد پایین", data: b.map(x => x.lower), borderColor: chartColors.green },
    ] });
  }
  return { node, render };
}

export function inventoryBlock(modelId, days = 90) {
  const node = card(`موجودی بازار (${days} روز)`, [el("div", { class: "chart-box" }, [el("canvas", { id: "invChart" })])]);
  async function render() {
    const inv = await api.get(`/api/analytics/inventory-trends/${modelId}/`, { days }).catch(() => ({ series: [] }));
    const i = inv.series || [];
    lineChart($("#invChart"), { labels: i.map(x => date(x.date)), datasets: [
      { label: "آگهی", data: i.map(x => x.ad_count), borderColor: chartColors.blue },
      { label: "جدید", data: i.map(x => x.new_count), borderColor: chartColors.green },
    ] });
  }
  return { node, render };
}
