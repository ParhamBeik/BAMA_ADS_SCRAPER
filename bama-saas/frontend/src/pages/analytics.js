// Analytics pages: cross-market analytics + dedicated price/inventory chart
// workspaces. Chart blocks are reused from blocks.js (no duplicated logic).
import { api } from "../api.js";
import { lineChart, barChart, chartColors } from "../charts.js";
import { priceChartsBlock, inventoryBlock, marketIndexBlock } from "./blocks.js";
import { dealTable, dropTable } from "./browse.js";
import { $, el, clear, append, card, stat, table, empty, badge, runPage, price, num, pct, date, ago } from "../ui.js";

const Ctx = () => document.getElementById("view");

async function loadMarkets() {
  return api.get("/api/markets/", { limit: 500 });
}

function modelPicker(markets, selectedId, onPick) {
  const sel = el("select", {}, [el("option", { value: "", text: "انتخاب مدل…" }), ...markets.map(m =>
    el("option", { value: m.model_id, text: `${m.brand_name} ${m.model_name}`, selected: String(m.model_id) === String(selectedId) }))]);
  sel.addEventListener("change", () => onPick(sel.value));
  return el("div", { class: "helpers" }, [el("div", {}, [el("label", { text: "مدل" }), sel])]);
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------
export async function analytics(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "تحلیل بازار";
  await runPage(view, async () => {
    const [brands, regional, dealers, drops, newest, oldest] = await Promise.all([
      api.get("/api/analytics/rankings/brands/", { limit: 10 }).catch(() => []),
      api.get("/api/analytics/regional/", { limit: 10 }).catch(() => []),
      api.get("/api/analytics/dealers/", { limit: 10 }).catch(() => []),
      api.get("/api/analytics/price-drops/", { days: 30, limit: 8 }).catch(() => []),
      api.get("/api/analytics/newest/", { limit: 6 }).catch(() => []),
      api.get("/api/analytics/oldest/", { limit: 6 }).catch(() => []),
    ]);
    const indexBlk = marketIndexBlock({ scope: "market", days: 90 });
    clear(view);
    append(view, [
      // Top of the page: the one number that says whether the market moved.
      indexBlk.node,
      card("پربازارترین برندها", [rankTable(brands, "name")]),
      el("div", { class: "grid cols-2" }, [
        card("شهرها (قیمت میانه)", [table(["شهر", "آگهی", "قیمت میانه"], regional, { renderRow: r => el("tr", {}, [el("td", { text: r.city_name }), el("td", { class: "mono", text: num(r.ad_count) }), el("td", { class: "mono", text: price(r.median_price) })]) })]),
        card("نمایندگی‌ها", [table(["نمایندگی", "آگهی", "قیمت میانه"], dealers, { renderRow: r => el("tr", {}, [el("td", { text: r.name }), el("td", { class: "mono", text: num(r.ad_count) }), el("td", { class: "mono", text: price(r.median_price) })]) })]),
      ]),
      el("div", { class: "grid cols-2" }, [
        card("جدیدترین آگهی‌ها", [listingTable(newest)]),
        card("قدیمی‌ترین آگهی‌ها", [listingTable(oldest)]),
      ]),
      card("کاهش قیمت‌ها (۳۰ روز)", [drops.length ? dropTable(drops) : empty()]),
    ]);
    // Chart.js needs the canvas in the document before it can draw.
    await indexBlk.render();
  });
}

function rankTable(rows, nameKey) {
  return table(["نام", "تعداد آگهی", "حداقل", "میانه", "حداکثر"], rows, {
    renderRow: r => el("tr", {}, [
      el("td", { text: r[nameKey] }),
      el("td", { class: "mono", text: num(r.ad_count) }),
      el("td", { class: "mono", text: price(r.min_price) }),
      el("td", { class: "mono", text: price(r.median_price) }),
      el("td", { class: "mono", text: price(r.max_price) }),
    ]),
  });
}

function listingTable(rows) {
  return table(["عنوان", "قیمت", "زمان انتشار"], rows, {
    renderRow: r => el("tr", {}, [
      el("td", {}, [el("a", { class: "linkrow", href: `#/ad/${r.code}` }, [document.createTextNode(r.title || r.code)])]),
      el("td", { class: "mono", text: price(r.current_price) }),
      el("td", { class: "muted", text: ago(r.publish_at) }),
    ]),
  });
}

// ---------------------------------------------------------------------------
// Price charts workspace
// ---------------------------------------------------------------------------
export async function priceCharts(view = Ctx(), params = {}) {
  document.getElementById("pageTitle").textContent = "نمودار قیمت";
  await runPage(view, async () => {
    const markets = await loadMarkets();
    const initial = params.model || (markets[0] && markets[0].model_id);
    clear(view);
    const pickerHost = el("div");
    const chartHost = el("div");
    append(view, [pickerHost, chartHost]);

    async function show(id) {
      clear(chartHost); chartHost.appendChild(el("div", { class: "spinner-wrap" }, [el("div", { class: "spinner" })]));
      const blk = priceChartsBlock(id);
      clear(chartHost); chartHost.appendChild(blk.node);
      await blk.render();
    }
    pickerHost.appendChild(modelPicker(markets, initial, id => id && show(id)));
    if (initial) show(initial);
  });
}

// ---------------------------------------------------------------------------
// Inventory charts workspace
// ---------------------------------------------------------------------------
export async function inventoryCharts(view = Ctx(), params = {}) {
  document.getElementById("pageTitle").textContent = "نمودار موجودی";
  await runPage(view, async () => {
    const [markets, overview] = await Promise.all([
      loadMarkets(),
      api.get("/api/analytics/market-overview/", { days: 120 }).catch(() => []),
    ]);
    const initial = params.model || (markets[0] && markets[0].model_id);
    clear(view);
    const ovCard = card("روند کلی بازار", [el("div", { class: "chart-box" }, [el("canvas", { id: "ovCanvas" })])]);
    append(view, [ovCard]);

    lineChart($("#ovCanvas"), {
      labels: overview.map(o => date(o.date)),
      datasets: [
        { label: "آگهی فعال", data: overview.map(o => o.active_count), borderColor: chartColors.blue },
        { label: "جدید", data: overview.map(o => o.new_count), borderColor: chartColors.green },
        { label: "حذف‌شده", data: overview.map(o => o.removed_count), borderColor: chartColors.red },
      ],
    });

    const pickerHost = el("div");
    const chartHost = el("div");
    append(view, [pickerHost, chartHost]);
    async function show(id) {
      const blk = inventoryBlock(id);
      clear(chartHost); chartHost.appendChild(blk.node);
      await blk.render();
    }
    pickerHost.appendChild(modelPicker(markets, initial, id => id && show(id)));
    if (initial) show(initial);
  });
}
