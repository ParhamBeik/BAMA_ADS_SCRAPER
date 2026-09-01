/**
 * The alert feed, and the rules that fill it.
 *
 * Before this screen the app's only notifier was a staff-owned singleton
 * pointed at one Telegram chat: "tell me when a good deal appears" was the
 * headline of the product's third question and it was invisible to every
 * ordinary account.
 *
 * Two product decisions are visible here.
 *
 * **An alert keeps what was true when it fired.** The discount and peer median
 * shown are the values stored on the delivery, not a live join to the deal
 * board — that table is dropped and rebuilt on a schedule, so a joined feed
 * would blank out an alert the moment its listing stopped qualifying, which is
 * exactly the moment the reader most needs to see what it said.
 *
 * **A rule cannot be looser than the fair-price engine.** `min_peers` is
 * floored at 8 by the API, because below that the median being compared against
 * is not one the app will quote — let alone wake somebody up for.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BellOff, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import type { Paginated } from "../api";
import { Async, BamaLink, Card, Fa, Table, Thumb, km, pct, toman } from "../ui";
import { ModelCombobox } from "../components/ModelCombobox";
import { Button } from "../components/ui/button";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";

interface Alert {
  id: number;
  code: string;
  title: string;
  price: number | null;
  year: number | null;
  mileage: number | null;
  city_name: string;
  status: string;
  image_url: string;
  bama_url: string;
  discount_pct: number | null;
  peer_median: number | null;
  rule_name: string;
  created_at: string;
  read_at: string | null;
}

interface Rule {
  id: number;
  name: string;
  enabled: boolean;
  brand_slug: string;
  model: number | null;
  variant: number | null;
  year_jalali: number | null;
  model_name: string;
  brand_name: string;
  min_discount_pct: number;
  min_peers: number;
  price_min: number | null;
  price_max: number | null;
  mileage_max: number | null;
  exclude_review: boolean;
  telegram_chat_id: string;
}

const MIN_PEERS = 8;

function RuleForm({ onDone }: { onDone: () => void }) {
  const client = useQueryClient();
  const [form, setForm] = useState({
    name: "",
    model: null as number | null,
    brand_slug: "",
    min_discount_pct: 12,
    min_peers: MIN_PEERS,
    price_max: "" as string | number,
    mileage_max: "" as string | number,
    exclude_review: true,
  });

  const create = useMutation({
    mutationFn: () =>
      api.post<Rule>("/api/alert-rules/", {
        ...form,
        price_max: form.price_max === "" ? null : Number(form.price_max),
        mileage_max: form.mileage_max === "" ? null : Number(form.mileage_max),
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["alert-rules"] });
      onDone();
    },
  });

  const field =
    "border-border bg-panel w-full rounded-md border px-2.5 py-1.5 text-sm";

  return (
    <div className="stack">
      <div className="grid cols-2 gap-3">
        <label className="grid gap-1.5">
          <span className="text-muted-foreground text-xs font-semibold">نام قاعده</span>
          <input
            className={field}
            value={form.name}
            placeholder="مثلاً: پژو ۲۰۶ زیر ۸۰۰ میلیون"
            onChange={(e) => setForm({ ...form, name: e.target.value })}
          />
        </label>
        <div className="grid gap-1.5">
          <span className="text-muted-foreground text-xs font-semibold">خودرو</span>
          <ModelCombobox
            value={form.model ? String(form.model) : undefined}
            placeholder="همه خودروها"
            onSelect={(picked) =>
              setForm({
                ...form,
                model: picked ? picked.id : null,
                brand_slug: picked ? picked.brand_slug : "",
              })
            }
          />
        </div>
        <label className="grid gap-1.5">
          <span className="text-muted-foreground text-xs font-semibold">
            کمترین تخفیف (٪)
          </span>
          <input
            className={field}
            type="number"
            min={1}
            max={99}
            value={form.min_discount_pct}
            onChange={(e) =>
              setForm({ ...form, min_discount_pct: Number(e.target.value) })
            }
          />
        </label>
        <label className="grid gap-1.5">
          <span className="text-muted-foreground text-xs font-semibold">
            کمترین آگهی مشابه
          </span>
          <input
            className={field}
            type="number"
            min={MIN_PEERS}
            value={form.min_peers}
            onChange={(e) => setForm({ ...form, min_peers: Number(e.target.value) })}
          />
          <span className="text-muted-foreground text-[11px]">
            کمتر از {MIN_PEERS} پذیرفته نمی‌شود — میانه‌ای که از آگهی‌های کمتر ساخته
            شود، مبنای قابل اتکایی برای اعلان نیست.
          </span>
        </label>
        <label className="grid gap-1.5">
          <span className="text-muted-foreground text-xs font-semibold">
            بیشترین قیمت (تومان)
          </span>
          <input
            className={field}
            type="number"
            value={form.price_max}
            onChange={(e) => setForm({ ...form, price_max: e.target.value })}
          />
        </label>
        <label className="grid gap-1.5">
          <span className="text-muted-foreground text-xs font-semibold">
            بیشترین کارکرد (کیلومتر)
          </span>
          <input
            className={field}
            type="number"
            value={form.mileage_max}
            onChange={(e) => setForm({ ...form, mileage_max: e.target.value })}
          />
        </label>
      </div>

      <div className="flex items-center justify-between gap-3">
        <Label htmlFor="excl" className="text-sm">
          خودروهای رنگ‌شده و تعویضی را کنار بگذار
          <span className="stat-sub block">
            آگهی‌هایی که خودشان ارزانی‌شان را توضیح می‌دهند، پیشنهاد نیستند.
          </span>
        </Label>
        <Switch
          id="excl"
          checked={form.exclude_review}
          onCheckedChange={(v) => setForm({ ...form, exclude_review: v })}
        />
      </div>

      <div className="row">
        <Button onClick={() => create.mutate()} disabled={create.isPending}>
          {create.isPending ? "در حال ذخیره…" : "ساخت قاعده"}
        </Button>
        <Button variant="ghost" onClick={onDone}>انصراف</Button>
        {create.isError && (
          <span className="badge warn">
            {(create.error as Error)?.message ?? "ذخیره نشد"}
          </span>
        )}
      </div>
    </div>
  );
}

function Rules() {
  const client = useQueryClient();
  const [adding, setAdding] = useState(false);
  const rules = useQuery({
    queryKey: ["alert-rules"],
    queryFn: ({ signal }) => api.get<Paginated<Rule>>("/api/alert-rules/", signal),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/api/alert-rules/${id}/`),
    onSuccess: () => client.invalidateQueries({ queryKey: ["alert-rules"] }),
  });

  const toggle = useMutation({
    mutationFn: (rule: Rule) =>
      api.patch(`/api/alert-rules/${rule.id}/`, { enabled: !rule.enabled }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["alert-rules"] }),
  });

  return (
    <Card
      title="قاعده‌های اعلان"
      action={
        !adding && (
          <Button variant="outline" size="sm" onClick={() => setAdding(true)}>
            <Plus className="size-4" /> قاعده تازه
          </Button>
        )
      }
    >
      {adding && <RuleForm onDone={() => setAdding(false)} />}
      <Async query={rules} shape="table" empty="هنوز قاعده‌ای نساخته‌اید.">
        {(data) =>
          data.results.length ? (
            <Table
              head={["قاعده", "خودرو", "کمترین تخفیف", "روشن", <span key="d" className="sr-only">حذف</span>]}
            >
              {data.results.map((rule) => (
                <tr key={rule.id}>
                  <td><Fa>{rule.name || "بدون نام"}</Fa></td>
                  <td>
                    <Fa>{rule.model_name || "همه خودروها"}</Fa>
                    {rule.brand_name && (
                      <div className="stat-sub"><Fa>{rule.brand_name}</Fa></div>
                    )}
                  </td>
                  <td className="num">{pct(rule.min_discount_pct, 0)}</td>
                  <td>
                    <Switch
                      checked={rule.enabled}
                      aria-label="روشن یا خاموش"
                      onCheckedChange={() => toggle.mutate(rule)}
                    />
                  </td>
                  <td className="num">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label="حذف قاعده"
                      onClick={() => remove.mutate(rule.id)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </td>
                </tr>
              ))}
            </Table>
          ) : (
            <div className="state">
              <strong>هنوز قاعده‌ای نساخته‌اید.</strong>
              <p className="empty-hint">
                یک قاعده بسازید تا وقتی آگهی مناسبی پیدا شد همین‌جا به شما گفته شود.
              </p>
            </div>
          )
        }
      </Async>
    </Card>
  );
}

export function Alerts() {
  const client = useQueryClient();
  const feed = useQuery({
    queryKey: ["alerts"],
    queryFn: ({ signal }) => api.get<Paginated<Alert>>("/api/alerts/", signal),
  });

  const markRead = useMutation({
    mutationFn: () => api.post("/api/alerts/mark-read/", {}),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["alerts"] });
      client.invalidateQueries({ queryKey: ["alerts-unread"] });
    },
  });

  // Opening the feed is reading it. Fired once on mount rather than per render,
  // and only when there is something unread — otherwise every visit writes.
  const unread = feed.data?.results?.some((a) => !a.read_at) ?? false;
  useEffect(() => {
    if (unread && !markRead.isPending) markRead.mutate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unread]);

  return (
    <div className="stack">
      <div>
        <h1 style={{ margin: 0, fontSize: 22, letterSpacing: "-0.01em" }}>اعلان‌ها</h1>
        <p className="stat-sub" style={{ margin: 0 }}>
          آگهی‌هایی که با قاعده‌های شما خواندند، از لحظه‌ای که ساخته شدند.
        </p>
      </div>

      <Card title="آگهی‌های رسیده">
        <Async query={feed} shape="cards" empty="هنوز اعلانی نرسیده است.">
          {(data) =>
            data.results.length ? (
              <div className="card-grid">
                {data.results.map((alert) => (
                  <div key={alert.id} className="listing-card stretch-host">
                    <Thumb src={alert.image_url}>
                      {alert.discount_pct != null && (
                        <span className="ribbon">{pct(alert.discount_pct, 0)}</span>
                      )}
                    </Thumb>
                    <div className="listing-meta">
                      <strong>
                        <Link to={`/listing/${alert.code}`} className="stretch-link">
                          <Fa>{alert.title || alert.code}</Fa>
                        </Link>
                      </strong>
                      <div className="row">
                        <span className="deal-price">{toman(alert.price)}</span>
                        <span>{km(alert.mileage)}</span>
                        {/* The median as it stood when the alert fired, not as
                            it stands now — see the file header. */}
                        <span className="deal-median">{toman(alert.peer_median)}</span>
                      </div>
                      <div className="row">
                        <Fa>{alert.city_name || "—"}</Fa>
                        <span>·</span>
                        <span>{alert.year ?? "—"}</span>
                      </div>
                      {alert.status !== "active" && (
                        <span className="badge warn">
                          <BellOff size={11} /> دیگر در باما فهرست نشده است
                        </span>
                      )}
                      <div className="row">
                        <BamaLink href={alert.bama_url} className="ghost above-stretch" />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="state">
                <strong>هنوز اعلانی نرسیده است.</strong>
                <p className="empty-hint">
                  اعلان‌ها هر ۱۵ دقیقه از روی فهرست معامله‌ها ساخته می‌شوند. اگر
                  قاعده‌ای ندارید، پایین همین صفحه یکی بسازید.
                </p>
              </div>
            )
          }
        </Async>
      </Card>

      <Rules />
    </div>
  );
}
