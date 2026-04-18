# Scraping Strategy Reference

Rate limiting, concurrency control, error handling, and cadence recommendations. This applies
to all source types — even RSS feeds need rate limiting for the enrichment step.

## Rate-Limiting Tiers

### Tier 1 — RSS Feed Enrichment (lightest touch)

The feed itself is a single request. Rate limiting applies to the Jina Reader and LLM inference calls
that process each entry.

- **Concurrency**: `asyncio.Semaphore(3)` — max 3 simultaneous enrichment calls
- **Delay**: 1–2 seconds between batches (built into semaphore release pattern)
- **Timeout**: 90 seconds per enrichment call (Jina Reader can be slow)
- **Retry**: 3 attempts with exponential backoff (2x, min 1s, max 10s)

### Tier 2 — Web Scraping (moderate)

Multiple requests to the same domain. Be respectful.

- **Discovery phase**: Sequential with `time.sleep(delay)` between each listing page
- **Detail phase**: Sequential by default; semaphore-bounded (3–5) if speed is needed
- **Delay**: 1–2 seconds between requests, with random jitter
- **Timeout**: 30 seconds per request
- **Retry**: 3 attempts with exponential backoff
- **Session**: Single `httpx.Client` for connection reuse
- **User-Agent**: Set a descriptive user agent identifying the bot

### Tier 3 — Docling Serve (PDF/HTML conversion)

Requests go to your own Docling Serve instance, not an external server. Rate limiting protects
the Docling server from overload, not the source (source PDFs are fetched once and may be
cached).

- **Concurrency**: Sequential by default (Docling is CPU/GPU intensive per document)
- **Delay**: 1–2 seconds between conversion requests
- **Timeout**: 300 seconds per conversion (large PDFs take time)
- **Retry**: 2 attempts (Docling failures are usually deterministic, retrying rarely helps)

## Concurrency Control

### Semaphore Pattern (async resources)

```python
import asyncio

MAX_CONCURRENT = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


async def rate_limited_fetch(url: str) -> str:
    """Fetch with concurrency limiting."""
    async with _semaphore:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
```

### Connection Pool (sync resources)

```python
import httpx

def create_client() -> httpx.Client:
    """Create an HTTP client with connection pooling."""
    return httpx.Client(
        timeout=REQUEST_TIMEOUT,
        limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        headers={"User-Agent": "ZeekerBot/1.0 (+https://data.zeeker.sg)"},
    )
```

## Adaptive Delay with Jitter

Fixed delays create predictable request patterns. Add random jitter to be less bot-like and
avoid thundering-herd effects:

```python
import random


def polite_sleep(base: float = REQUEST_DELAY_BASE, jitter: float = REQUEST_DELAY_JITTER):
    """Sleep with random jitter."""
    delay = base + random.uniform(-jitter, jitter)
    delay = max(0.1, delay)  # Never sleep less than 100ms
    time.sleep(delay)
```

### Respect Retry-After Headers

If a server returns HTTP 429 (Too Many Requests) or 503 with a `Retry-After` header, honour it:

```python
def handle_rate_limit(response: httpx.Response) -> float:
    """Extract Retry-After delay from response headers."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            return 30.0  # Default backoff
    return 30.0
```

## Circuit Breaker

If the source starts failing consistently, stop hammering it. Track consecutive failures and
pause or abort after a threshold:

```python
class CircuitBreaker:
    """Simple circuit breaker for request failures."""

    def __init__(self, max_failures: int = MAX_CONSECUTIVE_FAILURES, cooldown: float = 60.0):
        self.max_failures = max_failures
        self.cooldown = cooldown
        self.consecutive_failures = 0
        self.total_failures = 0
        self.total_successes = 0

    def record_success(self):
        self.consecutive_failures = 0
        self.total_successes += 1

    def record_failure(self):
        self.consecutive_failures += 1
        self.total_failures += 1

    @property
    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures

    def wait_if_needed(self):
        if self.is_open:
            click.echo(
                f"Circuit breaker: {self.consecutive_failures} consecutive failures. "
                f"Cooling down for {self.cooldown}s...",
                err=True,
            )
            time.sleep(self.cooldown)
            self.consecutive_failures = 0  # Reset after cooldown

    def summary(self) -> str:
        total = self.total_successes + self.total_failures
        return f"{self.total_successes}/{total} succeeded, {self.total_failures} failed"
```

Use it in the fetch loop:

```python
breaker = CircuitBreaker()

for item in items_to_process:
    breaker.wait_if_needed()
    try:
        result = fetch_item(item)
        breaker.record_success()
    except Exception as e:
        breaker.record_failure()
        click.echo(f"Failed: {e}", err=True)
        continue

click.echo(f"Results: {breaker.summary()}")
```

## Cadence Tiers

The cadence determines how often the resource should be rebuilt or updated. Assess the source's
update frequency and recommend the appropriate tier.

### Tier 1 — Daily

**When**: Source publishes multiple times per day. News feeds, press releases, daily legal
updates, government announcements.

**Configuration**:
- Cron: `0 3 * * *` (daily at 3 AM UTC)
- Mode: Incremental — only fetch items newer than last run
- Pagination: Early termination after N consecutive known items
- Timeout/circuit breaker: Tight — fail fast, retry tomorrow

**Comment block**:
```python
# CADENCE: Daily (Tier 1)
# Source publishes ~N new items per weekday
# Recommended cron: 0 3 * * *
# Strategy: Incremental — fetch only items newer than last run
```

### Tier 2 — Weekly

**When**: Source updates a few times per week. Government gazettes, regulatory updates, blog
archives, journal pre-prints.

**Configuration**:
- Cron: `0 3 * * 1` (Monday 3 AM UTC)
- Mode: Incremental recommended, full rebuild acceptable
- Pagination: Full scan of recent pages, early termination on older content

**Comment block**:
```python
# CADENCE: Weekly (Tier 2)
# Source updates a few times per week
# Recommended cron: 0 3 * * 1
# Strategy: Incremental recommended
```

### Tier 3 — Monthly

**When**: Source updated rarely. Reference content, legislation databases, PDF document
collections, static knowledge bases.

**Configuration**:
- Cron: `0 3 1 * *` (1st of month, 3 AM UTC)
- Mode: Full rebuild is fine given low volume
- Concurrency: Can be slightly more aggressive since runs are infrequent

**Comment block**:
```python
# CADENCE: Monthly (Tier 3)
# Source content changes infrequently
# Recommended cron: 0 3 1 * *
# Strategy: Full rebuild acceptable
```

### Tier 4 — One-shot / Ad-hoc

**When**: Historical archives, one-time data imports, backfill jobs. Run once, then only re-run
if source changes or you need to re-process.

**Configuration**:
- Cron: None — manual trigger only (`workflow_dispatch`)
- Mode: Full archive crawl
- Concurrency: Higher limits acceptable (this is a one-time event)
- Timeout: More lenient error budget — get as much as possible in one pass

**Comment block**:
```python
# CADENCE: One-shot (Tier 4)
# Historical/archival content — run manually as needed
# Strategy: Full archive crawl
```

## Determining Cadence from the Source

During site analysis, check dates on the listing items:

- Most recent items from today/yesterday → **Tier 1 (daily)**
- Most recent from this week → **Tier 2 (weekly)**
- Most recent from this month or older → **Tier 3 (monthly)**
- Content appears static with no dates → **Tier 4 (one-shot)**
- No dates visible → ask the user

Also consider the volume: a feed with 5 items/day is very different from one with 500.
High volume + high frequency = daily with strict incremental mode. Low volume + low
frequency = monthly full rebuild is fine.

## Cadence Affects Scraping Configuration

| Setting | Tier 1 (Daily) | Tier 2 (Weekly) | Tier 3 (Monthly) | Tier 4 (One-shot) |
|---------|----------------|-----------------|-------------------|--------------------|
| Incremental mode | Mandatory | Recommended | Optional | No |
| Pagination stop | After 5 known items | After 10 known | Full scan | Full scan |
| Concurrency | 3 | 3–5 | 5 | 5–10 |
| Circuit breaker | 5 failures | 10 failures | 15 failures | 20 failures |
| Request timeout | 30s | 30s | 60s | 120s |

## Checkpoint and Resume

Large archive crawls (thousands of pages) should not be run as a single uninterrupted session.
They can fail mid-way (network errors, rate limits, server maintenance), and even when they
succeed, tying up a machine for 15 hours is impractical. Instead, use a checkpoint pattern
that lets the crawl be spread across multiple runs — even across multiple days.

### The Checkpoint File

Write progress to a simple JSON file after each batch of work. The resource checks for this
file on startup and resumes from where it left off.

```python
import json
from pathlib import Path

CHECKPOINT_FILE = Path("checkpoint_{resource_name}.json")


def load_checkpoint() -> dict:
    """Load checkpoint state, or return defaults for a fresh run."""
    if CHECKPOINT_FILE.exists():
        data = json.loads(CHECKPOINT_FILE.read_text())
        click.echo(f"Resuming from checkpoint: page {data.get('last_page', 0)}, "
                    f"{data.get('items_processed', 0)} items processed")
        return data
    return {"last_page": 0, "items_processed": 0, "items_collected": []}


def save_checkpoint(state: dict):
    """Persist checkpoint state to disk."""
    CHECKPOINT_FILE.write_text(json.dumps(state, indent=2))


def clear_checkpoint():
    """Remove checkpoint file after successful completion."""
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        click.echo("Checkpoint cleared — crawl complete")
```

### Batch Size Limiting

Instead of crawling the entire archive in one go, process a configurable number of pages per
run. This lets the user spread the work across days by simply running `uv run zeeker build`
repeatedly.

```python
# Maximum pages to process per run (0 = unlimited)
MAX_PAGES_PER_RUN = 50

# Maximum items to process per run (0 = unlimited)
MAX_ITEMS_PER_RUN = 500
```

### Integrating Checkpoints into the Crawl Loop

```python
def discover_all_pages(
    base_url: str,
    existing_ids: set,
    client: httpx.Client,
) -> List[Dict[str, Any]]:
    """Paginate through listing pages with checkpoint support."""
    checkpoint = load_checkpoint()
    start_page = checkpoint.get("last_page", 0) + 1
    all_items = checkpoint.get("items_collected", [])
    items_this_run = 0
    total_pages = None  # Discovered from first response

    try:
        page = start_page
        while True:
            # Batch limit check
            pages_this_run = page - start_page
            if MAX_PAGES_PER_RUN > 0 and pages_this_run >= MAX_PAGES_PER_RUN:
                click.echo(
                    f"Batch limit reached: {pages_this_run} pages, "
                    f"{items_this_run} items this run. "
                    f"Run again to continue from page {page}."
                )
                save_checkpoint({
                    "last_page": page - 1,
                    "items_processed": len(all_items),
                    "items_collected": all_items,
                })
                return all_items

            if MAX_ITEMS_PER_RUN > 0 and items_this_run >= MAX_ITEMS_PER_RUN:
                click.echo(
                    f"Item limit reached: {items_this_run} items this run. "
                    f"Run again to continue from page {page}."
                )
                save_checkpoint({
                    "last_page": page - 1,
                    "items_processed": len(all_items),
                    "items_collected": all_items,
                })
                return all_items

            url = f"{base_url}?CurrentPage={page}"
            click.echo(f"Fetching page {page}"
                        + (f" of {total_pages}" if total_pages else "")
                        + f" ({len(all_items)} items so far)")

            response = client.get(url)
            if response.status_code == 404:
                break
            response.raise_for_status()

            items = parse_listing_page(response.text)
            if not items:
                break

            # Dedup against existing database
            new_items = [i for i in items if i["id"] not in existing_ids]
            all_items.extend(new_items)
            items_this_run += len(new_items)

            # Checkpoint after every page
            save_checkpoint({
                "last_page": page,
                "items_processed": len(all_items),
                "items_collected": all_items,
            })

            page += 1
            polite_sleep()

    except (httpx.RequestError, KeyboardInterrupt) as e:
        click.echo(f"Interrupted at page {page}: {e}", err=True)
        click.echo("Progress saved. Run again to resume.")
        save_checkpoint({
            "last_page": page - 1,
            "items_processed": len(all_items),
            "items_collected": all_items,
        })
        return all_items

    # Full crawl complete
    clear_checkpoint()
    return all_items
```

### Two-Phase Crawls: Discovery Then Content

For sources where content extraction is expensive (Docling PDF conversion, Jina Reader), split
the crawl into two phases with separate checkpoints:

**Phase 1 — Discovery:** Crawl listing pages, collect catalog metadata (case name, citation,
date, tags, URLs). This is fast — just HTML parsing. Store the catalog records in the database
with `content_text` empty.

**Phase 2 — Content extraction:** Iterate over catalog records that have empty `content_text`.
For each, fetch the content (via Docling, Jina, or direct scraping). Update the record.

This separation has several advantages:

- Discovery is fast and can complete in one run even for large archives
- Content extraction can be spread across many runs (50–100 documents per run)
- If Docling Serve goes down or an API key expires, you don't lose discovery progress
- The database is immediately useful for search by title/tags even before content is extracted
- You can prioritize content extraction (e.g., newest judgments first)

```python
CONTENT_BATCH_SIZE = 50  # Documents to extract content for per run


def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """Phase 1: Discover items from listing pages."""
    # ... standard discovery with checkpoint ...
    return catalog_records


def backfill_content(existing_table: Table):
    """Phase 2: Extract content for records missing content_text.

    Call this separately or at the end of fetch_data.
    """
    if not existing_table:
        return

    # Find records needing content extraction
    pending = list(existing_table.rows_where(
        "content_text IS NULL OR content_text = ''",
        limit=CONTENT_BATCH_SIZE,
    ))

    if not pending:
        click.echo("All records have content — nothing to backfill")
        return

    click.echo(f"Backfilling content for {len(pending)} of "
                f"{existing_table.count} records")

    for i, record in enumerate(pending):
        click.echo(f"[{i+1}/{len(pending)}] Extracting: {record['title'][:60]}...")
        try:
            content = extract_content(record["source_url"])
            existing_table.update(record["id"], {"content_text": content})
            click.echo(f"  → {len(content)} chars extracted")
        except Exception as e:
            click.echo(f"  → Failed: {e}", err=True)
        polite_sleep()

    remaining = existing_table.count_where(
        "content_text IS NULL OR content_text = ''"
    )
    if remaining > 0:
        click.echo(f"{remaining} records still pending. Run again to continue.")
```

### Summary Generation as a Third Phase

If AI summaries are used, they can be a separate backfill pass too. This avoids blocking the
entire crawl on LLM server availability:

```python
SUMMARY_BATCH_SIZE = 50


def backfill_summaries(existing_table: Table):
    """Phase 3: Generate summaries for records missing them."""
    pending = list(existing_table.rows_where(
        "summary IS NULL OR summary = ''",
        limit=SUMMARY_BATCH_SIZE,
    ))

    if not pending:
        click.echo("All records have summaries")
        return

    click.echo(f"Generating summaries for {len(pending)} records")

    for i, record in enumerate(pending):
        if not record.get("content_text"):
            continue  # Skip records without content yet

        click.echo(f"[{i+1}/{len(pending)}] Summarizing: {record['title'][:60]}...")
        try:
            summary = get_summary(record["content_text"])
            existing_table.update(record["id"], {"summary": summary})
        except Exception as e:
            click.echo(f"  → Failed: {e}", err=True)
        polite_sleep(base=0.5)  # AI calls are fast, shorter delay
```

### When to Use Checkpoints

| Archive size | Approach |
|-------------|----------|
| < 100 items | No checkpoint needed — single run |
| 100–500 items | Optional — depends on content extraction time |
| 500–5,000 items | Recommended — batch discovery + content backfill |
| > 5,000 items | Essential — two-phase with aggressive batching |

### Checkpoint File Location

Store the checkpoint file in the project root (next to `zeeker.toml`). Add it to `.gitignore`:

```
# Crawl checkpoints (temporary)
checkpoint_*.json
```

### User Communication

When a batch limit is reached, always tell the user clearly:

```
Batch limit reached: 50 pages, 487 items this run.
Total progress: 487 of ~10,500 items discovered.
Run `uv run zeeker build` again to continue from page 51.
Estimated runs remaining: ~20
```

This lets the user plan: "I'll run this once a day for three weeks to build the full archive,
then switch to daily incremental mode."
