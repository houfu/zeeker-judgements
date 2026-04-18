# JSON API Resource Reference

Use this reference when the source exposes a REST/JSON API. This is common for social media
platforms (Mastodon, Reddit, Bluesky), government open data portals, and content aggregators.

## When to Use

- The index page is JavaScript-rendered but the platform has a public API
- The user provides a known API-based platform (Mastodon, Reddit, Hacker News, etc.)
- The source has structured JSON endpoints that return richer data than HTML scraping would
- You discover XHR/fetch calls in the browser network inspector during site analysis

## Detection During Site Analysis

If the index page requires JavaScript, before reaching for a headless browser:

1. **Recognize the platform** — Mastodon, Reddit, Discourse, Ghost, WordPress, etc. all have
   well-documented public APIs.
2. **Check for API docs** — Look for `/api/`, `/api/v1/`, or developer documentation links.
3. **Inspect network requests** — The JS frontend is calling an API somewhere. The same
   endpoints are usually available directly.

### Mastodon-Specific Detection

Mastodon URLs follow the pattern `https://<instance>/@<username>`. Every Mastodon account has:

- **RSS feed**: `https://<instance>/@<username>.rss`
- **Public API**: `https://<instance>/api/v1/accounts/:id/statuses` (no auth required)

Prefer the API over RSS because it returns structured JSON with card metadata (link previews),
tags, and richer status information.

To resolve a username to an account ID:

```python
def resolve_mastodon_account(instance: str, username: str) -> str:
    """Look up the numeric account ID for a Mastodon username."""
    response = httpx.get(
        f"https://{instance}/api/v1/accounts/lookup",
        params={"acct": username},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["id"]
```

## Architecture

```
JSON API (paginated)
    ↓
For each item:
    ├─ Filter (keyword or AI classification) → skip irrelevant items
    ├─ Extract linked content (Jina Reader, if item links to external article)
    ├─ AI Summary (optional)
    └─ API metadata → structured fields
    ↓
Flat table (one row per item)
FTS on: title, summary, content_text
```

## Pagination

Most JSON APIs use cursor-based pagination (Pattern C from `pagination-patterns.md`).

### Mastodon Pagination

Mastodon uses `max_id` and `min_id` parameters. Each response returns a batch of statuses;
use the last status ID as `max_id` for the next page.

```python
def fetch_statuses(
    instance: str,
    account_id: str,
    existing_ids: set,
    limit: int = 40,
) -> List[dict]:
    """Fetch statuses with cursor-based pagination."""
    all_statuses = []
    max_id = None
    consecutive_known = 0

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        while True:
            params = {"limit": limit, "exclude_replies": "true", "exclude_reblogs": "true"}
            if max_id:
                params["max_id"] = max_id

            response = client.get(
                f"https://{instance}/api/v1/accounts/{account_id}/statuses",
                params=params,
            )
            response.raise_for_status()
            statuses = response.json()

            if not statuses:
                break

            for status in statuses:
                if status["id"] in existing_ids:
                    consecutive_known += 1
                    if consecutive_known >= INCREMENTAL_STOP_THRESHOLD:
                        click.echo(
                            f"Stopping: {consecutive_known} consecutive known statuses"
                        )
                        return all_statuses
                else:
                    consecutive_known = 0
                    all_statuses.append(status)

            max_id = statuses[-1]["id"]
            polite_sleep()

    return all_statuses
```

### Generic Cursor Pagination

For other APIs, adapt the cursor field name:

```python
# Common cursor patterns:
# - max_id / since_id (Mastodon, Twitter-like)
# - cursor / next_cursor (many REST APIs)
# - offset + limit (simple offset pagination)
# - next_page_token (Google-style)
# - after (GraphQL relay-style)
```

## Extracting Data from API Responses

API responses are already structured — no HTML parsing needed. Map the JSON fields directly
to your schema.

### Mastodon Status Fields

A Mastodon status object contains:

```python
{
    "id": "109305372050694915",        # Stable unique ID (use as record ID)
    "created_at": "2022-11-08T00:33:35.000Z",  # ISO 8601
    "content": "<p>HTML content of the toot</p>",
    "url": "https://mastodon.sg/@straits_times/109305372050694915",
    "language": "en",
    "tags": [{"name": "law"}, ...],
    "card": {                           # Link preview (if toot contains a URL)
        "url": "https://straitstimes.com/article/...",
        "title": "Article headline",
        "description": "Article summary from OpenGraph",
        "author_name": "Straits Times",
        "image": "https://...",
    },
    "media_attachments": [...],
}
```

The `card` object is key for news aggregator accounts — it contains the linked article's
metadata. Toots without a card are typically plain text updates or replies that can be skipped
for article-focused resources.

### Processing a Status into a Record

```python
def process_status(status: dict) -> Optional[Dict[str, Any]]:
    """Convert a Mastodon status into a database record."""
    card = status.get("card")
    if not card or not card.get("url"):
        return None  # Skip toots without article links

    return {
        "id": status["id"],
        "title": card.get("title", ""),
        "source_link": card["url"],
        "author": card.get("author_name", ""),
        "published": status["created_at"],
        "description": card.get("description", ""),
        "toot_url": status.get("url", ""),
        "toot_content": strip_html(status.get("content", "")),
        "tags": ",".join(t["name"] for t in status.get("tags", [])),
        "created_at": datetime.now().isoformat(),
    }
```

### HTML Stripping Helper

Mastodon `content` is HTML. Strip it for plain text:

```python
from bs4 import BeautifulSoup


def strip_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
```

## Rate Limiting

Most public APIs have documented rate limits. Respect them.

### Mastodon Rate Limits

- **Public endpoints (no auth):** 300 requests per 5 minutes
- **Statuses endpoint:** Returns max 40 items per request
- A daily run for a news account (~30 posts/day) needs just 1 API page — very light

Rate limit headers in the response:
```
X-RateLimit-Limit: 300
X-RateLimit-Remaining: 298
X-RateLimit-Reset: 2026-03-23T10:05:00.000Z
```

Check these and back off if `Remaining` is low:

```python
def check_rate_limit(response: httpx.Response):
    """Warn if approaching rate limit."""
    remaining = int(response.headers.get("X-RateLimit-Remaining", 999))
    if remaining < 10:
        reset_at = response.headers.get("X-RateLimit-Reset", "")
        click.echo(f"Rate limit low: {remaining} remaining, resets at {reset_at}", err=True)
        time.sleep(30)
```

## Environment Variables

API-based resources typically need:

- **No auth** for public read endpoints (Mastodon, many open data portals)
- `JINA_API_TOKEN` if the resource fetches linked article content
- `LLM_BASE_URL`, `LLM_MODEL`, and optionally `LLM_API_KEY` if using AI filtering or summary generation

No `DOCLING_SERVE_URL` unless the linked content is PDFs.

## Schema

For a news aggregator API (like Mastodon @straits_times):

```python
{
    "id": "Mastodon status ID (natural key)",
    "title": "Linked article headline (from card)",
    "source_link": "URL to the original article",
    "author": "Article author or source name",
    "published": "Status creation timestamp (ISO 8601)",
    "category": "Classification label (e.g. 'legal')",
    "description": "Article description from OpenGraph",
    "toot_url": "URL to the Mastodon toot",
    "content_text": "Full article text via Jina Reader",
    "summary": "AI-generated summary for search and display",
    "created_at": "When this record was first imported",
}
```

## zeeker.toml Entry

```toml
[resource.<n>]
description = "<Description>"
fts_fields = ["title", "summary", "content_text"]
columns = {
    id = "Status ID from source platform",
    title = "Linked article headline",
    source_link = "URL to the original article",
    author = "Article author or source name",
    published = "Publication timestamp",
    category = "Content classification",
    description = "Article description from source",
    toot_url = "URL to the original social media post",
    content_text = "Full article text",
    summary = "AI-generated summary for search and display",
    created_at = "When this record was first imported"
}
```
