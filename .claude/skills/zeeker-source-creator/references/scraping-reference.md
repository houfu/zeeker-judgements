# Web Scraping Resource Reference

Use this reference when the source is a website with HTML pages to scrape. This covers both
simple scraping (BeautifulSoup) and complex document extraction (Docling Serve for HTML).

## Site Analysis Checklist

When the user gives you an index page URL, systematically inspect it before proposing a design.

### 1. Page-Level Inspection

Fetch the page and parse it:

```python
import httpx
from bs4 import BeautifulSoup

response = httpx.get(url, timeout=30.0)
soup = BeautifulSoup(response.content, "html.parser")
```

Check the following:

**RSS/Atom feed links** — If found, consider using the RSS reference instead.
```python
feed_links = soup.find_all("link", attrs={"type": re.compile(r"rss|atom")})
```

**Main content container** — Find the largest block with repeating child elements. Look for
common patterns: `<ul>` or `<ol>` with `<li>` items, `<div>` with repeating card elements,
`<table>` with rows. Identify the CSS selector.

**Item count** — How many items are listed? A small fixed set (< 50) suggests a catalog that
can be scraped in one pass. Hundreds or thousands suggest pagination.

**Metadata per item** — Does each listing item carry metadata alongside the link? Dates,
authors, categories, tags, descriptions. These become columns.

### 2. Link Classification

Extract all links from the listing area and classify them:

```python
links = content_container.find_all("a", href=True)
for link in links:
    href = link["href"]
    title = link.get_text(strip=True)
    # Classify:
    # - Same-domain sub-page → detail scraping
    # - External link → Jina Reader or skip
    # - .pdf file → Docling Serve
    # - Anchor link (#) → skip
```

**URL pattern** — Do the links share a common path prefix? This helps validate that you're
capturing the right links. For example, all law chapter links contain `/About-Singapore-Law/`.

**Absolute vs relative** — Resolve relative URLs against the base URL.

### 3. Sample Detail Page Inspection

Follow 1–2 discovered links and inspect the detail page:

**Content container** — Identify the main content area. Look for `<article>`, `<main>`,
or a semantic class like `.article-content`, `.entry-content`, `.edn_article`.

**Element types** — What's inside the content container? Headings (`h1`–`h6`), paragraphs
(`p`), tables (`table`), lists (`ul`/`ol`), code blocks (`pre`/`code`), images (`img`).

**Content length** — Estimate the text length. Short (< 500 words) → flat table.
Long (> 500 words) → consider fragments.

**Structured metadata** — Is there metadata on the detail page that isn't on the listing?
Author, date, breadcrumbs, tags, related links.

### 4. Pagination Detection

Look for pagination controls on the listing page:

```python
# Page number links
page_links = soup.select("a[href*='page='], .pagination a, .pager a")

# Next link
next_link = soup.find("a", text=re.compile(r"next|→|>>", re.I))
next_link = next_link or soup.find("a", rel="next")

# Load more button (suggests JS-rendered content)
load_more = soup.find("button", text=re.compile(r"load more|show more", re.I))
```

See `pagination-patterns.md` for how to handle each pattern.

### 5. Update Frequency

Check dates on the listing items to gauge how often the source is updated:

```python
# Look for date patterns in listing text
date_elements = soup.find_all(text=re.compile(r"\d{1,2}\s+\w+\s+20\d{2}"))
```

If the most recent dates are today → daily cadence (Tier 1). Last week → weekly (Tier 2).
Last month or older → monthly (Tier 3) or one-shot (Tier 4).

## Architecture: Two Patterns

### Pattern A — Flat Table (short content)

Each listing item becomes one row with the content included inline.

```
Index page (single fetch)
    ↓
Discover item links + metadata
    ↓
For each item:
    ├─ Fetch detail page → extract content_text
    ├─ AI Summary (optional) → summary
    └─ Listing metadata → title, category, date, etc.
    ↓
Flat table (one row per item)
FTS on: title, summary, content_text
```

### Pattern B — Catalog + Fragments (long content)

A thin catalog table for navigation and a fragments table for search.

```
Index page(s) (may be multiple section pages)
    ↓
Discover item links + metadata → catalog table
    ↓
For each catalog item:
    ├─ Fetch detail page
    ├─ Extract structured content (headings, paragraphs, tables, lists)
    └─ Chunk into fragments → fragments table
    ↓
Catalog table (one row per document)
Fragments table (many rows per document)
FTS on: catalog.title + fragments.content_text
```

Choose Pattern B when detail pages have substantial content (> 500 words), especially with
clear internal structure (numbered sections, headings, tables).

## Simple Scraping with BeautifulSoup

For well-structured HTML where you know the selectors. Use this for most sites.

### Discovery Phase

Fetch the index page(s) and extract item links:

```python
def discover_items(index_url: str, section_name: str) -> List[Dict[str, Any]]:
    """Discover items from an index page."""
    response = httpx.get(index_url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    container = soup.select_one("<LISTING_SELECTOR>")
    if not container:
        click.echo(f"No listing container found at {index_url}", err=True)
        return []

    items = []
    for link in container.select("<ITEM_SELECTOR>"):
        href = link.get("href", "")
        title = link.get_text(strip=True)

        if not href or len(title) < 5:
            continue

        # Resolve relative URLs
        if href.startswith("/"):
            href = f"{BASE_URL}{href}"

        items.append({
            "id": make_id(href),
            "source_url": href,
            "title": title,
            "section": section_name,
            "created_at": datetime.now().isoformat(),
        })

    click.echo(f"Found {len(items)} items in {section_name}")
    return items
```

Replace `<LISTING_SELECTOR>` and `<ITEM_SELECTOR>` with the selectors discovered during site
analysis. These are the main customization points.

### Content Extraction Phase

Fetch each detail page and extract content:

```python
def extract_page_content(url: str) -> List[Dict[str, Any]]:
    """Extract structured content from a detail page."""
    response = httpx.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")

    container = soup.select_one("<CONTENT_SELECTOR>")
    if not container:
        return []

    elements = container.find_all(
        ["p", "table", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6"]
    )

    content_parts = []
    for el in elements:
        # Skip nested elements (inside tables or lists)
        if el.find_parent(["table", "ul", "ol"]):
            continue

        text = el.get_text(strip=True)
        if not text:
            continue

        if el.name == "table":
            content_parts.append({"text": extract_table_text(el), "type": "table"})
        elif el.name in ["ul", "ol"]:
            content_parts.append({"text": extract_list_text(el), "type": "list"})
        elif el.name.startswith("h"):
            content_parts.append({"text": text, "type": "heading"})
        else:
            content_parts.append({"text": text, "type": "paragraph"})

    return content_parts
```

### Table and List Extraction Helpers

```python
def extract_table_text(table_element) -> str:
    """Extract table as pipe-separated text."""
    rows = []
    for tr in table_element.find_all("tr"):
        cells = [cell.get_text(strip=True) for cell in tr.find_all(["td", "th"])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def extract_list_text(list_element) -> str:
    """Extract list items as bullet-pointed text."""
    items = []
    for li in list_element.find_all("li", recursive=False):
        text = li.get_text(strip=True)
        if text:
            items.append(f"- {text}")
    return "\n".join(items)
```

## Complex Extraction with Docling Serve

For pages with complex layouts, Docling Serve can process the HTML and return structured
components. This replaces manual BeautifulSoup parsing for complex documents.

Requires `DOCLING_SERVE_URL` environment variable pointing to a running Docling Serve instance.

### Converting HTML via Docling Serve

```python
def extract_with_docling(url: str) -> dict:
    """Extract structured content via Docling Serve."""
    docling_url = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")
    api_key = os.environ.get("DOCLING_SERVE_API_KEY")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key

    response = httpx.post(
        f"{docling_url}/v1/convert/source",
        json={
            "sources": [{"kind": "http", "url": url}],
            "options": {
                "from_formats": ["html"],
                "to_formats": ["json", "md"],
            },
        },
        headers=headers,
        timeout=120.0,
    )
    response.raise_for_status()
    return response.json()
```

The JSON response contains a structured `DoclingDocument` with typed components. The markdown
response gives clean text for FTS. Use Docling when the page has complex tables, multi-column
layouts, or deeply nested structure that's hard to parse with simple selectors.

## Fragment Creation

For Pattern B resources, split the extracted content into searchable fragments.

### Simple Chunking (by heading)

Split on headings — each heading starts a new fragment that includes all content until the
next heading:

```python
def create_fragments(
    content_parts: List[Dict[str, Any]], item_id: str
) -> List[Dict[str, Any]]:
    """Split content into fragments, one per section."""
    fragments = []
    current_text_parts = []
    current_type = "paragraph"
    order = 0

    for part in content_parts:
        if part["type"] == "heading" and current_text_parts:
            # Flush current fragment
            fragments.append({
                "id": make_id(item_id, str(order)),
                "item_id": item_id,
                "fragment_order": order,
                "content_type": current_type,
                "content_text": "\n\n".join(current_text_parts),
                "char_count": sum(len(t) for t in current_text_parts),
            })
            current_text_parts = []
            order += 1

        current_text_parts.append(part["text"])
        current_type = part["type"]

    # Final fragment
    if current_text_parts:
        fragments.append({
            "id": make_id(item_id, str(order)),
            "item_id": item_id,
            "fragment_order": order,
            "content_type": current_type,
            "content_text": "\n\n".join(current_text_parts),
            "char_count": sum(len(t) for t in current_text_parts),
        })

    return fragments
```

Adapt the chunking strategy to the source's content structure. If the source uses numbered
paragraphs (like legal texts with `1.2.3` numbering), split on those instead. If the source
is a long narrative, consider size-based chunking (~500–1000 words per fragment).

## Session Management

Use a single `httpx.Client` for all requests to a site — this reuses connections and cookies:

```python
def fetch_all_items(index_urls: List[str]) -> List[Dict[str, Any]]:
    """Fetch items from multiple index pages with connection reuse."""
    all_items = []
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for url in index_urls:
            response = client.get(url)
            items = parse_listing(response)
            all_items.extend(items)
            time.sleep(REQUEST_DELAY_BASE)
    return all_items
```

## Multiple Index Pages

Some sites have multiple section pages (like the reference project with Overview, Commercial
Law, and Singapore Legal System). Discover these during site analysis — look for navigation
menus, sidebar links, or breadcrumb trails that reveal the site's section structure.

Generate a `get_index_urls()` function that returns all section URLs:

```python
def get_index_urls() -> List[tuple[str, str]]:
    """Return list of (index_url, section_name) tuples to scrape."""
    return [
        ("https://example.com/section-a", "Section A"),
        ("https://example.com/section-b", "Section B"),
    ]
```
