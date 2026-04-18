# Resource Patterns

Common patterns shared across all Zeeker resource types.

## Resource Module Structure

Every resource module follows this skeleton:

```python
"""
<Resource Name> resource for <description>.

Cadence: <Daily|Weekly|Monthly|One-shot> (Tier <1-4>)
Source: <URL>
Strategy: <Incremental|Full rebuild>
"""

import hashlib
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import click
import httpx
from sqlite_utils.db import Table


# =============================================================================
# CONFIGURATION
# =============================================================================
MAX_CONCURRENT_REQUESTS = 3
REQUEST_DELAY_BASE = 1.0
REQUEST_DELAY_JITTER = 0.5
REQUEST_TIMEOUT = 30.0
MAX_CONSECUTIVE_FAILURES = 5
MAX_RETRIES = 3

# =============================================================================
# SCHEMA VERSION: 1.0
# Last modified: <date>
# =============================================================================


def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """
    Fetch data for the <resource_name> table.

    Args:
        existing_table: sqlite-utils Table object if table exists, None for new table.
                       Use this to check for existing data and avoid duplicates.

    Returns:
        List of records to insert into database.
    """
    ...


def transform_data(raw_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Optional data transformation before database insertion."""
    return raw_data
```

For fragment-enabled resources, also implement:

```python
def fetch_fragments_data(
    existing_fragments_table: Optional[Table],
    main_data_context: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Create content fragments from each item."""
    ...


def transform_fragments_data(
    raw_fragments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Optional fragment transformation before database insertion."""
    return raw_fragments
```

## ID Strategy

Every record needs a stable, unique identifier for deduplication during incremental updates.
Choose the right strategy based on what the source provides:

**IDs are system identifiers, not content.** Never use meaningful data values as IDs — citation
numbers, paragraph numbers, case references, section numbers, and similar values are *content*
that users query, display, and search. They belong in their own columns, not in the `id` field.
IDs should be opaque and stable: if the source corrects a citation, renumbers paragraphs, or
changes a reference code, existing IDs must not break. Always use hash-based IDs derived from
the source URL or a combination of stable structural properties.

**URL hash** — The default and most reliable strategy. Hash the source URL to a fixed length.
Good for any source where every item has a unique URL. For fragments, hash the parent ID plus
the fragment's position (reading order index).

**Composite hash** — When no single URL identifies the item, hash together multiple stable
properties: `source_url + date + title` or similar. Warn the user about collision risks — make
sure the combination is genuinely unique.

Use SHA-256 truncated to 12 hex characters for new resources (more collision-resistant than MD5):

```python
def make_id(*elements: str) -> str:
    """Generate a stable ID from one or more string elements."""
    joined = "|".join(str(e) for e in elements)
    return hashlib.sha256(joined.encode()).hexdigest()[:12]
```

**What goes in content columns, not in IDs:**

- Citation numbers (e.g., "[2026] SGHC 61") → `citation` column
- Paragraph numbers → `paragraph_number` column
- Case references → `case_number` column
- Section numbers → `section_number` column
- Document reference codes → `reference` column

These are valuable data for search, display, and faceting. Store them as queryable fields.

## Schema Conventions

The schema follows from what the source provides. Organize fields into these groups:

**Identity** — `id` (stable identifier), `source_url` (where this record came from)

**Source metadata** — Whatever the source gives you: `title`, `author`, `published`,
`category`, `section`, `description`. Use the source's own terminology. If the source calls it
"category", store it as `category`, not `type` or `classification`.

**Content** — `content_text` (full extracted text, cleaned, ready for FTS). For RSS resources
that fetch linked articles, this is the Jina-extracted content. For scraping, this is the
BeautifulSoup-extracted text. For PDFs, this is the Docling-extracted text.

**Display** — `summary` (AI-generated, ~100 words, search-optimized). Include this for flat-table
resources where rows are discrete items. The summary serves both display (readable in the
Datasette table view) and search (contains key domain terms). Not needed for fragment tables
since the fragments themselves are the search units.

**Housekeeping** — `created_at` (ISO 8601 string, when first inserted). Keep this minimal.

### Fragment Schema

For fragment-enabled resources, the fragments table has:

```
id              — hash of (item_id + fragment_order), opaque identifier
item_id         — foreign key to the catalog table
fragment_order  — reading order position (integer, zero-indexed)
content_type    — "heading" | "paragraph" | "table" | "list" | "formula" | "code"
content_text    — the extracted text content
char_count      — length of content_text
```

Add source-specific content fields as needed. For example, court judgments have numbered
paragraphs — store `paragraph_number` as a content field (integer), not as the ID.
Similarly, if fragments fall under section headings, store the `heading` text as a field.
These content fields are valuable for search and display — a user searching for paragraph 33
of a specific judgment can query `paragraph_number = 33` and `item_id = X`.

## FTS Field Selection

FTS fields determine what users can search in the Datasette interface.

**Flat table (RSS, simple scraping):**
```toml
fts_fields = ["title", "summary", "content_text"]
```
Three-tier search: title for direct hits, summary for topical discovery, full content for deep
search.

**Catalog table (fragment-enabled resources):**
```toml
fts_fields = ["title"]
```
Document-level search by title. The detailed search happens on fragments.

**Fragments table:**
```toml
fragments_fts_fields = ["content_text"]
```
Paragraph-level search. The `content_type` field becomes a useful Datasette facet.

## AI Summary Generation

For flat-table resources, generate a search-optimized summary via AI. The summary should extract
key searchable terms and concepts — not just be a "nice paragraph". Use a system prompt tuned
for the domain.

Template for the summary function:

```python
SUMMARY_SYSTEM_PROMPT = """
As an expert in <domain>, provide summaries of <content_type> for <audience>.
These summaries should highlight the critical <domain_terms>, relevant <key_concepts>,
and implications. The summary should be 1 narrative paragraph, no longer than 100 words,
efficiently delivering the key insights for quick comprehension.
"""


async def get_summary(text: str) -> str:
    """Generate a search-optimized summary using any OpenAI-compatible LLM server."""
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

    if not base_url:
        click.echo("LLM_BASE_URL not set — skipping summary", err=True)
        return ""

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=3, timeout=60)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": f"Summarise this:\n\n{text[:4000]}"},
            ],
        )
        return response.choices[0].message.content
    except Exception as e:
        click.echo(f"Summary generation failed: {e}", err=True)
        return ""
```

Adapt the system prompt to the specific domain. For legal news, emphasize legal aspects,
precedents, implications. For government data, emphasize policy, regulatory impact, key figures.
For academic content, emphasize methodology, findings, significance.

## Environment Variable Handling

Check for required env vars at the point of use, not at module import. Log clear error messages
and fall back gracefully. Match the reference project's pattern:

```python
def get_env_or_warn(var_name: str, required_for: str) -> str:
    """Get an environment variable with a clear warning if missing."""
    value = os.environ.get(var_name, "")
    if not value:
        click.echo(
            f"{var_name} not set — {required_for} will be skipped", err=True
        )
    return value
```

### Environment Variables by Source Type

| Variable | RSS | Scraping (simple) | Scraping (Docling) | PDF |
|----------|-----|--------------------|--------------------|-----|
| `JINA_API_TOKEN` | Required | — | — | — |
| `LLM_BASE_URL` | Required | Optional | Optional | Optional |
| `LLM_API_KEY` | If cloud LLM | If cloud LLM | If cloud LLM | If cloud LLM |
| `LLM_MODEL` | Required | Optional | Optional | Optional |
| `DOCLING_SERVE_URL` | — | — | Required | Required |
| `DOCLING_SERVE_API_KEY` | — | — | Optional | Optional |

## Incremental Update Pattern

Every resource should support incremental updates via the `existing_table` parameter. The
pattern is:

```python
def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    # Extract existing IDs for dedup
    existing_ids = set()
    if existing_table:
        existing_ids = {row["id"] for row in existing_table.rows}

    # Fetch new items
    all_items = discover_items()

    # Filter out known items
    new_items = [item for item in all_items if item["id"] not in existing_ids]

    click.echo(f"Found {len(all_items)} total, {len(new_items)} new")
    return new_items
```

For time-based incremental updates (RSS feeds with dates), also check the `_zeeker_updates`
table for the last update timestamp and skip items older than that. See the headlines resource
in the reference project for this pattern.

## Logging and Progress

Use `click.echo` consistently for progress output. Log:

- Total items discovered
- Items skipped (duplicates, too old, errors)
- Items being processed (with title or identifier)
- Per-item status (success, fallback, error)
- Final summary (N new, M skipped, K errors)

```python
click.echo(f"Fetching items from {source_url}")
click.echo(f"Found {total} items, {new_count} new, {skip_count} skipped")
click.echo(f"Processing: {item['title']}")
click.echo(f"  → Content extracted ({len(text)} chars)")
click.echo(f"  → Summary generated")
```

## Metadata: Fill in Everything You Can

The skill should maximize the metadata passed to `zeeker add` and written into `zeeker.toml`.
Every field you populate is one less thing the user has to figure out, and it directly improves
the Datasette UI (facets, sorting, column descriptions all appear in the frontend).

### What to Infer from Site Analysis

During Step 1-3, you discover a lot about the source. Map these findings to metadata:

| What you discovered | Where it goes |
|---------------------|---------------|
| Source URL | `zeeker.toml` `[project]` `source` field |
| Source name (e.g., "eLitigation") | `zeeker.toml` `[project]` `title` and `description` |
| Categorical columns (court, category, section) | `--facets` flags on `zeeker add` |
| Date column name | `--sort "<date_col> desc"` flag on `zeeker add` |
| Which text fields exist | `--fts-fields` flags on `zeeker add` |
| Whether content is long | `--fragments` flag on `zeeker add` |
| Whether enrichment is async | `--async` flag on `zeeker add` |
| What each column contains | `[resource.<n>.columns]` section in `zeeker.toml` |
| Data license (government = often CC-BY-4.0) | `zeeker.toml` `[project]` `license` fields |
| Update frequency | CLAUDE.md cadence entry + workflow cron |

### Building the `zeeker add` Command

Construct the `zeeker add` command with as many flags as the source justifies. For example,
for the eLitigation judgments resource:

```bash
uv run zeeker add judgments --fragments --async \
  --description "Singapore court judgments from eLitigation with paragraph-level search" \
  --fts-fields case_name --fts-fields summary --fts-fields content_text \
  --fragments-fts-fields content_text \
  --facets court --facets decision_date \
  --sort "decision_date desc" \
  --size 25
```

For the Straits Times Mastodon legal news feed:

```bash
uv run zeeker add legal_news --async \
  --description "Legal news from The Straits Times filtered for law-related articles" \
  --fts-fields title --fts-fields summary --fts-fields content_text \
  --facets category \
  --sort "published desc" \
  --size 20
```

### zeeker.toml Column Descriptions

After `zeeker add` creates the resource entry, update `zeeker.toml` to add a `[resource.<n>.columns]`
section with descriptions for every column. These appear in Datasette's UI as column help text.

Write descriptions from the user's perspective -- what would someone browsing the data want to
know about each column?

**Example: eLitigation judgments**

```toml
[resource.judgments]
description = "Singapore court judgments from eLitigation with paragraph-level search"
fragments = true
fts_fields = ["case_name", "summary", "content_text"]
fragments_fts_fields = ["content_text"]
facets = ["court", "decision_date"]
sort = "decision_date desc"
size = 25

[resource.judgments.columns]
id = "Unique identifier (hash of source URL)"
citation = "Neutral citation e.g. [2026] SGHC 61"
case_name = "Name of the case e.g. YAQ v YAR"
case_numbers = "Court file reference numbers (pipe-separated if multiple)"
decision_date = "Date the judgment was delivered (ISO 8601)"
court = "Court abbreviation: SGCA, SGHC, SGHCF, SGDC, SGFC, SGMC"
subject_tags = "Legal subject classifications from eLitigation (JSON array)"
source_url = "URL to the judgment page on eLitigation"
pdf_url = "URL to the PDF version of the judgment"
content_text = "Full judgment text as markdown"
summary = "AI-generated summary highlighting key legal points"
created_at = "When this record was first imported"
```

**Example: RSS news feed**

```toml
[resource.legal_news]
description = "Legal news from The Straits Times filtered for law-related content"
fts_fields = ["title", "summary", "content_text"]
facets = ["category"]
sort = "published desc"
size = 20

[resource.legal_news.columns]
id = "Unique identifier (hash of source URL)"
title = "Article headline"
source_link = "URL to the original article on Straits Times"
author = "Article byline"
published = "Publication date (ISO 8601)"
category = "Content classification: legal"
description = "Article description from OpenGraph metadata"
toot_url = "URL to the Mastodon post where this article was found"
content_text = "Full article text extracted via Jina Reader"
summary = "AI-generated summary for search and display"
created_at = "When this record was first imported"
```

**Example: PDF collection with fragments**

```toml
[resource.guidelines]
description = "CCCS competition law guidelines with paragraph-level search"
fragments = true
fts_fields = ["title", "content_text"]
fragments_fts_fields = ["content_text"]
facets = ["has_table", "has_figure"]

[resource.guidelines.columns]
id = "Unique identifier (hash of PDF URL)"
source_url = "URL to the PDF file"
title = "Document title"
content_text = "Full document text as markdown"
page_count = "Number of pages in the PDF"
created_at = "When this document was first processed"
```

### Project-Level Metadata

When scaffolding a new project with `zeeker init`, immediately update `zeeker.toml` with:

```toml
[project]
name = "singapore-legal-data"
database = "singapore-legal-data.db"
title = "Singapore Legal Data"
description = "Searchable Singapore legal data — court judgments, legislation, and regulatory guidelines"
license = "CC-BY-4.0"
license_url = "https://creativecommons.org/licenses/by/4.0/"
source = "https://www.elitigation.sg/gd/"
```

Infer the license from the source:
- Check the source site's footer, terms of use page, or open data license page
- Present what you found to the user and ask them to confirm
- **Do not assume a license** -- even government sites vary (Singapore Open Data Licence,
  CC-BY-4.0, custom terms, or no license stated)
- If no license information is found on the site, ask the user directly
- For the database structure itself (schema, code), MIT is standard
- The `license` field in zeeker.toml applies to the *data*, not the code

### CLAUDE.md Source-Specific Notes

`zeeker add` auto-generates basic resource documentation. Append source-specific details
that Zeeker cannot infer. Add these after the auto-generated section:

```markdown
### `judgments` Resource (Additional Notes)
- **Source:** https://www.elitigation.sg/gd/
- **Cadence:** Daily (Tier 1) -- new judgments published regularly
- **Estimated volume:** ~10,500 existing judgments, ~5-10 new per week
- **Scraping strategy:** Page-number pagination (1,053 pages), incremental stop after 5
  consecutive known citations. Checkpoint/resume enabled for initial archive crawl.
- **Content extraction:** HTML parsing for catalog metadata, PDF via Docling Serve for
  full judgment text and paragraph-level fragments.
- **Environment Variables:**
  - `DOCLING_SERVE_URL` -- Docling Serve instance for PDF conversion
  - `LLM_BASE_URL` + `LLM_MODEL` -- LLM server for AI summaries
  - `LLM_API_KEY` -- only if using a cloud LLM provider
  - `S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` -- deployment
- **Batch settings:** `MAX_PAGES_PER_RUN=50`, `CONTENT_BATCH_SIZE=50`
- **Court codes:** SGCA (Court of Appeal), SGHC (High Court), SGHCF (High Court Family),
  SGDC (District Court), SGFC (Family Court), SGMC (Magistrate's Court)
```

This gives future Claude (or a human developer) everything they need to understand, maintain,
and modify the resource without re-doing the site analysis.

## Content Filtering

Some sources are firehoses — they publish content across many topics, but the resource only
needs a subset. For example, a general news feed where you only want legal news. Add a
filtering step between discovery and enrichment.

### When to Filter

Filter when the source publishes broadly but the database has a narrow focus. Signals:

- The user says "I only want legal/tech/finance articles from this source"
- The source is a general news outlet, social media account, or aggregator
- The zeeker project is domain-specific (e.g., a legal data project)

### Strategy A — Keyword Matching (fast, no API cost)

Define a list of domain-specific keywords. Keep items where the title or description matches
any keyword. Good as a first pass or when API costs matter.

```python
LEGAL_KEYWORDS = [
    "court", "judge", "law", "legal", "legislation", "tribunal", "verdict",
    "sentence", "conviction", "plaintiff", "defendant", "prosecution",
    "attorney", "lawyer", "solicitor", "regulation", "statute", "appeal",
    "injunction", "arbitration", "judicial", "ruling", "indictment",
    # Domain-specific additions:
    "MAS", "AGC", "MinLaw", "High Court", "SGCA", "SGHC",
]


def matches_keywords(title: str, description: str = "") -> bool:
    """Check if text matches any domain keyword (case-insensitive)."""
    text = f"{title} {description}".lower()
    return any(kw.lower() in text for kw in LEGAL_KEYWORDS)
```

### Strategy B — AI Classification (accurate, costs per item)

Send the item's title and description to an LLM for classification. More accurate for
nuanced cases where keywords miss relevant articles or catch false positives.

```python
async def classify_relevance(title: str, description: str, domain: str) -> bool:
    """Use any OpenAI-compatible LLM to classify whether an article is relevant."""
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "gpt-4.1-mini")

    if not base_url:
        click.echo("LLM_BASE_URL not set — skipping classification", err=True)
        return True  # Default to including when classification unavailable

    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key=api_key or "not-needed", max_retries=2, timeout=30)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You classify news articles. Is this article about "
                        f"{domain}? Reply ONLY 'yes' or 'no'."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Title: {title}\nDescription: {description}",
                },
            ],
        )
        answer = response.choices[0].message.content.strip().lower()
        return answer.startswith("yes")
    except Exception as e:
        click.echo(f"Classification failed: {e}", err=True)
        return True  # Default to including on failure
```

### Strategy C — Hybrid (fast first pass, AI on ambiguous items)

Keyword match first. Items that clearly match → keep. Items that clearly don't match (sport
scores, weather, lifestyle keywords) → skip. Everything else → AI classification.

```python
EXCLUDE_KEYWORDS = ["sport", "soccer", "football", "recipe", "horoscope", "weather"]


async def should_include(title: str, description: str, domain: str) -> bool:
    """Hybrid filter: keywords first, AI for ambiguous cases."""
    text = f"{title} {description}".lower()

    # Quick exclude
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False

    # Quick include
    if matches_keywords(title, description):
        return True

    # Ambiguous — ask AI
    return await classify_relevance(title, description, domain)
```

### Choosing a Strategy

| Volume per run | Domain clarity | Strategy |
|----------------|----------------|----------|
| < 50 items | Any | B (AI) — cost is negligible |
| 50–200 items | Clear keywords exist | A (keywords) or C (hybrid) |
| > 200 items | Any | C (hybrid) — minimize API calls |
| Any | Subtle/nuanced domain | B (AI) — keywords will miss too much |

### Logging Filtered Items

Always log what was filtered and why, so the user can tune the filter:

```python
click.echo(f"Filter: {kept} kept, {skipped} skipped out of {total} items")
# Optionally log skipped titles for debugging:
for item in skipped_items[:5]:
    click.echo(f"  → Skipped: {item['title'][:80]}")
```

### Schema Impact

Add a `category` field to the schema to record the classification result. Even if all stored
items pass the filter (they're all "legal"), the category field documents what the filter
selected for. If the filter has sub-categories, use those instead.
