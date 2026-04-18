"""One-shot: fetch 5-8 detail-page HTML fixtures for unit tests.

Picks URLs from the live listing page (most recent judgments) plus an
older year for structural variety. Saves under ``tests/fixtures/`` with
the listing card's citation slug as the filename so the origin is
obvious from ``ls``.

Run once:  uv run python scripts/fetch_fixtures.py

Politeness: reuses the same 1.5s jittered sleep as Phase 1. Hits ~1
listing page + up to 8 detail pages (~12s including parse).
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

# Add project root so we can import the resources module
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from resources.judgments import (  # noqa: E402
    BASE_URL,
    INDEX_PARAMS,
    INDEX_PATH,
    create_client,
    polite_sleep,
)

FIXTURES_DIR = ROOT / "tests" / "fixtures"
FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def slugify(text: str) -> str:
    return SLUG_RE.sub("_", text).strip("_").lower()[:80]


def listing_urls(client: httpx.Client, params: dict, n: int) -> list[tuple[str, str]]:
    """Return up to n (source_url, slug) tuples from one listing page."""
    url = urljoin(BASE_URL, INDEX_PATH)
    r = client.get(url, params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    out: list[tuple[str, str]] = []
    for card in soup.select("div.card.col-12"):
        if len(out) >= n:
            break
        title_link = card.select_one("a.gd-heardertext")
        cite_link = card.select_one("a.citation-num-link span.gd-addinfo-text")
        if title_link is None or not title_link.get("href"):
            continue
        href = title_link["href"].strip()
        source = urljoin(BASE_URL, href)
        slug_seed = cite_link.get_text(strip=True) if cite_link else href
        slug = slugify(slug_seed)
        if not slug:
            continue
        out.append((source, slug))
    return out


def fetch_detail(client: httpx.Client, url: str, out_path: Path) -> None:
    r = client.get(url)
    r.raise_for_status()
    out_path.write_text(r.text, encoding="utf-8")
    print(f"  saved {out_path.name} ({len(r.text) // 1024} KB)")


def main() -> int:
    plans: list[tuple[dict, int, str]] = [
        # Most recent — picks up whatever's at the top of SUPCT listing
        ({**INDEX_PARAMS, "CurrentPage": "1"}, 3, "recent"),
        # 2015 era — structural variation
        ({**INDEX_PARAMS, "YearOfDecision": "2015", "CurrentPage": "1"}, 2, "2015"),
        # 2005 era — oldest structural variant
        ({**INDEX_PARAMS, "YearOfDecision": "2005", "CurrentPage": "1"}, 2, "2005"),
    ]

    with create_client() as client:
        picks: list[tuple[str, str]] = []
        for params, n, label in plans:
            print(f"Listing ({label}): {params}")
            try:
                found = listing_urls(client, params, n)
            except Exception as exc:  # noqa: BLE001
                print(f"  listing failed: {exc}")
                continue
            for url, slug in found:
                picks.append((url, f"{label}_{slug}"))
            polite_sleep()

        print(f"\nFetching {len(picks)} detail pages...")
        for url, slug in picks:
            out_path = FIXTURES_DIR / f"{slug}.html"
            if out_path.exists():
                print(f"  skip  {out_path.name} (already present)")
                continue
            print(f"  GET   {url}")
            try:
                fetch_detail(client, url, out_path)
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL  {exc}")
                continue
            polite_sleep()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
