# Pagination Patterns Reference

How to handle paginated listing pages. Read this when the site analysis reveals pagination
controls on the index page.

## Core Rules

1. **Listing pages are always fetched sequentially** — you need page N's response to know if
   page N+1 exists.
2. **Detail pages can be fetched concurrently** (semaphore-bounded) after collecting all URLs
   from the listing phase.
3. **Incremental runs stop early** when they encounter already-known items.

## Four Pagination Patterns

### Pattern A — Page Number in URL

The simplest and most common pattern. The URL includes a page parameter that increments.

**Detection signals:**
- URL contains `?page=`, `?p=`, `?offset=`, `&start=`
- Links like `/results/2`, `/results/3` in the pagination area

```python
def discover_all_pages(
    base_url: str, existing_ids: set, client: httpx.Client
) -> List[Dict[str, Any]]:
    """Paginate through numbered pages, collecting items."""
    all_items = []
    page = 1
    consecutive_known = 0

    while True:
        url = f"{base_url}?page={page}"
        click.echo(f"Fetching page {page}: {url}")

        response = client.get(url)
        if response.status_code == 404:
            break  # Past the last page
        response.raise_for_status()

        items = parse_listing_page(response.text)
        if not items:
            break  # Empty page — we've gone past the end

        for item in items:
            if item["id"] in existing_ids:
                consecutive_known += 1
                if consecutive_known >= INCREMENTAL_STOP_THRESHOLD:
                    click.echo(
                        f"Stopping: {consecutive_known} consecutive known items"
                    )
                    return all_items
            else:
                consecutive_known = 0
                all_items.append(item)

        page += 1
        polite_sleep()

    return all_items
```

### Pattern B — Next-Page Link

A "Next" button or `rel="next"` link in the HTML. More resilient to URL structure changes.

**Detection signals:**
- An `<a>` with text "Next", "→", ">>"
- An `<a rel="next">` element
- A `.pagination .next` or similar CSS class

```python
def discover_all_pages_by_next_link(
    start_url: str, existing_ids: set, client: httpx.Client
) -> List[Dict[str, Any]]:
    """Follow next-page links until there are none."""
    all_items = []
    current_url = start_url
    consecutive_known = 0
    pages_fetched = 0
    max_pages = 100  # Safety limit

    while current_url and pages_fetched < max_pages:
        click.echo(f"Fetching: {current_url}")
        response = client.get(current_url)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")
        items = parse_listing_from_soup(soup)

        for item in items:
            if item["id"] in existing_ids:
                consecutive_known += 1
                if consecutive_known >= INCREMENTAL_STOP_THRESHOLD:
                    click.echo(
                        f"Stopping: {consecutive_known} consecutive known items"
                    )
                    return all_items
            else:
                consecutive_known = 0
                all_items.append(item)

        # Find the next link
        next_link = soup.find("a", rel="next")
        if not next_link:
            next_link = soup.find("a", text=re.compile(r"next|→|>>", re.I))

        if next_link and next_link.get("href"):
            current_url = resolve_url(start_url, next_link["href"])
        else:
            current_url = None

        pages_fetched += 1
        polite_sleep()

    return all_items
```

### Pattern C — Cursor/Token-Based (API style)

The response includes a `next_cursor`, `next_page_token`, or `continuation` parameter.
Common in REST APIs.

**Detection signals:**
- The listing page loads via JavaScript (view source shows minimal HTML)
- Network inspector shows XHR/fetch calls with cursor parameters
- The HTML includes `data-next-cursor` or similar attributes

```python
def discover_all_pages_by_cursor(
    api_url: str, existing_ids: set, client: httpx.Client
) -> List[Dict[str, Any]]:
    """Paginate using cursor tokens from the API response."""
    all_items = []
    cursor = None
    consecutive_known = 0

    while True:
        params = {"limit": 50}
        if cursor:
            params["cursor"] = cursor

        response = client.get(api_url, params=params)
        response.raise_for_status()
        data = response.json()

        items = data.get("results", [])
        if not items:
            break

        for item in items:
            item_id = make_id(item.get("url", str(item)))
            if item_id in existing_ids:
                consecutive_known += 1
                if consecutive_known >= INCREMENTAL_STOP_THRESHOLD:
                    return all_items
            else:
                consecutive_known = 0
                all_items.append(process_api_item(item))

        cursor = data.get("next_cursor") or data.get("next_page_token")
        if not cursor:
            break

        polite_sleep()

    return all_items
```

### Pattern D — Infinite Scroll / JavaScript-Rendered

Content loads dynamically via JavaScript. This is the hardest pattern.

**Detection signals:**
- The page source has minimal content but the rendered page shows lots of items
- A "Load More" button exists
- Scrolling down loads more content
- Network inspector shows XHR/fetch calls triggered by scroll events

**Strategy:**

Do NOT reach for Playwright/Selenium as a first resort. Instead:

1. **Find the underlying API** — Open browser dev tools, network tab, scroll/click to trigger
   loading, and find the XHR request. It usually hits a JSON API endpoint. If found, use
   Pattern C (cursor-based) against that API directly.

2. **If no API is discoverable** — Flag this to the user as a complexity warning. Suggest:
   - Checking if the site has an RSS feed or sitemap (`/sitemap.xml`)
   - Checking if there's a mobile version with simpler HTML
   - Using a headless browser as a last resort (adds `playwright` dependency)

If headless browsing is truly necessary:

```python
# This adds a heavy dependency — only use as last resort
# uv add playwright
# playwright install chromium

async def fetch_with_browser(url: str) -> str:
    """Fetch JavaScript-rendered page content."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle")
        content = await page.content()
        await browser.close()
        return content
```

## Incremental Stop

The `INCREMENTAL_STOP_THRESHOLD` constant controls how many consecutive already-known items
trigger early termination of pagination. This avoids re-crawling the entire archive on
incremental runs.

```python
# Stop paginating after N consecutive known items
INCREMENTAL_STOP_THRESHOLD = 5
```

The right threshold depends on the source:

- **Chronologically ordered** (newest first): 3–5 is usually sufficient. Once you hit items
  from the last run, everything beyond is older and already processed.
- **Non-chronological or mixed ordering**: Increase to 10–15 to avoid missing items that were
  inserted between known ones.
- **First run / full rebuild**: Set to a very high number or disable entirely.

## Multiplied Request Counts

Pagination multiplies request counts dramatically. A source with 50 pages × 20 items × 1
detail page each = 1,050 requests. Plan for this:

- Use the circuit breaker to detect if the source starts failing
- Log progress clearly so the user knows where in the pagination they are
- Consider a page limit for first runs (`max_pages = 100`) with a log message suggesting the
  user increase it if needed

## URL Resolution Helper

Pagination links are often relative. Resolve them against the base URL:

```python
from urllib.parse import urljoin


def resolve_url(base: str, href: str) -> str:
    """Resolve a potentially relative URL against a base URL."""
    return urljoin(base, href)
```
