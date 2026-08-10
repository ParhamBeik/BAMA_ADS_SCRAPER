// Account pages: login, register, profile, subscription, admin (staff).
import { api } from "../api.js";
import { login, register, logout, fetchMe, requireLogin, isLoggedIn, cachedUser } from "../auth.js";
import { el, clear, append, card, table, empty, badge, runPage, toast, date, datetime, num } from "../ui.js";

const Ctx = () => document.getElementById("view");
const JOBS = [
  { path: "/api/admin/jobs/fetch/", label: "فچ زنده باما", desc: "دریافت آگهی‌های جدید" },
  { path: "/api/admin/jobs/import/", label: "ایمپورت داده", desc: "بارگذاری JSON اسکریپ‌شده" },
  { path: "/api/admin/jobs/refresh-analytics/", label: "بازسازی تحلیل‌ها", desc: "بازسازی تحلیل‌ها" },
  { path: "/api/admin/jobs/deal-scores/", label: "محاسبه Deal Score", desc: "بازسازی کش امتیاز معامله" },
  { path: "/api/admin/jobs/evaluate-alerts/", label: "ارزیابی هشدارها", desc: "اجرای هشدارها و ارسال نوتیف" },
];

// ---------------------------------------------------------------------------
// Login / Register
// ---------------------------------------------------------------------------
export async function loginPage(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "ورود";
  clear(view);
  const form = el("form", { class: "card form-card" }, [
    el("h2", { text: "ورود به حساب" }),
    field("ایمیل", el("input", { name: "email", type: "email", required: true, placeholder: "you@example.com" })),
    field("رمز عبور", el("input", { name: "password", type: "password", required: true, placeholder: "••••••••" })),
    el("button", { class: "btn", text: "ورود", style: "width:100%" }),
    el("p", { class: "muted" }, ["حساب ندارید؟ ", el("a", { href: "#/register", text: "ثبت‌نام کنید" })]),
  ]);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await login(form.email.value, form.password.value);
      toast("خوش آمدید");
      location.hash = "#/";
    } catch (err) { toast(err.message, "error"); }
  });
  view.appendChild(form);
}

export async function registerPage(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "ثبت‌نام";
  clear(view);
  const form = el("form", { class: "card form-card" }, [
    el("h2", { text: "ساخت حساب کاربری" }),
    field("نام کامل", el("input", { name: "full_name", placeholder: "نام و نام خانوادگی" })),
    field("ایمیل", el("input", { name: "email", type: "email", required: true, placeholder: "you@example.com" })),
    field("رمز عبور", el("input", { name: "password", type: "password", required: true, placeholder: "حداقل ۸ کاراکتر" })),
    el("button", { class: "btn", text: "ثبت‌نام", style: "width:100%" }),
    el("p", { class: "muted" }, ["از قبل حساب دارید؟ ", el("a", { href: "#/login", text: "ورود" })]),
  ]);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await register({ email: form.email.value, password: form.password.value, full_name: form.full_name.value });
      toast("ثبت‌نام انجام شد");
      location.hash = "#/";
    } catch (err) { toast(err.message, "error"); }
  });
  view.appendChild(form);
}

function field(label, input) {
  return el("div", { class: "field" }, [el("label", { text: label }), input]);
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------
export async function profile(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "پروفایل";
  if (!requireLogin(view)) return;
  await runPage(view, async () => {
    const me = await fetchMe();
    const u = me?.user || cachedUser()?.user || {};
    clear(view);
    append(view, [
      el("div", { class: "card" }, [
        el("h2", { text: "حساب کاربری" }),
        kv([["ایمیل", u.email], ["نام", u.full_name], ["کارمند", u.is_staff ? "بله" : "خیر"], ["تاریخ عضویت", datetime(u.date_joined)]]),
      ]),
      el("div", { class: "row" }, [el("button", { class: "btn danger", text: "خروج از حساب", onclick: async () => { await logout(); location.hash = "#/login"; } })]),
    ]);
  });
}

// ---------------------------------------------------------------------------
// Subscription
// ---------------------------------------------------------------------------
export async function subscription(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "اشتراک";
  if (!requireLogin(view)) return;
  await runPage(view, async () => {
    const me = await fetchMe();
    const sub = me?.subscription;
    clear(view);
    if (!sub) { append(view, [empty("اشتراکی یافت نشد.")]); return; }
    const limit = sub.monthly_api_limit;
    const usage = sub.api_usage_count || 0;
    append(view, [
      el("div", { class: "grid cols-3" }, [
        statCard("طرح اشتراک", sub.plan_type, sub.status === "active" ? "فعال" : sub.status),
        statCard("مصرف ماهانه", `${num(usage)} / ${limit == null ? "∞" : num(limit)}`, limit == null ? "نامحدود" : "درخواست در ماه"),
        statCard("تاریخ شروع", date(sub.started_at), sub.expires_at ? `انقضا: ${date(sub.expires_at)}` : "بدون انقضا"),
      ]),
      el("div", { class: "card" }, [
        el("h2", { text: "ارتقای اشتراک" }),
        el("p", { class: "muted", text: "پلن‌های پولی (Pro / Dealer) به‌زودی فعال می‌شوند. در نسخه MVP طرح رایگان با محدودیت سهمیه اعمال می‌شود." }),
      ]),
    ]);
  });
}

function statCard(label, value, sub) {
  return el("div", { class: "card stat" }, [el("div", { class: "label", text: label }), el("div", { class: "value", text: value }), el("div", { class: "sub", text: sub })]);
}

function kv(rows) {
  const g = el("div", { class: "kv" });
  for (const [k, v] of rows) { g.appendChild(el("div", { class: "k", text: k })); g.appendChild(el("div", { text: String(v ?? "—") })); }
  return g;
}

// ---------------------------------------------------------------------------
// Admin (staff)
// ---------------------------------------------------------------------------
export async function admin(view = Ctx()) {
  document.getElementById("pageTitle").textContent = "مدیریت سیستم";
  if (!requireLogin(view)) return;
  await runPage(view, async () => {
    let me;
    try { me = await fetchMe(); } catch { me = cachedUser(); }
    const isStaff = me?.user?.is_staff || cachedUser()?.user?.is_staff;
    clear(view);
    if (!isStaff) {
      append(view, [el("div", { class: "error-box", text: "این بخش فقط برای کاربران مدیر (staff) قابل دسترسی است." })]);
      return;
    }
    const grid = el("div", { class: "grid cols-3" }, JOBS.map(j =>
      el("div", { class: "card flex" }, [
        el("strong", { text: j.label }),
        el("span", { class: "muted", text: j.desc }),
        el("button", { class: "btn sm", text: "اجرا", onclick: () => runJob(j, view) }),
      ])));
    append(view, [el("h2", { text: "اجرای کارها" }), grid, el("h2", { text: "آخرین اجراها", style: "margin-top:18px" })]);
    try {
      const runs = await api.get("/api/fetch-runs/", { page_size: 10 });
      const items = runs.results || runs;
      append(view, [table(["منبع", "وضعیت", "شروع", "پایان"], items, {
        renderRow: r => el("tr", {}, [
          el("td", { text: r.source }),
          el("td", {}, [badge(r.status, r.status === "succeeded" ? "green" : r.status === "running" ? "blue" : r.status === "failed" ? "red" : "gray")]),
          el("td", { class: "muted", text: datetime(r.started_at) }),
          el("td", { class: "muted", text: datetime(r.finished_at) }),
        ]),
      })]);
    } catch { append(view, [empty("بدون سابقه اجرا.")]); }
  });
}

async function runJob(j, view) {
  try { const r = await api.post(j.path); toast(`${j.label}: ${(r && r.status) || "اجرا شد"}`); setTimeout(() => admin(view), 1200); }
  catch (e) { toast(e.message, "error"); }
}
