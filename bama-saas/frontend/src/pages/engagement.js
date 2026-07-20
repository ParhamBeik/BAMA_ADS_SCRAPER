// Premium engagement pages: favorites, watchlists, alerts. All owner-scoped and
// require a logged-in user (requireLogin gates them; in dev AllowAny still
// scopes by request.user, so a real JWT login is needed to see/create data).
import { api, pageOf } from "../api.js";
import { requireLogin } from "../auth.js";
import { el, clear, append, card, table, empty, runPage, toast, price, num, date } from "../ui.js";

const Ctx = () => document.getElementById("view");
const ALERT_TYPES = {
  undervalued: "زیر ارزش بازار",
  price_drop: "کاهش قیمت",
  new_listing: "آگهی جدید",
  market: "هشدار بازار",
};

// ---------------------------------------------------------------------------
// Favorites
// ---------------------------------------------------------------------------
export async function favorites(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "علاقه‌مندی‌ها";
  if (!requireLogin(view)) return;
  await runPage(view, async () => {
    const data = await api.get("/api/favorites/", { page_size: 25 });
    const { items } = pageOf(data);
    const ads = await Promise.all(items.map(f => api.get(`/api/ads/${f.code}/`).catch(() => null)));
    clear(view);
    if (!items.length) { append(view, [empty("هنوز آگهی‌ای به علاقه‌مندی‌ها اضافه نکرده‌اید.")]); return; }
    append(view, [
      table(["عنوان", "قیمت", "تاریخ افزودن", ""], items.map((f, i) => ({ f, ad: ads[i] })), {
        renderRow: ({ f, ad }) => el("tr", {}, [
          el("td", {}, [el("a", { class: "linkrow", href: `#/ad/${f.code}` }, [document.createTextNode(ad?.title || f.code)])]),
          el("td", { class: "mono", text: price(ad?.current_price) }),
          el("td", { class: "muted", text: date(f.created_at) }),
          el("td", {}, [el("button", { class: "btn sm danger", text: "حذف", onclick: () => removeFav(f.code, view) })]),
        ]),
      }),
    ]);
  });
}

async function removeFav(code, view) {
  try { await api.del(`/api/favorites/${code}/`); toast("حذف شد"); favorites(view); }
  catch (e) { toast(e.message, "error"); }
}

// ---------------------------------------------------------------------------
// Watchlists
// ---------------------------------------------------------------------------
export async function watchlists(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "واچ‌لیست‌ها";
  if (!requireLogin(view)) return;
  await runPage(view, async () => {
    const data = await api.get("/api/watchlists/");
    const { items } = pageOf(data);
    const form = el("form", { class: "helpers" }, [
      el("input", { name: "name", placeholder: "نام واچ‌لیست جدید" }),
      el("button", { class: "btn", text: "ایجاد" }),
    ]);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = form.name.value.trim();
      if (!name) return;
      try { await api.post("/api/watchlists/", { name }); toast("ایجاد شد"); watchlists(view); }
      catch (err) { toast(err.message, "error"); }
    });
    clear(view);
    append(view, [form, items.length ? table(["نام", "تعداد آگهی", "تاریخ", ""], items, {
      renderRow: w => el("tr", {}, [
        el("td", { text: w.name }),
        el("td", { class: "mono", text: num((w.ads || []).length) }),
        el("td", { class: "muted", text: date(w.created_at) }),
        el("td", {}, [el("button", { class: "btn sm danger", text: "حذف", onclick: () => delList(`/api/watchlists/${w.id}/`, view) })]),
      ]),
    }) : empty("هنوز واچ‌لیستی نساخته‌اید.")]);
  });
}

async function delList(path, view) {
  try { await api.del(path); toast("حذف شد"); watchlists(view); }
  catch (e) { toast(e.message, "error"); }
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------
export async function alerts(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "هشدارها";
  if (!requireLogin(view)) return;
  await runPage(view, async () => {
    const [data, markets] = await Promise.all([
      api.get("/api/alerts/"),
      api.get("/api/markets/", { limit: 500 }).catch(() => []),
    ]);
    const { items } = pageOf(data);
    const form = el("form", { class: "helpers" }, [
      el("select", { name: "alert_type" }, Object.entries(ALERT_TYPES).map(([v, label]) => el("option", { value: v, text: label }))),
      el("select", { name: "model" }, [el("option", { value: "", text: "مدل…" }), ...markets.map(m => el("option", { value: m.model_id, text: `${m.brand_name} ${m.model_name}` }))]),
      el("input", { name: "threshold", type: "number", placeholder: "آستانه (٪)" }),
      el("button", { class: "btn", text: "ایجاد هشدار" }),
    ]);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(form);
      const body = { alert_type: fd.get("alert_type"), threshold: fd.get("threshold") || null, channels: ["in_app"] };
      if (body.alert_type === "undervalued") body.model = Number(fd.get("model")) || null;
      try { await api.post("/api/alerts/", body); toast("هشدار ایجاد شد"); alerts(view); }
      catch (err) { toast(err.detail || err.message, "error"); }
    });
    clear(view);
    append(view, [
      form,
      items.length ? table(["نوع", "آستانه", "کانال‌ها", "وضعیت", ""], items, {
        renderRow: a => el("tr", {}, [
          el("td", { text: ALERT_TYPES[a.alert_type] || a.alert_type }),
          el("td", { class: "mono", text: a.threshold != null ? `${a.threshold}٪` : "—" }),
          el("td", { text: (a.channels || []).join("، ") }),
          el("td", {}, [badge(a.enabled ? "فعال" : "غیرفعال", a.enabled ? "green" : "gray")]),
          el("td", { class: "row" }, [
            el("button", { class: "btn sm secondary", text: a.enabled ? "خاموش" : "روشن", onclick: () => toggleAlert(a, view) }),
            el("button", { class: "btn sm danger", text: "حذف", onclick: () => api.del(`/api/alerts/${a.id}/`).then(() => { toast("حذف شد"); alerts(view); }) }),
          ]),
        ]),
      }) : empty("هشداری تنظیم نشده است."),
    ]);
  });
}

async function toggleAlert(a, view) {
  try { await api.patch(`/api/alerts/${a.id}/`, { enabled: !a.enabled }); alerts(view); }
  catch (e) { toast(e.message, "error"); }
}

function badge(text, kind) { return el("span", { class: `badge ${kind}`, text }); }
