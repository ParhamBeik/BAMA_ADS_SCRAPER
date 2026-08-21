/**
 * The deal board — the product's point.
 *
 * Every column here exists so the ranking can be checked rather than trusted.
 * The discount is measured against the cohort's own median, the peer count says
 * how many cars that median was built from, and the confidence tier says whether
 * the backend considers that enough. A 40% discount off three listings is not a
 * better deal than 12% off forty, and the board has to make that visible.
 *
 * Two corrections this screen carries, both from an audit of what it was
 * actually showing:
 *
 * **It defaults to the ≤30% band.** An audit of the top 200 rows found 74% were
 * installment ads advertising a down payment rather than a price. Those are now
 * excluded upstream (`listing_kind.exclude_unclear_price`), but the deeper
 * problem survives the filter: the cohort key is `(model, variant, year)` and
 * knows nothing about accident damage, free-zone plates or pre-sales, so above
 * ~30% the gap is essentially always an attribute the model cannot see rather
 * than a bargain. Those rows are still reachable — under a tab that says what
 * they are, instead of on a page that calls them the best deals available.
 *
 * **It paginates.** The board holds ~8,600 rows and this screen used to request
 * a hard-coded top 50 with no way forward, so every genuine 5–20% deal in the
 * cache was unreachable.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, LayoutGrid, List } from "lucide-react";
import { api } from "../api";
import type { Envelope } from "../api";
import { qs, useFilters } from "../filters";
import { Async, Card, ConfidenceDots, Fa, ListingActions, Pager, Provenance, Table, Thumb, km, pct, toman } from "../ui";

/** Above this, the gap is an unmodelled attribute far more often than a deal. */
const TRUSTED_MAX_DISCOUNT = 30;
/** score is rounded to 1 decimal (deal_score.py); the smallest step above it. */
const SCORE_STEP = 0.1;
const PAGE_SIZE = 24;

interface NotifierSettings {
  enabled: boolean;
  min_discount_pct: number;
  min_peers: number;
  price_min: number | null;
  price_max: number | null;
  telegram_chat_id: string;
}

interface Deal {
  code: string;
  title: string;
  discount_pct: number | null;
  price: number | null;
  peer_median: number | null;
  peer_count: number | null;
  confidence: string | null;
  age_days: number | null;
  year: number | null;
  mileage: number | null;
  city_name: string;
  primary_image_url: string;
  condition_flagged: boolean;
}

interface DealBoard extends Envelope {
  count: number;
  limit: number;
  offset: number;
  results: Deal[];
}

interface Brand {
  slug: string;
  name_fa: string;
}

/**
 * The rules that decide what is worth a Telegram message.
 *
 * Deliberately on this page rather than in a settings screen: the thresholds
 * only mean anything next to the board they filter.
 */
function NotifierPanel() {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<NotifierSettings | null>(null);

  const settings = useQuery({
    queryKey: ["notifier-settings"],
    queryFn: ({ signal }) =>
      api.get<NotifierSettings>("/api/notifier-settings/", signal),
  });

  useEffect(() => {
    if (settings.data && !form) setForm(settings.data);
  }, [settings.data, form]);

  const save = useMutation({
    mutationFn: (body: Partial<NotifierSettings>) =>
      api.patch<NotifierSettings>("/api/notifier-settings/", body),
    onSuccess: (data) => {
      setForm(data);
      client.invalidateQueries({ queryKey: ["notifier-settings"] });
    },
  });

  if (!form) return null;
  const set = (patch: Partial<NotifierSettings>) =>
    setForm({ ...form, ...patch });

  return (
    <Card>
      <div className="row between">
        <strong>
          Telegram alerts{" "}
          <span className={`badge ${form.enabled ? "ok" : ""}`}>
            {form.enabled ? "On" : "Off"}
          </span>
        </strong>
        <button className="ghost" onClick={() => setOpen(!open)}>
          {open ? "Close" : "Settings"}
        </button>
      </div>

      {!open ? (
        <p className="muted">
          Listings with at least {form.min_discount_pct}% off and{" "}
          {form.min_peers} peers — each listing only once.
        </p>
      ) : (
        <div className="stack">
          <label className="row">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            <span>Send alerts</span>
          </label>
          <div className="row wrap">
            <label>
              Min discount (%)
              <input
                type="number" min={1} max={99} value={form.min_discount_pct}
                onChange={(e) => set({ min_discount_pct: Number(e.target.value) })}
              />
            </label>
            <label>
              Min peers
              <input
                type="number" min={8} value={form.min_peers}
                onChange={(e) => set({ min_peers: Number(e.target.value) })}
              />
            </label>
            <label>
              Min price
              <input
                type="number" value={form.price_min ?? ""}
                onChange={(e) =>
                  set({ price_min: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <label>
              Max price
              <input
                type="number" value={form.price_max ?? ""}
                onChange={(e) =>
                  set({ price_max: e.target.value ? Number(e.target.value) : null })
                }
              />
            </label>
            <label>
              Telegram chat ID
              <input
                value={form.telegram_chat_id}
                onChange={(e) => set({ telegram_chat_id: e.target.value })}
              />
            </label>
          </div>
          <div className="row">
            <button onClick={() => save.mutate(form)} disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </button>
            {save.isSuccess && !save.isPending && (
              <span className="badge ok">Saved</span>
            )}
            {save.isError && (
              <span className="badge warn">
                {(save.error as Error)?.message ?? "Save failed"}
              </span>
            )}
          </div>
          <p className="muted">
            A minimum peer count under 8 isn't accepted — a median built from
            fewer listings isn't a reliable basis for an alert.
          </p>
        </div>
      )}
    </Card>
  );
}

/** "0d on market" is technically right and reads like a bug. */
function ageLabel(days: number | null): string {
  if (days == null) return "—";
  if (days === 0) return "Listed today";
  return `${days}d on market`;
}

function DealCard({ deal }: { deal: Deal }) {
  const suspect = (deal.discount_pct ?? 0) > TRUSTED_MAX_DISCOUNT;
  return (
    <Link to={`/listing/${deal.code}`} className="listing-card">
      <Thumb src={deal.primary_image_url}>
        <span className={`ribbon${suspect ? " suspect" : ""}`}>
          {pct(deal.discount_pct, 0)}
        </span>
        {deal.condition_flagged && (
          <span className="card-badges">
            <span className="badge warn" title="Listing description mentions an accident, free-zone plate, or body condition">
              <AlertTriangle size={11} /> Condition
            </span>
          </span>
        )}
      </Thumb>
      <div className="listing-meta">
        <strong>
          <Fa>{deal.title || deal.code}</Fa>
        </strong>
        <div className="row">
          <span className="deal-price">{toman(deal.price)}</span>
          <span className="deal-median">{toman(deal.peer_median)}</span>
        </div>
        <div className="row">
          <ConfidenceDots tier={deal.confidence} />
          <span>{deal.peer_count ?? "—"} peers</span>
          <span>·</span>
          <span>{deal.year ?? "—"}</span>
          <span>·</span>
          <span>{km(deal.mileage)}</span>
        </div>
        <div className="row">
          <Fa>{deal.city_name || "—"}</Fa>
          <span>·</span>
          <span>{ageLabel(deal.age_days)}</span>
        </div>
      </div>
    </Link>
  );
}

export function Deals() {
  const filters = useFilters();
  const page = filters.getInt("page") ?? 1;
  const band = filters.get("band") === "review" ? "review" : "trusted";
  const view = filters.get("view") === "table" ? "table" : "cards";
  const brand = filters.get("brand");
  const priceMin = filters.get("price_min");
  const priceMax = filters.get("price_max");
  const confidence = filters.get("confidence");

  const brands = useQuery({
    queryKey: ["brands"],
    queryFn: ({ signal }) =>
      api.get<{ results?: Brand[] } | Brand[]>("/api/brands/", signal),
  });
  const brandList: Brand[] = Array.isArray(brands.data)
    ? brands.data
    : (brands.data?.results ?? []);

  const params = {
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
    brand,
    price_min: priceMin,
    price_max: priceMax,
    confidence,
    // The band is the whole point of the tab: one query, two windows onto it.
    // Backend min_score/max_score are both inclusive, so the boundary value
    // itself must land in exactly one window — trusted claims it (<=), review
    // starts one score step above (>), matching the ribbon's ">" suspect check.
    ...(band === "review"
      ? { min_score: TRUSTED_MAX_DISCOUNT + SCORE_STEP }
      : { max_score: TRUSTED_MAX_DISCOUNT }),
  };

  const deals = useQuery({
    queryKey: ["deal-scores", params],
    queryFn: ({ signal }) =>
      api.get<DealBoard>(`/api/analytics/deal-scores/${qs(params)}`, signal),
  });

  const hasFilter = Boolean(brand || priceMin || priceMax || confidence);
  const clear = () =>
    filters.set({
      brand: null, price_min: null, price_max: null, confidence: null, page: null,
    });

  return (
    <div className="stack" dir="rtl">
      <div className="segmented" role="group" aria-label="Discount range">
        <button
          className={band === "trusted" ? "on" : ""}
          onClick={() => filters.set({ band: null, page: null })}
        >
          Trusted deals
        </button>
        <button
          className={band === "review" ? "on" : ""}
          onClick={() => filters.set({ band: "review", page: null })}
        >
          Needs review (over {TRUSTED_MAX_DISCOUNT}%)
        </button>
      </div>

      <p className="muted">
        {band === "trusted" ? (
          <>
            Listings priced below their peer group's median. The discount is
            measured against the peer-group median; "peers" and the
            confidence dots show how many listings that median was built
            from.
          </>
        ) : (
          <>
            A discount above {TRUSTED_MAX_DISCOUNT}% almost always has a
            reason the model can't see: the peer group is built only from
            "model, trim, and year," and knows nothing about accidents,
            free-zone plates, or pre-sales. Nothing is hidden here, but check
            it yourself before you call.
          </>
        )}
      </p>

      <div className="filters">
        <select
          value={brand ?? ""}
          onChange={(e) => filters.set({ brand: e.target.value || null, page: null })}
          aria-label="Brand"
        >
          <option value="">All brands</option>
          {brandList.map((b) => (
            <option key={b.slug} value={b.slug}>{b.name_fa}</option>
          ))}
        </select>
        <select
          value={confidence ?? ""}
          onChange={(e) => filters.set({ confidence: e.target.value || null, page: null })}
          aria-label="Confidence"
        >
          <option value="">Any confidence</option>
          <option value="high">High confidence only</option>
          <option value="medium">Medium confidence</option>
          <option value="low">Low confidence</option>
        </select>
        <input
          key={`price_min-${priceMin}`}
          type="number"
          placeholder="Min price (toman)"
          defaultValue={priceMin ?? ""}
          onBlur={(e) => filters.set({ price_min: e.target.value || null, page: null })}
        />
        <input
          key={`price_max-${priceMax}`}
          type="number"
          placeholder="Max price (toman)"
          defaultValue={priceMax ?? ""}
          onBlur={(e) => filters.set({ price_max: e.target.value || null, page: null })}
        />
        {hasFilter && <button onClick={clear}>Clear filters</button>}
        <div className="segmented" style={{ marginInlineStart: "auto" }}>
          <button
            className={view === "cards" ? "on" : ""}
            onClick={() => filters.set({ view: null })}
            aria-label="Card view"
          >
            <LayoutGrid size={14} />
          </button>
          <button
            className={view === "table" ? "on" : ""}
            onClick={() => filters.set({ view: "table" })}
            aria-label="Table view"
          >
            <List size={14} />
          </button>
        </div>
      </div>

      <Card>
        <Async query={deals} empty="No scores computed yet." shape="cards">
          {(board) => {
            const rows = board.results ?? [];
            if (!rows.length) {
              // page > 1 with zero rows usually means the page fell out of
              // range (fewer results now than when this URL was built), not
              // that there are no matches at all — leave a way back to page 1
              // instead of a dead end.
              return (
                <div className="state">
                  <strong>No listings match these filters.</strong>
                  <p className="empty-hint">
                    {page > 1
                      ? "This page may no longer exist."
                      : "Try simpler filters or a different brand."}
                  </p>
                  {page > 1 && (
                    <button onClick={() => filters.set({ page: null })}>
                      Back to page 1
                    </button>
                  )}
                </div>
              );
            }
            const total = board.count ?? rows.length;
            const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
            return (
              <>
                {view === "cards" ? (
                  <div className="card-grid">
                    {rows.map((d) => (
                      <DealCard key={d.code} deal={d} />
                    ))}
                  </div>
                ) : (
                  <Table
                    head={[
                      "Listing", "Discount", "Price", "Peer median",
                      "Peers", "Confidence", "Age", "",
                    ]}
                  >
                    {rows.map((d) => (
                      <tr key={d.code}>
                        <td>
                          <Link to={`/listing/${d.code}`}>
                            <Fa>{d.title || d.code}</Fa>
                          </Link>
                          {d.condition_flagged && (
                            <div className="badge warn" style={{ marginTop: 4 }}>
                              <AlertTriangle size={11} /> Read the condition notes
                            </div>
                          )}
                        </td>
                        <td className="num up">{pct(d.discount_pct)}</td>
                        <td className="num">{toman(d.price)}</td>
                        <td className="num">{toman(d.peer_median)}</td>
                        <td className="num">{d.peer_count ?? "—"}</td>
                        <td className="num"><ConfidenceDots tier={d.confidence} /></td>
                        <td className="num">
                          {d.age_days != null ? `${d.age_days}d` : "—"}
                        </td>
                        <td className="num">
                          <ListingActions code={d.code} />
                        </td>
                      </tr>
                    ))}
                  </Table>
                )}

                <div style={{ marginTop: 14 }}>
                  <Pager
                    page={page}
                    lastPage={lastPage}
                    total={total}
                    onChange={(next) => filters.set({ page: next })}
                  />
                </div>
                <Provenance envelope={board} />
              </>
            );
          }}
        </Async>
      </Card>

      <NotifierPanel />
    </div>
  );
}
