import time
from collections.abc import Iterator
from typing import Any

import requests

from app.config import Settings

SEARCH_URL = "https://bama.ir/cad/api/search"
WARMUP_URL = "https://bama.ir/car?image=1&priced=1"


# ---------------------------------------------------------------------------
# Bama HTTP client
# ---------------------------------------------------------------------------

def create_session(settings: Settings) -> requests.Session:
    """Create a browser-like session so Bama returns the normal search payload."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "application/json, text/plain, */*", "Accept-Language": "fa,en;q=0.9",
        "Referer": "https://bama.ir/car", "X-Requested-With": "XMLHttpRequest",
    })
    if settings.bama_cookie:
        session.headers["Cookie"] = settings.bama_cookie
    return session


def iter_ads(settings: Settings, max_ads: int, page_pause: float) -> Iterator[dict[str, Any]]:
    """Yield non-banner ads page by page until the feed ends or max_ads is hit."""
    session = create_session(settings)
    try:
        try:
            session.get(WARMUP_URL, timeout=settings.bama_request_timeout)
        except requests.RequestException:
            pass
        page = 1
        yielded = 0
        while yielded < max_ads:
            response = session.get(f"{SEARCH_URL}?pageIndex={page}", timeout=settings.bama_request_timeout)
            response.raise_for_status()
            rows = response.json().get("data", {}).get("ads", [])
            ads = [row for row in rows if isinstance(row, dict) and row.get("type") != "banner"]
            if not ads:
                break
            for ad in ads:
                if yielded >= max_ads:
                    break
                yielded += 1
                yield ad
            page += 1
            if page_pause:
                time.sleep(page_pause)
    finally:
        session.close()
