from app.main import app


def test_openapi_contains_required_routes_and_admin_security_header() -> None:
    schema = app.openapi()
    required = {
        "/health", "/db/health", "/summary", "/fetch-runs", "/fetch-runs/{run_id}",
        "/audit-runs/{run_id}", "/ads", "/ads/{code}", "/ads/{code}/price-history",
        "/ads/{code}/versions", "/ads/{code}/changes", "/ads/{code}/timeline", "/changes",
        "/brands", "/brands/{brand}/models", "/markets",
        "/markets/{brand}/{model}/price-trends", "/insights/liquidity",
        "/insights/undervalued", "/insights/market-depth", "/admin/fetch/run", "/admin/audit/run",
    }
    assert required <= set(schema["paths"])
    parameters = schema["paths"]["/admin/fetch/run"]["post"]["parameters"]
    assert any(p["name"] == "x-admin-key" and p["in"] == "header" for p in parameters)
