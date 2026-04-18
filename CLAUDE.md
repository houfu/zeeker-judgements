# CLAUDE.md - Zeeker-Judgements Project Development Guide

This file provides Claude Code with project-specific context and guidance for developing this project.

## Project Overview

**Project Name:** zeeker-judgements
**Database:** zeeker-judgements.db
**Purpose:** Database project for zeeker-judgements data management

## Development Environment

This project uses **uv** for dependency management with an isolated virtual environment:

- `pyproject.toml` - Project dependencies and metadata
- `.venv/` - Isolated virtual environment (auto-created)
- All commands should be run with `uv run` prefix

### Dependency Management
- **Add dependencies:** `uv add package_name` (e.g., `uv add requests pandas`)
- **Install dependencies:** `uv sync` (automatically creates .venv if needed)
- **Common packages:** requests, beautifulsoup4, pandas, lxml, pdfplumber, openpyxl

### Environment Variables
Zeeker automatically loads `.env` files when running build, deploy, and asset commands:

- **Create `.env` file:** Store sensitive credentials and configuration
- **Auto-loaded:** Environment variables are available in your resources during `zeeker build`
- **S3 deployment:** Required for `zeeker deploy` and `zeeker assets deploy`

**Example `.env` file:**
```
# S3 deployment credentials
S3_BUCKET=my-datasette-bucket
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
S3_ENDPOINT_URL=https://s3.amazonaws.com

# API keys for data resources
JINA_API_TOKEN=your_jina_token
OPENAI_API_KEY=your_openai_key
```

**Usage in resources:**
```python
import os

def fetch_data(existing_table):
    api_key = os.getenv("MY_API_KEY")  # Loaded from .env automatically
    # ... rest of your code
```

## Development Commands

### Quick Commands
- `uv run zeeker add RESOURCE_NAME` - Add new resource to this project
- `uv run zeeker add RESOURCE_NAME --fragments` - Add resource with document fragments support
- `uv run zeeker build` - Build database from all resources in this project
- `uv run zeeker deploy` - Deploy this project's database to S3

### Code Formatting
- `uv run black .` - Format code with black
- `uv run ruff check .` - Lint code with ruff
- `uv run ruff check --fix .` - Auto-fix ruff issues

### Testing This Project
- `uv run pytest` - Run tests (if added to project)
- Check generated `zeeker-judgements.db` after build
- Verify metadata.json structure

### Working with Dependencies
When implementing resources that need external libraries:
1. **First add the dependency:** `uv add library_name`
2. **Then use in your resource:** `import library_name` in `resources/resource_name.py`
3. **Build works automatically:** `uv run zeeker build` uses the isolated environment

## Resources in This Project

### `judgments` Resource
- **Description:** Singapore court judgments from eLitigation with paragraph-level search
- **File:** `resources/judgments.py`
- **Facets:** court, decision_date
- **Default Sort:** decision_date desc
- **Page Size:** 25
- **Type:** Flat table in Phase 1 (fragments table deferred to Phase 2)
- **Schema:** see `resources/judgments.py` `fetch_data()` and `zeeker.toml`

## Project-Specific Notes

### Data Source
- **URL:** https://www.elitigation.sg/gd/
- **License:** `© Government of Singapore` (per the site footer).
- **Scale:** ~10,588 judgments across ~1,059 listing pages (10/page). Covers
  all Singapore courts back to 2000 — SGCA, SGHC, SGHCA, SGHCF, SGHCR, SGDC,
  SGFC, SGMC — plus tribunals like SGCDT (Community Disputes) and SGSCT
  (Small Claims). The `court` regex is permissive: any `SG[A-Z]+` token in
  the URL path is captured verbatim.
- **Cadence:** Tier 4 one-shot batch crawl for the initial archive;
  transitions to Tier 1 daily incremental once the archive is complete.
- **Update frequency:** New judgments appear regularly (typically several
  per week day). Sort-by-date-desc + `INCREMENTAL_STOP_THRESHOLD=5` makes
  daily catch-up cheap.

### Roadmap (phased)
- **Phase 1 (DONE):** Discovery crawler — scrapes listing pages, persists
  catalog metadata to `judgments`. Content columns (`content_text`,
  `court_summary`, `summary`) are left NULL for later backfill.
- **Phase 2 (not yet implemented):** HTML content extraction from the
  detail page at `source_url` (NOT the PDF — see "Phase 2 design" below).
  Populate `content_text` and `court_summary`. Enable `fragments = true`
  in `zeeker.toml` and implement `fetch_fragments_data` to split numbered
  paragraphs into `judgments_fragments`.
- **Phase 3 (not yet implemented):** AI summaries — populate `summary`
  using an OpenAI-compatible LLM endpoint.
- **Deployment (deferred):** No S3 workflow wired up yet. The
  auto-generated `.github/workflows/deploy.yml` is inert until secrets are
  configured; ignore for Phase 1.

### Phase 2 design notes (HTML over PDF)

The initial plan was "PDF → Docling Serve → markdown". After inspecting the
detail pages we confirmed a much simpler path: the HTML served at
`source_url` is the same court-approved mobile/web conversion, with stable
semantic CSS classes across 20+ years and every court tier (verified SGCA,
SGHC, SGHCF, SGMC, SGSCT; 2005 → 2026). We keep `pdf_url` in the catalog
so users can reach the authoritative PDF, but extraction uses BeautifulSoup
on HTML — no Docling infrastructure required.

**Source containers on each detail page:**
- `div#divJudgement` — full judgment body (required, always present)
- `div#divCaseSummary` — court-authored summary (often empty; capture only
  when non-empty)
- `div#divHeadMessage` — standard disclaimer:
  *"This judgment text has undergone conversion so that it is mobile and
  web-friendly. This may have created formatting or a[...]"* — surface this
  in the UI/README so readers know the PDF is authoritative.

**`Judg-*` class map (stable across years/courts):**
| Class | Role |
|---|---|
| `Judg-1` | Top-level numbered paragraph (number is first token, e.g. `"1 The Claimant..."`) |
| `Judg-2` | Sub-paragraph |
| `Judg-Heading-1` … `Judg-Heading-5` | Nested section headings |
| `Judg-Quote-0/1/2`, `Judg-QuoteList-2` | Block quotations |
| `Judg-List-1-No`, `Judg-List-1-Item` | Numbered-list entries |
| `Judg-Author`, `Judg-Date-Reserved`, `Judg-Sign` | Front/back-matter |
| `Judg-Lawyers` | Counsel block |

**Paragraph-number parsing quirk:** the separator between the number and
the paragraph text is a non-breaking space `\xa0` on older docs and an
em-space `\u2003` on newer ones. Use `re.match(r"(\d+)[\s\xa0\u2003]+", text)`.

**Footnotes:** inline references are `<sup>` tags in the paragraph;
content lives in `div[id^="fn"]` elsewhere in the document (id may be
`fn1` or a UUID like `fn-041fe0fc-fb3c-...`). Volume varies — 0 in older
judgments, 100+ in long family-court ones. Capture footnote text into the
fragment's `footnote_text` field and set `has_footnotes = true`.

**Images and tables — attachment rule:** judgments embed tables and images
both *inside* numbered paragraphs (e.g. a screenshot referenced mid-text)
and *between* numbered paragraphs as standalone exhibits. Phase 2 must
handle both placements consistently:

- **Tables** (`<table>`) — convert cells to a pipe-separated text
  representation so the content is searchable via FTS. Append the text to
  the **parent paragraph's** `content_text` (backward attachment: attach
  to the most recent `Judg-1`/`Judg-2`). Flag with `has_table = true`.
  If a table appears before any numbered paragraph in a section, attach
  forward to the next one. Normalise to a consistent separator (e.g.
  `\n\n---table---\n`).
- **Images** (`<img>`) — can be remote URLs OR base64 `data:` URIs (we've
  seen embedded screenshots as base64 in at least one recent judgment).
  Store the `alt` text when present, otherwise derive a placeholder
  (`"[Figure: screenshot, 1024x768]"` etc.) so FTS can hit on captions /
  alt text. Flag with `has_figure = true`; persist the image URL or a
  stable identifier on the fragment (`figure_src`) without downloading
  binary content.
- **Inline vs block**: if the element is nested inside a `Judg-1` paragraph,
  just append the text/placeholder to that fragment's `content_text`. If it
  sits at the top level of `div#divJudgement` between paragraphs, apply the
  backward-attachment rule. Never create a separate fragment for a figure
  or table — they belong to their parent paragraph for search/display
  purposes.

**Extension to the fragment schema for Phase 2:**
- `has_table` (bool) — paragraph contains or absorbed a table
- `has_figure` (bool) — paragraph contains or absorbed an image
- `figure_src` (text, JSON array) — list of image URLs / data URI hashes
- `figure_descriptions` (text, JSON array) — alt-text per figure, aligned
  to `figure_src` by position

These are useful Datasette facets later (filter to paragraphs containing
tables, e.g. "show me every judgment that has a damages schedule").

**Env vars:** Phase 2 needs **none**. HTML extraction reuses the same
`httpx.Client` from Phase 1. The `DOCLING_SERVE_URL` block can be dropped
from `.env.example` when Phase 2 lands.

### Environment variables
Phase 1 needs **none** — the listing scraper is a pure public-HTML crawl.
See `.env.example` for the variables that will light up in Phase 2 / 3 /
deployment.

The crawler has a few operational knobs that default via env vars (handy
for smoke tests — no code edits required):

| Env var | Default | What it controls |
|---|---|---|
| `JUDGMENTS_MAX_PAGES_PER_RUN` | `50` | Batch cap on listing pages per invocation. Set to `2` for smoke tests. |
| `JUDGMENTS_INCREMENTAL_STOP` | `5` | Consecutive already-known IDs before early exit. |
| `JUDGMENTS_DELAY_BASE` | `1.5` | Base sleep (s) between page fetches. |
| `JUDGMENTS_DELAY_JITTER` | `0.5` | +/- jitter added to the base sleep. |

### Operational notes
- **Checkpointing:** state lives in `checkpoint_judgments_discovery.json`
  (gitignored). Resumes mid-archive across many runs; gets cleared
  automatically when the archive is exhausted OR when the daily incremental
  stop fires.
- **Batch-crawl pacing:** at defaults, ~50 pages ≈ 75s sleep + fetch/parse.
  Each run lands ~500 new records; ~22 runs cover the full archive.
- **Politeness:** single `httpx.Client` connection pool, jittered 1–2s
  delay, 3-retry tenacity backoff, 5-failure circuit breaker with 60s
  cooldown. User-Agent identifies the bot.
- **Build:** `uv run zeeker build judgments`. Re-invoke the same command to
  continue a batch crawl (checkpoint drives resume).
- **zeeker quirk:** when `fragments = true` in `zeeker.toml`, zeeker
  invokes `fetch_data()` twice (once for insert, again for fragment
  context) and reloads the module between calls, defeating any
  module-level cache. Phase 1 dodges this by keeping `fragments = false`
  until Phase 2 actually produces fragments.

### Smoke test playbook
1. `rm -f zeeker-judgements.db checkpoint_judgments_discovery.json`
2. `JUDGMENTS_MAX_PAGES_PER_RUN=2 JUDGMENTS_DELAY_BASE=1.0 uv run zeeker build judgments`
   — expect 20 records staged from pages 1–2.
3. Re-run the same command — should resume from page 3, add 20 more
   records (40 total in DB), advance checkpoint to `last_page=4`.
4. Delete the checkpoint and re-run — the crawler should hit 5 known IDs
   on page 1 and exit in under a second ("steady-state mode"), then clear
   the checkpoint automatically.

### Data source notes
- Catchwords (`subject_tags`) are hierarchical: `Subject — Topic — Sub —
  Question`. Stored as a JSON array per row so Datasette can facet / full-
  text search them. Use `json_extract(subject_tags, '$[0]')` or similar for
  queries.
- Case numbers can be multi-valued (e.g. `DC/OC 1154/2025 ( DC/AD 16/2026 )`
  has an embedded secondary reference). Stored verbatim from the listing,
  pipe-separated when multiple `a.case-num-link` elements exist.
- PDF URLs are stored percent-encoded because the underlying eLitigation
  endpoint embeds `[`, `]` and spaces from the citation into the path.

---

This file is automatically created by Zeeker and can be customized for your project's needs.
The main Zeeker development guide is in the repository root CLAUDE.md file.
