// Browse flow: dashboard, search, market, brand, model, variant, ad detail.
import { api, pageOf } from "../api.js";
import { isLoggedIn } from "../auth.js";
import { lineChart, barChart, chartColors } from "../charts.js";
import { priceChartsBlock, inventoryBlock } from "./blocks.js";
import {
  $, el, clear, append, card, stat, table, empty, badge, runPage, toast,
  price, num, pct, date, datetime, ago, miles, adRow,
} from "../ui.js";

const Ctx = () => document.getElementById("view");

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
export async function dashboard(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "داشبورد";
  await runPage(view, async () => {
    const [markets, overview, deals, drops] = await Promise.all([
      api.get("/api/markets/", { limit: 500 }),
      api.get("/api/analytics/market-overview/", { days: 60 }),
      api.get("/api/analytics/deal-scores/", { limit: 6 }).catch(() => []),
      api.get("/api/analytics/price-drops/", { days: 14, limit: 6 }).catch(() => []),
    ]);
    const totalAds = markets.reduce((s, m) => s + m.ad_count, 0);
    const latest = overview[overview.length - 1] || {};
    const medianNow = latest.median_price;

    clear(view);
    append(view, [
      el("div", { class: "grid cols-4" }, [
        stat("آگهی‌های فعال", num(latest.active_count || totalAds), `از ${num(markets.length)} مدل`),
        stat("میانگین قیمت بازار", price(medianNow), "میانه روز جاری"),
        stat("مدل‌های بازار", num(markets.length), "دسته فعال"),
        stat("کاهش قیمت اخیر", num(drops.length), "۱۴ روز گذشته"),
      ]),
      el("div", { class: "grid cols-2" }, [
        card("روند بازار (۶۰ روز)", [el("div", { class: "chart-box" }, [el("canvas", { id: "cOverview" })])]),
        card("پرطرفدارترین مدل‌ها", [
          table(["مدل", "برند", "آگهی", "حداقل قیمت", "میانگین"], markets.slice(0, 8), {
            renderRow: m => el("tr", {}, [
              el("td", {}, [el("a", { class: "linkrow", href: `#/model/${m.model_id}` }, [document.createTextNode(m.model_name)])]),
              el("td", { text: m.brand_name }),
              el("td", { class: "mono", text: num(m.ad_count) }),
              el("td", { class: "mono", text: price(m.min_price) }),
              el("td", { class: "mono", text: price(m.avg_price) }),
            ]),
          }),
        ]),
      ]),
      el("div", { class: "grid cols-2" }, [
        card("بهترین معاملات (Deal Score)", [deals.length ? dealTable(deals) : empty()]),
        card("کاهش قیمت‌های اخیر", [drops.length ? dropTable(drops) : empty()]),
      ]),
    ]);

    lineChart($("#cOverview"), {
      labels: overview.map(o => date(o.date)),
      yToman: true,
      datasets: [
        { label: "میانه قیمت", data: overview.map(o => o.median_price), borderColor: chartColors.blue },
        { label: "آگهی فعال", data: overview.map(o => o.active_count), borderColor: chartColors.green, yAxisID: "y" },
      ],
    });
  });
}

export function dealTable(deals) {
  return table(["عنوان", "امتیاز", "تخفیف", "قیمت", "سال"], deals, {
    renderRow: d => el("tr", {}, [
      el("td", {}, [el("a", { class: "linkrow", href: `#/ad/${d.code}` }, [document.createTextNode(`${d.brand_name || ""} ${d.model_name || d.title || d.code}`)])]),
      el("td", {}, [badge(num(d.score?.toFixed(1)), "green")]),
      el("td", { class: "mono", text: pct(d.discount_pct) }),
      el("td", { class: "mono", text: price(d.price) }),
      el("td", { class: "mono", text: d.year }),
    ]),
  });
}

export function dropTable(drops) {
  return table(["عنوان", "از", "به", "کاهش", "زمان"], drops, {
    renderRow: d => el("tr", {}, [
      el("td", {}, [el("a", { class: "linkrow", href: `#/ad/${d.code}` }, [document.createTextNode(d.title || d.code)])]),
      el("td", { class: "mono muted", text: price(d.old_price) }),
      el("td", { class: "mono", text: price(d.new_price) }),
      el("td", {}, [badge(`-${pct(d.drop_pct)}`, "green")]),
      el("td", { class: "muted", text: ago(d.observed_at) }),
    ]),
  });
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
export async function search(view = Ctx(), params = {}) {
  document.getElementById("pageTitle").textContent = "جستجوی آگهی";
  const state = { brand: params.brand || "", model: params.model || "", page: 1, ...readSearchState() };
  await runPage(view, async () => {
    const brands = await api.get("/api/brands/");
    await renderSearch(view, brands, state);
  });
}

function readSearchState() {
  try { return JSON.parse(sessionStorage.getItem("bama.search") || "{}"); } catch { return {}; }
}
function writeSearchState(s) { sessionStorage.setItem("bama.search", JSON.stringify(s)); }

async function renderSearch(view, brands, state) {
  clear(view);
  const helpers = el("div", { class: "helpers" });
  const brandSel = el("select", {}, [el("option", { value: "", text: "همه برندها" }), ...brands.map(b => el("option", { value: b.slug, text: b.name_fa, selected: state.brand === b.slug }))]);
  const modelSel = el("select", {}, [el("option", { value: "", text: "همه مدل‌ها" })]);
  const yearMin = el("input", { type: "number", placeholder: "سال از", value: state.year_min || "" });
  const yearMax = el("input", { type: "number", placeholder: "تا", value: state.year_max || "" });
  const priceMin = el("input", { type: "number", placeholder: "قیمت از (تومان)", value: state.price_min || "" });
  const priceMax = el("input", { type: "number", placeholder: "تا", value: state.price_max || "" });
  const mileageMax = el("input", { type: "number", placeholder: "کارکرد نهایت (km)", value: state.mileage_max || "" });
  const trans = el("select", {}, [el("option", { value: "", text: "گیربکس" }), el("option", { value: "اتوماتیک", text: "اتوماتیک", selected: state.transmission === "اتوماتیک" }), el("option", { value: "دستی", text: "دستی", selected: state.transmission === "دستی" })]);
  const sortSel = el("select", {}, [
    el("option", { value: "-publish_at", text: "جدیدترین" }),
    el("option", { value: "-current_price", text: "گران‌ترین" }),
    el("option", { value: "current_price", text: "ارزان‌ترین" }),
    el("option", { value: "-year", text: "جدیدترین سال" }),
    el("option", { value: "mileage", text: "کم‌کارکردترین" }),
  ]);
  sortSel.value = state.sorting || "-publish_at";
  const submit = el("button", { class: "btn", text: "جستجو" });

  append(helpers, [brandSel, modelSel, yearMin, yearMax, priceMin, priceMax, mileageMax, trans, sortSel, submit]);
  const results = el("div");
  append(view, [helpers, results]);

  async function loadModels(slug) {
    clear(modelSel); modelSel.appendChild(el("option", { value: "", text: "همه مدل‌ها" }));
    if (!slug) return;
    try {
      const models = await api.get(`/api/brands/${slug}/models/`);
      for (const m of models) modelSel.appendChild(el("option", { value: m.id, text: m.name_fa, selected: String(state.model) === String(m.id) }));
    } catch { /* ignore */ }
  }
  brandSel.addEventListener("change", () => loadModels(brandSel.value));
  if (state.brand) loadModels(state.brand);

  async function doSearch(page = 1) {
    state.page = page;
    state.brand = brandSel.value; state.model = modelSel.value;
    state.year_min = yearMin.value; state.year_max = yearMax.value;
    state.price_min = priceMin.value; state.price_max = priceMax.value;
    state.mileage_max = mileageMax.value; state.transmission = trans.value; state.sorting = sortSel.value;
    writeSearchState(state);
    clear(results); results.appendChild(empty("در حال جستجو..."));
    const data = await api.get("/api/ads/", {
      brand: state.brand || undefined, model: state.model || undefined,
      year_min: state.year_min || undefined, year_max: state.year_max || undefined,
      price_min: state.price_min || undefined, price_max: state.price_max || undefined,
      mileage_max: state.mileage_max || undefined, transmission: state.transmission || undefined,
      ordering: state.sorting, page: state.page, page_size: 25,
    });
    const { items, count } = pageOf(data);
    clear(results);
    append(results, [
      el("div", { class: "muted", text: `${num(count)} آگهی` }),
      table(["عنوان", "مدل", "سال", "کارکرد", "قیمت", "زمان"], items, { renderRow: a => adRow(a) }),
      pager(count, state.page, 25, p => doSearch(p)),
    ]);
  }
  submit.addEventListener("click", () => doSearch(1));
  doSearch(state.page || 1);
}

export function pager(count, page, size, onNav) {
  const pages = Math.ceil(count / size) || 1;
  const node = el("div", { class: "row", style: "margin-top:12px" });
  node.appendChild(el("span", { class: "muted", text: `صفحه ${num(page)} از ${num(pages)}` }));
  if (page > 1) node.appendChild(el("button", { class: "btn sm secondary", text: "قبلی", onclick: () => onNav(page - 1) }));
  if (page < pages) node.appendChild(el("button", { class: "btn sm", text: "بعدی", onclick: () => onNav(page + 1) }));
  return node;
}

// ---------------------------------------------------------------------------
// Markets landing
// ---------------------------------------------------------------------------
export async function market(view = Ctx(), params = {}) {
  document.getElementById("pageTitle").textContent = "بازار خودرو";
  await runPage(view, async () => {
    const q = params.q || "";
    const markets = await api.get("/api/markets/", { limit: 500 });
    const filtered = q ? markets.filter(m => `${m.brand_name} ${m.model_name}`.includes(q)) : markets;
    clear(view);
    append(view, [
      el("p", { class: "muted", text: `${num(markets.length)} مدل فعال — برای جزئیات بازار هر مدل روی نام آن کلیک کنید.` }),
      table(["مدل", "برند", "تعداد آگهی", "حداقل", "میانگین", "حداکثر"], filtered, {
        renderRow: m => el("tr", {}, [
          el("td", {}, [el("a", { class: "linkrow", href: `#/model/${m.model_id}` }, [document.createTextNode(m.model_name)])]),
          el("td", {}, [el("a", { class: "linkrow", href: `#/brand/${m.brand_slug}` }, [document.createTextNode(m.brand_name)])]),
          el("td", { class: "mono", text: num(m.ad_count) }),
          el("td", { class: "mono", text: price(m.min_price) }),
          el("td", { class: "mono", text: price(m.avg_price) }),
          el("td", { class: "mono", text: price(m.max_price) }),
        ]),
      }),
    ]);
  });
}

// ---------------------------------------------------------------------------
// Brand
// ---------------------------------------------------------------------------
export async function brand(view = Ctx(), params = {}) {
  const slug = params.slug;
  await runPage(view, async () => {
    const [info, markets] = await Promise.all([
      api.get(`/api/brands/${slug}/`).catch(() => null),
      api.get("/api/markets/", { limit: 500 }),
    ]);
    const name = info?.name_fa || slug;
    document.getElementById("pageTitle").textContent = `برند ${name}`;
    const models = markets.filter(m => m.brand_slug === slug);
    clear(view);
    append(view, [
      el("p", { class: "muted", text: `${num(models.length)} مدل برای این برند` }),
      table(["مدل", "تعداد آگهی", "حداقل قیمت", "میانگین", "حداکثر"], models, {
        renderRow: m => el("tr", {}, [
          el("td", {}, [el("a", { class: "linkrow", href: `#/model/${m.model_id}` }, [document.createTextNode(m.model_name)])]),
          el("td", { class: "mono", text: num(m.ad_count) }),
          el("td", { class: "mono", text: price(m.min_price) }),
          el("td", { class: "mono", text: price(m.avg_price) }),
          el("td", { class: "mono", text: price(m.max_price) }),
        ]),
      }),
    ]);
  });
}

// ---------------------------------------------------------------------------
// Model — the rich analytics surface for one model
// ---------------------------------------------------------------------------
export async function modelPage(view = Ctx(), params = {}) {
  const id = params.id;
  await runPage(view, async () => {
    const [truemean, tom, deals, variants] = await Promise.all([
      api.get(`/api/markets/${id}/true-mean/`).catch(() => ({})),
      api.get(`/api/analytics/time-on-market/${id}/`).catch(() => ({})),
      api.get("/api/analytics/deal-scores/", { model: id, limit: 8 }).catch(() => []),
      api.get(`/api/models/${id}/variants/`).catch(() => []),
    ]);
    const modelName = truemean.model_name || `مدل ${id}`;
    const brandName = truemean.brand_name || "";
    document.getElementById("pageTitle").textContent = `${brandName} ${modelName}`.trim();

    const priceBlk = priceChartsBlock(id);
    const invBlk = inventoryBlock(id);

    clear(view);
    append(view, [
      el("div", { class: "grid cols-4" }, [
        stat("ارزش واقعی بازار", price(truemean.mean || truemean.median), `میانه: ${price(truemean.median)} · بازه منصفانه: ${price(truemean.fair_min)}`, ""),
        stat("نمونه‌های بررسی‌شده", num(truemean.count_after), `از ${num(truemean.count_before)} پس از حذف پرت`),
        stat("میانگین روزهای نمایش", num(tom.avg_days_listed), `میانه ${num(tom.median_days_listed)} روز`),
        stat("حذف‌شده از فهرست", num(tom.removed_count), `میانه ${num(tom.median_days_to_delist)} روز تا حذف`),
      ]),
      priceBlk.node,
      el("div", { class: "grid cols-2" }, [
        invBlk.node,
        variants.length ? card("تیپ‌ها (Variant)", [table(["تیپ", "جزئیات"], variants.slice(0, 12), { renderRow: v => el("tr", {}, [el("td", { text: v.name_fa }), el("td", {}, [el("a", { class: "linkrow", href: `#/variant/${v.id}`, text: "مشاهده آگهی‌ها" })])]) })]) : empty(),
      ]),
      card("بهترین معاملات این مدل", [deals.length ? dealTable(deals) : empty()]),
    ]);

    await Promise.all([priceBlk.render(), invBlk.render()]);
  });
}

// ---------------------------------------------------------------------------
// Variant
// ---------------------------------------------------------------------------
export async function variant(view = Ctx(), params = {}) {
  const id = params.id;
  await runPage(view, async () => {
    const data = await api.get("/api/ads/", { variant: id, page_size: 50, ordering: "current_price" });
    const { items } = pageOf(data);
    const sample = items[0] || {};
    document.getElementById("pageTitle").textContent = `${sample.brand_name || ""} ${sample.model_name || ""} ${sample.variant_name || ""}`.trim() || `تیپ ${id}`;
    clear(view);
    append(view, [
      el("p", { class: "muted", text: `${num(data.count)} آگهی برای این تیپ` }),
      table(["عنوان", "سال", "کارکرد", "قیمت", "شهر", "زمان"], items, {
        renderRow: a => el("tr", {}, [
          el("td", {}, [el("a", { class: "linkrow", href: `#/ad/${a.code}` }, [document.createTextNode(a.title || a.code)])]),
          el("td", { class: "mono", text: a.year }),
          el("td", { class: "mono", text: miles(a.mileage) }),
          el("td", { class: "mono", text: price(a.current_price) }),
          el("td", { text: a.city_name }),
          el("td", { class: "muted", text: ago(a.publish_at) }),
        ]),
      }),
      sample.model_id ? el("a", { class: "btn secondary", href: `#/model/${sample.model_id}`, text: "تحلیل بازار مدل" }) : null,
    ]);
  });
}

// ---------------------------------------------------------------------------
// Ad detail
// ---------------------------------------------------------------------------
export async function adDetail(view = Ctx(), params = {}) {
  const code = params.code;
  await runPage(view, async () => {
    const [ad, hist, deal] = await Promise.all([
      api.get(`/api/ads/${code}/`),
      api.get(`/api/ads/${code}/price-history/`).catch(() => ({ series: [] })),
      api.get(`/api/analytics/deal-scores/${code}/`).catch(() => null),
    ]);
    const raw = ad.raw_payload || {};
    const detail = raw.detail || {};
    const imgs = (raw.images || []).map(i => i.small || i.thumb).filter(Boolean);
    document.getElementById("pageTitle").textContent = ad.title || code;

    clear(view);
    append(view, [
      el("div", { class: "grid cols-2" }, [
        el("div", { class: "card flex" }, [
          el("h2", { text: "جزئیات آگهی" }),
          imgs.length ? el("img", { class: "ad-thumb", src: imgs[0], style: "width:100%;height:auto;border-radius:8px" }) : null,
          kvGrid([
            ["کد", ad.code],
            ["قیمت", price(ad.current_price) + " تومان"],
            ["نوع قیمت", ad.price_type],
            ["برند / مدل", `${ad.brand_name || ""} ${ad.model_name || ""}`.trim()],
            ["تیپ", ad.variant_name],
            ["سال", ad.year],
            ["کارکرد", miles(ad.mileage)],
            ["گیربکس", ad.transmission],
            ["شهر", ad.city_name],
            ["رنگ بدنه", detail.body_color],
            ["وضعیت بدنه", detail.body_status],
            ["سوخت", detail.fuel],
            ["تاریخ انتشار", datetime(ad.publish_at)],
            ["آخرین مشاهده", ago(ad.last_seen_at)],
          ]),
          ad.url ? el("a", { class: "btn secondary sm", href: `https://bama.ir${ad.url}`, target: "_blank", rel: "noopener", text: "مشاهده در باما" }) : null,
        ]),
        el("div", { class: "card flex" }, [
          el("h2", { text: "تحلیل قیمت" }),
          deal ? el("div", { class: "grid cols-3" }, [
            stat("Deal Score", num(deal.score?.toFixed(1)), "از ۱۰۰"),
            stat("تخفیف نسبت به همتایان", pct(deal.discount_pct), "ارزش واقعی"),
            stat("میانه همتایان", price(deal.peer_median), "قیمت منصفانه"),
          ]) : el("p", { class: "muted", text: "امتیاز معامله برای این آگهی محاسبه نشده است." }),
          el("h3", { text: "تاریخچه قیمت" }),
          (hist.series && hist.series.length) ? el("div", { class: "chart-box" }, [el("canvas", { id: "cHist" })]) : el("p", { class: "muted", text: "تغییر قیمتی ثبت نشده است." }),
          isLoggedIn() ? engagementActions(ad) : null,
        ]),
      ]),
    ]);

    if (hist.series && hist.series.length) {
      lineChart($("#cHist"), {
        labels: hist.series.map(s => date(s.observed_at)),
        yToman: true,
        datasets: [{ label: "قیمت", data: hist.series.map(s => s.price) }],
      });
    }
  });
}

function kvGrid(rows) {
  const grid = el("div", { class: "kv" });
  for (const [k, v] of rows) {
    if (v == null || v === "" || v === "—") continue;
    grid.appendChild(el("div", { class: "k", text: k }));
    grid.appendChild(el("div", { class: "mono", text: String(v) }));
  }
  return grid;
}

function engagementActions(ad) {
  const wrap = el("div", { class: "row" });
  const favBtn = el("button", { class: "btn sm secondary", text: "افزودن به علاقه‌مندی" });
  favBtn.addEventListener("click", async () => {
    try { await api.post("/api/favorites/", { code: ad.code }); toast("به علاقه‌مندی‌ها اضافه شد"); }
    catch (e) { toast(e.message, "error"); }
  });
  wrap.appendChild(favBtn);
  return el("div", {}, [el("h3", { text: "ابزار Premium" }), wrap]);
}
