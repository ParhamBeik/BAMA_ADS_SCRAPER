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

// The composition-controlled index. Worth its own block rather than another
// line on the price chart: it answers a different question from the median —
// "did prices move" instead of "what does a car cost" — and the two disagree
// sharply whenever the mix of listings shifts.
export function marketIndexBlock({ scope = "market", id = null, days = 90 } = {}) {
  const node = card("شاخص قیمت بازار (پایه ۱۰۰)", [
    el("div", { class: "muted", id: "miSummary", text: "در حال بارگذاری…" }),
    el("div", { class: "chart-box" }, [el("canvas", { id: "miChart" })]),
  ]);
  async function render() {
    const params = { scope, days };
    if (id) params.id = id;
    const res = await api.get("/api/analytics/market-index/", params)
      .catch(() => ({ series: [] }));
    const s = res.series || [];
    const summary = $("#miSummary");
    if (!s.length) {
      summary.textContent = "داده‌ای برای این بازه موجود نیست.";
      return;
    }
    const pct = res.change_pct ?? 0;
    const dir = pct > 0 ? "رشد" : pct < 0 ? "افت" : "بدون تغییر";
    summary.textContent =
      `${dir} ${Math.abs(pct).toFixed(2)}٪ در ${s.length} روز — ` +
      `بر پایه ${s[s.length - 1].cohort_count} گروه هم‌رده. ` +
      `این شاخص تنها تغییر قیمت را می‌سنجد، نه تغییر ترکیب آگهی‌ها.`;
    lineChart($("#miChart"), {
      labels: s.map(x => date(x.date)),
      datasets: [
        { label: "شاخص قیمت", data: s.map(x => x.index_value), borderColor: chartColors.blue },
      ],
    });
  }
  return { node, render };
}
