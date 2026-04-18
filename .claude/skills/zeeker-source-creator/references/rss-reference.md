# RSS Feed Resource Reference

Use this reference when the source has an RSS or Atom feed. This is the lightest-touch source
type — one HTTP request gets the feed, then individual entries are enriched with content
extraction and AI summaries.

## When to Use

- The index page has a `<link rel="alternate" type="application/rss+xml">` in its `<head>`
- The user provides a direct RSS/Atom feed URL (`.xml`, `.rss`, `/feed`)
- The page is a news aggregator linking to external articles (even without a formal feed, the
  Jina Reader + summary pattern applies)

## Detection During Site Analysis

When inspecting the index page, check for feed links:

```python
from bs4 import BeautifulSoup

soup = BeautifulSoup(html, "html.parser")
feed_links = soup.find_all("link", attrs={"type": re.compile(r"rss|atom")})
for link in feed_links:
    print(f"Feed found: {link.get('href')}")
```

Also look for common feed URL patterns: `/feed`, `/rss`, `/atom.xml`, `/index.xml`,
`/Portals/0/RSS/`. If no feed is found in the HTML, try appending these to the base URL.

## Architecture

```
RSS Feed (single fetch)
    ↓
feedparser (parse entries)
    ↓
For each entry:
    ├─ Jina Reader → content_text (full article)
    ├─ AI Summary → summary (~100 words, search-optimized)
    └─ Feed metadata → title, author, date, category, source_link
    ↓
Flat table (one row per article)
FTS on: title, summary, content_text
```

## Schema

```python
{
    "id": "sha256 hash of date + title (or feed-provided ID)",
    "title": "Article headline from the feed",
    "source_link": "URL to the original article",
    "author": "Byline from the feed entry",
    "category": "Feed category or source name",
    "published": "ISO 8601 date from the feed",
    "content_text": "Full article text extracted via Jina Reader",
    "summary": "AI-generated ~100 word search-optimized summary",
    "created_at": "ISO 8601 timestamp when record was inserted",
}
```

## Dependencies

```
uv add feedparser httpx tenacity openai
```

`JINA_API_TOKEN` and `LLM_BASE_URL` + `LLM_MODEL` required. `LLM_API_KEY` only needed for
cloud LLM providers (not needed for local servers like Ollama). The `openai` Python package
is used as a client library — it works with any OpenAI-compatible API server, not just OpenAI.
See `resource-patterns.md` for env var handling.

## Resource Module Template

The resource module for an RSS feed follows this structure. Adapt the constants at the top
(feed URL, system prompt, skip patterns) for the specific source.

### Feed Parsing

```python
import feedparser

FEED_URL = "<RSS feed URL>"

def discover_entries() -> list[dict]:
    """Parse the RSS feed and return raw entries."""
    click.echo(f"Fetching feed from {FEED_URL}")
    feed = feedparser.parse(FEED_URL)
    click.echo(f"Found {len(feed.entries)} entries in feed")
    return feed.entries
```

### Content Extraction via Jina Reader

Jina Reader converts any URL into clean markdown text. Call it with the article's source link.
Use a semaphore to limit concurrent requests (the feed may have 50+ entries).

```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

MAX_CONCURRENT_REQUESTS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=1, max=10))
async def extract_content(url: str) -> str:
    """Extract article content via Jina Reader."""
    token = os.environ.get("JINA_API_TOKEN")
    if not token:
        click.echo("JINA_API_TOKEN not set", err=True)
        return ""

    async with _semaphore:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.get(
                f"https://r.jina.ai/{url}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Retain-Images": "none",
                    "X-Target-Selector": "article",
                },
            )
            r.raise_for_status()
        return r.text
```

The `X-Target-Selector` header tells Jina which part of the page to extract. Use `article` for
news sites, or identify the correct selector during site analysis. Other useful Jina headers:

- `X-Retain-Images: none` — strip images (you want text for FTS)
- `X-Remove-Selector: .sidebar,.ads,.comments` — strip noise elements

### Entry Processing

Process each feed entry: extract content, generate summary, assemble the record.

```python
async def process_entry(entry: dict) -> Optional[dict]:
    """Process a single RSS entry into a database record."""
    try:
        published = parse_feed_date(entry.get("published", ""))
        record = {
            "id": entry.get("id", make_id(published, entry.get("title", ""))),
            "title": entry.get("title", ""),
            "source_link": entry.get("link", ""),
            "author": entry.get("author", ""),
            "category": entry.get("category", ""),
            "published": published,
            "created_at": datetime.now().isoformat(),
        }

        click.echo(f"Processing: {record['title']}")

        # Extract content
        try:
            record["content_text"] = await extract_content(record["source_link"])
            click.echo(f"  → Content extracted ({len(record['content_text'])} chars)")
        except Exception as e:
            click.echo(f"  → Content extraction failed: {e}", err=True)
            record["content_text"] = f"Article: {record['title']}"

        # Generate summary
        try:
            record["summary"] = await get_summary(record["content_text"])
            click.echo(f"  → Summary generated")
        except Exception as e:
            click.echo(f"  → Summary failed: {e}", err=True)
            record["summary"] = ""

        return record
    except Exception as e:
        click.echo(f"Error processing entry: {e}", err=True)
        return None
```

### The fetch_data Function

Wire it all together with incremental update support:

```python
async def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """Fetch new RSS entries, extract content, generate summaries."""
    entries = discover_entries()

    # Incremental: skip known entries
    existing_ids = set()
    if existing_table:
        existing_ids = {row["id"] for row in existing_table.rows}

    # Filter and process
    tasks = []
    skip_count = 0
    for entry in entries:
        published = parse_feed_date(entry.get("published", ""))
        entry_id = entry.get("id", make_id(published, entry.get("title", "")))

        if entry_id in existing_ids:
            skip_count += 1
            continue

        tasks.append(asyncio.create_task(process_entry(entry)))

    results = await asyncio.gather(*tasks)
    new_records = [r for r in results if r is not None]

    click.echo(f"Done: {len(new_records)} new, {skip_count} skipped")
    return new_records
```

Note: the function is `async` — add `async = true` handling or mark in zeeker config if the
framework requires it. The reference project uses async for its headlines resource.

### Date Parsing Helper

RSS feeds use various date formats. Parse them defensively:

```python
from email.utils import parsedate_to_datetime


def parse_feed_date(date_str: str) -> str:
    """Parse an RSS date string to ISO 8601. Falls back to now()."""
    if not date_str:
        return datetime.now().isoformat()
    try:
        return parsedate_to_datetime(date_str).isoformat()
    except Exception:
        try:
            return datetime.strptime(date_str, "%d %B %Y %H:%M:%S").isoformat()
        except Exception:
            return datetime.now().isoformat()
```

## Skip Patterns

Some RSS entries should be skipped. Common patterns from the reference project:

- **Advertisements**: title starts with "ADV:" or "ADV "
- **Too old**: entries older than a configurable max age (default 60 days for daily feeds)
- **Already processed**: ID exists in `existing_table` or date is before last update

Implement these as a `should_skip()` function with clear logging of skip reasons.

## zeeker.toml Entry

```toml
[resource.<name>]
description = "<Human-readable description>"
fts_fields = ["title", "summary", "content_text"]
columns = {
    id = "Unique identifier for this entry",
    title = "Article headline",
    source_link = "URL to the original article",
    author = "Article byline",
    category = "News source or feed category",
    published = "Publication date (ISO 8601)",
    content_text = "Full article text extracted via Jina Reader",
    summary = "AI-generated summary for search and display",
    created_at = "When this record was first imported"
}
```

## Cadence

RSS feeds are typically Tier 1 (daily) or Tier 2 (weekly). Check the dates of the most recent
entries in the feed to determine the right cadence. A news feed with daily entries → daily cron.
A blog with weekly posts → weekly cron.
