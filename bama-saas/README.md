# Bama SaaS Backend

Independent PostgreSQL/FastAPI service that fetches live Bama listings. It does not import or depend on `bama-scraper` data.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Open <http://localhost:8000/docs>. Schema changes are made only through Alembic; the API never calls `create_all()`.

## Docker

From the repository root:

```bash
ADMIN_API_KEY='choose-a-secret' docker compose up --build
```

Compose starts only PostgreSQL and the API. The API container applies migrations before Uvicorn starts.

## Operations

Read endpoints are public. Mutating endpoints require `X-Admin-Key` and return a tracked run with HTTP 202:

```bash
curl -X POST http://localhost:8000/admin/fetch/run \
  -H 'Content-Type: application/json' -H 'X-Admin-Key: choose-a-secret' \
  -d '{"max_ads": 5}'
curl -X POST http://localhost:8000/admin/audit/run -H 'X-Admin-Key: choose-a-secret'
```

Equivalent container/local commands are `python -m app.cli fetch --max-ads 5` and `python -m app.cli audit`. PostgreSQL advisory locks prevent concurrent runs. Fetch sightings are always retained, unchanged sightings reuse immutable payload versions, and price rows are added only when price/payment state changes. Disappearing ads are not marked inactive—freshness comes from `last_seen_at`.

## API groups

- Operations: `/health`, `/db/health`, `/summary`, `/fetch-runs`, `/audit-runs/{id}`
- Catalog: `/ads`, `/ads/{code}`, `/brands`, `/brands/{brand}/models`, `/markets`
- History: `/ads/{code}/price-history`, `/ads/{code}/versions`, `/ads/{code}/changes`, `/changes`, `/ads/{code}/timeline`, `/markets/{brand}/{model}/price-trends`
- Insights: `/insights/liquidity`, `/insights/undervalued`, `/insights/market-depth`

Prices are integer values in Bama's displayed unit. Liquidity is a transparent volume/dispersion score; undervaluation compares an ad with the median of at least five matching brand/model/trim/year listings.

## Tests

```bash
pytest
python -m compileall app
```

The integration suite expects `TEST_DATABASE_URL` to point to a disposable PostgreSQL database.
