---
name: zeeker-source-creator
description: >
  Automate the creation of Zeeker database resources -- the Python modules that fetch, extract,
  and store data from web sources into searchable SQLite databases served via Datasette. Use this
  skill whenever the user wants to create a new Zeeker resource, add a data source to a Zeeker
  project, scaffold a new Zeeker project from scratch, or describes a website/RSS feed/PDF
  collection they want to scrape and ingest. Trigger on mentions of "zeeker", "datasette",
  "resource", "scrape this site", "add a source", "ingest", or when a user provides a URL and
  wants to extract structured data from it into a database. Also trigger when the user wants to
  understand a website's structure for scraping purposes, or asks about scraping strategy,
  pagination, or rate limiting in the context of data collection.
---

# Zeeker Source Creator

Build Zeeker database resources from web sources. The user gives you a URL (an index page,
RSS feed, or PDF collection) and you figure out the site structure, propose a resource design,
and generate the code.

## Core Workflow

### Step 1 -- Receive and Inspect the Source

The user provides a URL. Fetch the page and analyze it to determine what kind of source it is
and how to scrape it. Read `references/scraping-reference.md` for the full site analysis
checklist. In summary:

1. **Fetch the page** with `httpx` or `web_fetch`
2. **Check for RSS/Atom feeds** in the `<head>` (`<link rel="alternate" type="application/rss+xml">`)
3. **Identify the listing structure** -- find the main content container with repeating items
4. **Classify the links** -- do they point to sub-pages, external articles, PDFs, or documents?
5. **Check for pagination** -- next links, page numbers, load-more buttons
6. **Follow 1-2 sample detail pages** -- inspect the content structure and estimate length
7. **Check update frequency** -- look at dates on listing items to gauge cadence

### Step 2 -- Determine the Source Type

Based on the inspection, classify the source:

| Signal | Source Type | Reference File |
|--------|------------|----------------|
| RSS/Atom feed link found in `<head>` | RSS Feed | `references/rss-reference.md` |
| Links point to `.pdf` files | PDF Extraction | `references/pdf-reference.md` |
| Links to sub-pages with long content (>500 words) | Web Scraping (catalog + fragments) | `references/scraping-reference.md` |
| Links to sub-pages with short content | Web Scraping (flat table) | `references/scraping-reference.md` |
| Page links to external articles (news aggregator) | RSS-like (use Jina Reader) | `references/rss-reference.md` |
| Known platform with public API (Mastodon, Reddit, etc.) | JSON API | `references/api-reference.md` |
| Page requires JS but platform has an API | JSON API | `references/api-reference.md` |

If the page has both an RSS feed and HTML listings, prefer the RSS feed. If the source is a
known platform with a public API (e.g., Mastodon `/@username` URLs), prefer the API over both
RSS and scraping -- it returns richer structured data.

### Step 3 -- Report Findings and Propose Design

Before generating any code, report to the user what you found. Include:

- Source type detected
- Number of items found on the listing page
- Key CSS selectors identified (listing container, item links, metadata)
- Whether pagination exists and what pattern
- Sample detail page structure (content container, element types, estimated length)
- Recommended schema (flat table vs. catalog + fragments)
- Recommended cadence tier (read `references/scraping-strategy.md` for the four tiers)
- Which environment variables will be needed
- **Whether filtering is needed** -- if the source publishes broadly but the user wants a
  specific domain, propose a filtering strategy (see `references/resource-patterns.md` Content
  Filtering section). Consider the volume per run and domain clarity to recommend keyword
  matching, AI classification, or hybrid.
- **Whether checkpointing is needed** -- if the source has more than ~500 items, propose a
  batch crawl strategy with checkpoint/resume support so the user can spread the work across
  multiple runs. For very large archives (>5,000 items), recommend the two-phase approach:
  fast discovery first, then content extraction as a separate backfill. See the "Checkpoint
  and Resume" section in `references/scraping-strategy.md`.
- **Data licensing** -- ask the user what license applies to the data. Check the source site
  for a terms of use page, footer copyright notice, or open data license. Present what you
  found and ask the user to confirm. If nothing is stated on the site, ask the user directly:
  "What license should apply to this data in the database?" Do not guess -- licensing has
  legal implications. Common options for Singapore government data include the Singapore Open
  Data Licence and CC-BY-4.0, but the user must confirm.

Wait for the user to confirm or adjust before proceeding.

### Step 4 -- Determine Project Context

Is this for an existing Zeeker project or a new one?

- **Existing project**: Check for `zeeker.toml` in the working directory. Read it to understand
  the current project name, database, and existing resources.
- **New project**: Run `uv run zeeker init <project-name>` to create the scaffold, then
  customize the generated files. Read `references/project-scaffold-reference.md` for what to
  customize (dependencies, zeeker.toml metadata, .env.example, workflow files).

### Step 5 -- Read Reference Files and Generate

Based on the source type, read:

1. **Always**: `references/resource-patterns.md` (common patterns, env vars, schema conventions)
2. **Always**: `references/scraping-strategy.md` (rate limiting, concurrency, cadence config)
3. **Source-specific**: The appropriate reference file for the source type
4. **If pagination detected**: `references/pagination-patterns.md`
5. **If new project**: `references/project-scaffold-reference.md`
6. **Always**: `references/deploy-reference.md` (GitHub Actions workflows, S3 deployment)

Then generate using the Zeeker CLI as the foundation:

**5a. Create the resource scaffold with `zeeker add`:**

Construct the command with maximum metadata from site analysis. Every flag you pass improves
the Datasette UI. Read "Metadata: Fill in Everything You Can" in
`references/resource-patterns.md` for the full mapping.

```bash
# Standard resource (flat table, sync)
uv run zeeker add <n> --description "<desc>" \
  --fts-fields title --fts-fields summary --fts-fields content_text \
  --facets <categorical_col> --sort "<date_col> desc" --size 25

# Fragment-enabled resource (catalog + fragments)
uv run zeeker add <n> --fragments --description "<desc>" \
  --fts-fields title --fragments-fts-fields content_text \
  --facets <categorical_col> --sort "<date_col> desc"

# Async resource (for RSS/API with concurrent enrichment)
uv run zeeker add <n> --async --description "<desc>" \
  --fts-fields title --fts-fields summary --fts-fields content_text \
  --facets <categorical_col> --sort "<date_col> desc"

# Async + fragments
uv run zeeker add <n> --fragments --async --description "<desc>" \
  --fts-fields title --fragments-fts-fields content_text \
  --facets <categorical_col> --sort "<date_col> desc"
```

After `zeeker add` creates the resource, immediately update `zeeker.toml` to add the
`[resource.<n>.columns]` section with descriptions for every column. Also update the
`[project]` section with source URL, license, and description if not already done.

**5b. Fill in the generated template:**

`zeeker add` generates a placeholder `fetch_data()` (and `fetch_fragments_data()` for
fragments). Replace the placeholder logic with the source-specific implementation from the
appropriate reference file. The template already has the correct function signatures, imports
for `sqlite_utils`, and the `existing_table` parameter.

**5c. Add the scraping configuration block:**

Add the rate-limiting constants, polite_sleep helper, circuit breaker, and any checkpoint
logic to the top of the generated module. These come from `references/scraping-strategy.md`.

**5d. Add source-specific dependencies:**

```bash
uv add <packages needed for this resource>
```

**5e. Update CLAUDE.md with source-specific notes:**

Zeeker auto-generates basic resource documentation. Append source-specific details: data
source URL, cadence recommendation, required environment variables, scraping strategy notes.

**5f. Generate deployment workflow:**

Create or update `.github/workflows/` with the appropriate workflow for the cadence tier.
See `references/deploy-reference.md` for templates.

**5g. Remind the user** to configure GitHub Secrets for deployment.

### Step 6 -- Validate

After generating, do a quick sanity check:

- Does the resource module import all needed packages?
- Does `fetch_data()` return `List[Dict[str, Any]]`?
- Do the `zeeker.toml` FTS fields match actual column names?
- Are all required environment variables documented in `.env.example`?
- Is the rate-limiting configuration present and reasonable?
- Does the workflow file reference the correct database name and resource name?
- Are all required GitHub Secrets listed in the CLAUDE.md entry?

## Environment Variables by Source Type

Read `references/resource-patterns.md` for the full matrix, but in brief:

| Source Type | DOCLING_SERVE_URL | JINA_API_TOKEN | LLM_BASE_URL + LLM_MODEL |
|-------------|-------------------|----------------|--------------------------|
| RSS Feed | No | Yes | Yes (summary) |
| JSON API (with linked articles) | No | Yes | Yes (filter + summary) |
| Web Scraping (simple) | No | No | Optional |
| Web Scraping (complex/Docling) | Yes | No | Optional |
| PDF | Yes | No | Optional |

## Schema Conventions

The schema follows from what the source provides. Read `references/resource-patterns.md` for
the full conventions, but the key principle is: capture what the source gives you, add content
extraction and an AI summary for search, and minimal housekeeping. No speculative fields.
IDs are always opaque hashes, never content values like citations or paragraph numbers.

## Tooling Conventions

All generated code follows these conventions:

- **`uv`** for Python environment management -- all commands prefixed with `uv run`
- **`black`** formatting -- line-length 100, Python 3.12 target
- **`httpx`** for all HTTP calls (sync and async)
- **`tenacity`** for retry logic with exponential backoff
- **`click.echo`** for progress logging (consistent with Zeeker's own CLI output)
- **`beautifulsoup4`** with `lxml` parser for HTML parsing
- **`feedparser`** for RSS/Atom feeds
- **Docling Serve** via REST API for PDF and complex document extraction

## Zeeker CLI Quick Reference

```bash
# Project lifecycle
uv run zeeker init <project-name>          # Scaffold new project
uv run zeeker add <resource> [flags]       # Add resource (creates template + config)
uv run zeeker build                        # Build database from all resources
uv run zeeker build <resource>             # Build specific resource only
uv run zeeker build --sync-from-s3         # Incremental build (download existing DB first)
uv run zeeker build --setup-fts            # Build with full-text search indexes
uv run zeeker deploy                       # Deploy database to S3
uv run zeeker backup                       # Create dated archive in S3

# zeeker add flags
--description "..."                        # Resource description
--fragments                                # Enable catalog + fragments tables
--async                                    # Generate async fetch_data template
--fts-fields title --fts-fields content    # Full-text search fields
--fragments-fts-fields content_text        # FTS fields for fragments table
--facets category --facets court           # Datasette facet columns
--sort "date desc"                         # Default sort order
--size 25                                  # Default page size
```
