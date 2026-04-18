# PDF Extraction Resource Reference

Use this reference when the source provides PDF documents. Docling Serve handles the heavy
lifting — layout analysis, table structure recognition, reading order detection, OCR — via a
REST API. The Zeeker resource just makes HTTP calls and stores the results.

## When to Use

- The index page links to `.pdf` files
- The user provides a directory or URL pattern pointing to PDFs
- The source is a collection of documents that happen to be PDFs (annual reports, legal
  judgments, government gazettes, research papers)

## Architecture

```
Index page or file listing
    ↓
Discover PDF URLs/paths
    ↓
For each PDF:
    ├─ POST to Docling Serve → structured DoclingDocument (JSON)
    ├─ Extract document metadata → catalog table
    ├─ Extract typed components → fragments table
    └─ AI Summary of full text (optional) → catalog.summary
    ↓
Catalog table (one row per document)
Fragments table (many rows per document, typed components)
FTS on: catalog.title + fragments.content_text
content_type as Datasette facet
```

## Environment Variables

```bash
# Required — URL of the running Docling Serve instance
# Local: docker run -p 5001:5001 quay.io/docling-project/docling-serve
DOCLING_SERVE_URL=http://localhost:5001

# Optional — only if the Docling Serve instance has auth enabled
DOCLING_SERVE_API_KEY=

# Optional — for AI summary generation on the catalog table
# Any OpenAI-compatible server: Ollama, vLLM, llama.cpp, or cloud providers
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=
LLM_MODEL=llama3.1
```

## Dependencies

```
uv add httpx tenacity
```

Note: the Zeeker project itself does NOT need `docling` as a dependency. Docling runs as a
separate service. The resource only needs `httpx` to call its REST API.

## Docling Serve API

### Convert from URL

For PDFs hosted on the web:

```python
def convert_pdf_url(pdf_url: str) -> dict:
    """Send a PDF URL to Docling Serve for conversion."""
    docling_url = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")
    api_key = os.environ.get("DOCLING_SERVE_API_KEY")

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key

    response = httpx.post(
        f"{docling_url}/v1/convert/source",
        json={
            "sources": [{"kind": "http", "url": pdf_url}],
            "options": {
                "to_formats": ["json", "md"],
                "do_ocr": True,
            },
        },
        headers=headers,
        timeout=300.0,  # PDFs can take a while
    )
    response.raise_for_status()
    return response.json()
```

### Convert from File

For local PDFs, upload them as multipart form data:

```python
def convert_pdf_file(file_path: str) -> dict:
    """Upload a local PDF to Docling Serve for conversion."""
    docling_url = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")
    api_key = os.environ.get("DOCLING_SERVE_API_KEY")

    headers = {}
    if api_key:
        headers["X-Api-Key"] = api_key

    with open(file_path, "rb") as f:
        response = httpx.post(
            f"{docling_url}/v1/convert/file",
            files={"files": (os.path.basename(file_path), f, "application/pdf")},
            data={
                "to_formats": "json,md",
                "do_ocr": "true",
            },
            headers=headers,
            timeout=300.0,
        )
    response.raise_for_status()
    return response.json()
```

### Async Conversion (for large documents)

For very large PDFs, use the async endpoint and poll for results:

```python
def convert_pdf_async(pdf_url: str) -> str:
    """Start async conversion and return task ID."""
    docling_url = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")

    response = httpx.post(
        f"{docling_url}/v1/convert/source/async",
        json={"sources": [{"kind": "http", "url": pdf_url}]},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["task_id"]


def poll_task(task_id: str, max_wait: int = 600) -> dict:
    """Poll for async task completion."""
    docling_url = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")
    start = time.time()

    while time.time() - start < max_wait:
        response = httpx.get(f"{docling_url}/v1/status/{task_id}")
        data = response.json()
        if data["status"] == "completed":
            return data["result"]
        elif data["status"] == "failed":
            raise RuntimeError(f"Docling conversion failed: {data.get('error')}")
        time.sleep(5)

    raise TimeoutError(f"Docling task {task_id} timed out after {max_wait}s")
```

## Processing the Docling Response

The JSON response contains a structured `DoclingDocument`. Extract catalog metadata and
fragments from it.

### Catalog Record

```python
def make_catalog_record(
    pdf_url: str, docling_result: dict, markdown_text: str
) -> Dict[str, Any]:
    """Extract catalog-level metadata from the Docling result."""
    doc = docling_result.get("document", {})

    return {
        "id": make_id(pdf_url),
        "source_url": pdf_url,
        "title": doc.get("title", os.path.basename(pdf_url)),
        "content_text": markdown_text,  # Full text as markdown for FTS
        "page_count": doc.get("num_pages", 0),
        "created_at": datetime.now().isoformat(),
    }
```

If the source provides additional metadata (publication date, author, category), extract it
from the listing page during discovery and merge it into the catalog record.

### Fragment Records

Walk the Docling document's components and create one fragment per component:

```python
def make_fragment_records(
    item_id: str, docling_result: dict
) -> List[Dict[str, Any]]:
    """Create typed fragment records from Docling document components."""
    doc = docling_result.get("document", {})
    items = doc.get("main_text", [])  # or iterate doc structure

    fragments = []
    for order, item in enumerate(items):
        text = item.get("text", "").strip()
        if not text or len(text) < 10:
            continue

        content_type = classify_component_type(item)

        fragments.append({
            "id": make_id(item_id, str(order)),
            "item_id": item_id,
            "fragment_order": order,
            "content_type": content_type,
            "content_text": text,
            "char_count": len(text),
        })

    return fragments


def classify_component_type(component: dict) -> str:
    """Map Docling component type to a simple category."""
    doc_type = component.get("type", "").lower()
    label = component.get("label", "").lower()

    if "heading" in doc_type or "title" in doc_type or "section" in label:
        return "heading"
    elif "table" in doc_type:
        return "table"
    elif "list" in doc_type:
        return "list"
    elif "formula" in doc_type or "equation" in doc_type:
        return "formula"
    elif "code" in doc_type:
        return "code"
    else:
        return "paragraph"
```

Note: the exact structure of the Docling JSON response may vary by version. Inspect the
response from a sample conversion to confirm the field paths. The markdown export is always
reliable as a fallback for the full text.

## Chunking via Docling Serve

Docling Serve also has a chunking endpoint that does structure-aware splitting. This can be
used instead of (or in addition to) the component-based fragmentation above:

```python
def chunk_document(docling_result: dict) -> List[Dict[str, Any]]:
    """Use Docling's built-in hybrid chunker."""
    docling_url = os.environ.get("DOCLING_SERVE_URL", "http://localhost:5001")

    response = httpx.post(
        f"{docling_url}/v1/chunk",
        json={
            "document": docling_result.get("document"),
            "chunker": "hybrid",
            "options": {
                "max_tokens": 500,
            },
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json().get("chunks", [])
```

The hybrid chunker is structure-aware (respects headings and tables) and token-aware (chunks
fit within a configurable token limit). Good for RAG-ready fragments.

## Handling Mixed Content: Tables, Charts, and Figures

PDF documents frequently embed tables, charts, diagrams, and other non-text elements within
or between paragraphs. Docling produces these as separate components in reading order — it
does not nest a table inside a paragraph component. This means the fragment creation logic
needs to handle attachment: which paragraph does this table or figure belong to?

### The Attachment Rule

**Non-text components attach backward to the most recent numbered paragraph.** Tables, charts,
diagrams, and images are treated as extensions of the paragraph that introduces them. If no
numbered paragraph precedes the non-text element (e.g., a table at the very start of a
section), attach it forward to the next numbered paragraph.

This works because documents are written with text that introduces a table or figure, then
the table or figure follows.

### Tables: Convert to Searchable Text

Tables should be converted to a text representation and appended to the parent paragraph's
`content_text`. This makes the table content searchable via FTS.

```python
def table_to_text(table_component: dict) -> str:
    """Convert a Docling table component to searchable text."""
    # Docling provides structured table data
    cells = table_component.get("data", {}).get("table_cells", [])
    if not cells:
        # Fallback: use the text/markdown export
        return table_component.get("text", "")

    # Build a pipe-separated text representation
    lines = []
    for row in cells:
        row_cells = [cell.get("text", "") for cell in row]
        lines.append(" | ".join(row_cells))
    return "\n".join(lines)
```

The resulting fragment looks like:

```python
{
    "paragraph_ref": "11.3",
    "content_text": (
        "The financial penalty framework is as follows...\n\n"
        "Category | Threshold | Maximum Penalty\n"
        "Cartel conduct | Any market share | 10% of turnover\n"
        "Abuse of dominance | >50% market share | 10% of turnover"
    ),
    "content_type": "paragraph",
    "has_table": True,
    "has_figure": False,
    "figure_description": None,
}
```

### Charts and Graphs: Caption as Text

Chart images are not searchable by themselves. Two sources of text make them findable:

1. **Docling's AI-generated caption** — a text description of the visual content. Imperfect
   but gives FTS something to index.
2. **The surrounding paragraph text** — the paragraph that introduces the chart already
   describes what it shows.

Store the figure description both inline (appended to `content_text` for FTS) and as a
separate field (for display or filtering):

```python
{
    "paragraph_ref": "3.6",
    "content_text": (
        "CCCS will set its strategic priorities and consider each case...\n\n"
        "[Figure: Bar chart showing enforcement actions by year, 2015-2022. "
        "Peak enforcement activity in 2019 with 15 cases.]"
    ),
    "content_type": "paragraph",
    "has_table": False,
    "has_figure": True,
    "figure_description": "Bar chart showing enforcement actions by year, 2015-2022.",
}
```

Diagrams and flowcharts get the same treatment — AI-captioned as text, attached to the
preceding paragraph.

### Implementing the Attachment Logic

When walking Docling components to create fragments, accumulate non-text components and
attach them to the appropriate paragraph:

```python
def create_fragments_with_attachments(
    components: List[dict], item_id: str
) -> List[Dict[str, Any]]:
    """Create fragments, attaching tables and figures to their parent paragraphs."""
    fragments = []
    current_heading = ""
    fragment_order = 0

    # Regex for numbered paragraphs (sequential or decimal)
    import re
    numbered_pattern = re.compile(r"^(\d+(?:\.\d+)*)\s")

    for component in components:
        comp_type = classify_component_type(component)
        text = component.get("text", "").strip()

        if not text and comp_type not in ("table", "picture"):
            continue

        if comp_type == "heading":
            current_heading = text
            continue

        if comp_type in ("table", "list", "picture"):
            # Attach to the previous fragment (backward attachment)
            if fragments:
                last = fragments[-1]
                attachment_text = ""

                if comp_type == "table":
                    attachment_text = "\n\n" + table_to_text(component)
                    last["has_table"] = True
                elif comp_type == "picture":
                    desc = component.get("caption", "") or component.get("description", "")
                    if desc:
                        attachment_text = f"\n\n[Figure: {desc}]"
                    last["has_figure"] = True
                    last["figure_description"] = desc
                elif comp_type == "list":
                    attachment_text = "\n\n" + text

                last["content_text"] += attachment_text
                last["char_count"] = len(last["content_text"])
            continue

        # This is a text paragraph — create a new fragment
        paragraph_ref = ""
        match = numbered_pattern.match(text)
        if match:
            paragraph_ref = match.group(1)

        fragments.append({
            "id": make_id(item_id, str(fragment_order)),
            "item_id": item_id,
            "fragment_order": fragment_order,
            "heading": current_heading,
            "paragraph_ref": paragraph_ref,
            "content_text": text,
            "content_type": "paragraph",
            "has_table": False,
            "has_figure": False,
            "figure_description": None,
            "char_count": len(text),
        })
        fragment_order += 1

    return fragments
```

### Fragment Schema with Mixed Content Fields

```
id                  — hash of (item_id + fragment_order)
item_id             — foreign key to catalog table
fragment_order      — reading order position
heading             — section heading this fragment falls under
paragraph_ref       — source paragraph reference ("4.5", "11.3", etc.)
content_text        — paragraph text with any attached table/figure text
content_type        — "paragraph" | "heading" | etc.
has_table           — whether a table is embedded in this fragment
has_figure          — whether a chart/diagram is embedded
figure_description  — AI-generated description of any embedded figure
char_count          — length of content_text
```

`has_table` and `has_figure` are useful Datasette facets — users can filter to fragments
that contain tables (e.g., penalty schedules, comparison matrices) or figures.

### Preserving the Original PDF

The catalog table should always store the PDF source URL. Mixed content is inherently lossy
when converted to text — a complex table loses its visual layout, a chart loses its visual
impact. The PDF URL gives users a path back to the authoritative visual version.

```python
{
    "source_url": "https://example.gov.sg/guidelines.pdf",
    "pdf_url": "https://example.gov.sg/guidelines.pdf",  # Explicit PDF link
}
```

## Resource Module Structure

```python
def fetch_data(existing_table: Optional[Table]) -> List[Dict[str, Any]]:
    """Discover PDFs and extract catalog metadata."""
    existing_ids = set()
    if existing_table:
        existing_ids = {row["id"] for row in existing_table.rows}

    catalog_records = []
    pdf_urls = discover_pdfs()  # From index page scraping

    for url in pdf_urls:
        doc_id = make_id(url)
        if doc_id in existing_ids:
            click.echo(f"Skipping (already processed): {url}")
            continue

        click.echo(f"Converting: {url}")
        try:
            result = convert_pdf_url(url)
            md_text = result.get("markdown", "")
            record = make_catalog_record(url, result, md_text)
            # Stash the full result for fragment creation
            record["_docling_result"] = result
            catalog_records.append(record)
        except Exception as e:
            click.echo(f"Error converting {url}: {e}", err=True)
            continue

        time.sleep(REQUEST_DELAY_BASE)  # Be respectful to the Docling server

    click.echo(f"Processed {len(catalog_records)} new PDFs")
    return catalog_records


def fetch_fragments_data(
    existing_fragments_table: Optional[Table],
    main_data_context: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Create fragments from Docling conversion results."""
    if not main_data_context:
        return []

    all_fragments = []
    for record in main_data_context:
        docling_result = record.pop("_docling_result", None)
        if not docling_result:
            continue

        fragments = make_fragment_records(record["id"], docling_result)
        all_fragments.extend(fragments)
        click.echo(f"Created {len(fragments)} fragments for: {record['title']}")

    return all_fragments
```

## Catalog Schema

```python
{
    "id": "sha256 hash of PDF URL",
    "source_url": "URL to the PDF file",
    "title": "Document title (from Docling or filename)",
    "content_text": "Full document text as markdown",
    "page_count": 42,
    "created_at": "2026-03-23T10:00:00",
}
```

Add any metadata available from the listing page (author, date, category, section).

## Fragment Schema

```python
{
    "id": "sha256 hash of item_id + fragment_order",
    "item_id": "foreign key to catalog table",
    "fragment_order": 0,
    "heading": "Section heading this fragment falls under",
    "paragraph_ref": "4.5 (source numbering, as a string)",
    "content_text": "Paragraph text with any attached table/figure text",
    "content_type": "paragraph",
    "has_table": False,
    "has_figure": False,
    "figure_description": "AI-generated description of embedded figure (if any)",
    "char_count": 450,
}
```

Not all fields are needed for every source. Include `heading` and `paragraph_ref` when the
document uses them. Include `has_table`, `has_figure`, and `figure_description` when the
source contains mixed content. For simple text-only PDFs, the minimal schema (id, item_id,
fragment_order, content_text, content_type, char_count) is sufficient.

## zeeker.toml Entry

```toml
[resource.<n>]
description = "<Description of the PDF collection>"
fragments = true
fts_fields = ["title", "content_text"]
fragments_fts_fields = ["content_text"]
columns = {
    id = "Document identifier",
    source_url = "URL to the PDF file",
    title = "Document title",
    content_text = "Full document text",
    page_count = "Number of pages in the PDF",
    created_at = "When this document was first processed"
}
```

## Cadence

PDF collections are typically Tier 3 (monthly) or Tier 4 (one-shot). Annual reports come out
once a year. Legal judgments may be published weekly. Check the source to determine the right
cadence. The key question: how often are new PDFs added to the collection?
