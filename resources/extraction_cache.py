"""On-disk cache for Phase 2 extraction.

Two layers, both under ``.cache/`` (gitignored):

- ``.cache/judgments_html/{id}.html.gz`` — raw detail-page HTML, gzipped.
  Acts as the source of truth for re-extraction. Keeps the published DB
  lean while letting us re-parse without re-hitting eLitigation.
- ``.cache/judgments_extractions/{id}.json`` — parsed output (paragraph
  fragments, court summary, content text, metadata). Written after a
  successful parse so ``fetch_fragments_data`` can emit rows without
  re-parsing and zeeker's module reload between main-table and fragment
  phases can't lose state.

All writes go through ``write_*_atomic`` (tmp file + ``os.replace``) so a
SIGINT or crash can't leave a half-written file behind.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

CACHE_ROOT = Path(".cache")
HTML_DIR = CACHE_ROOT / "judgments_html"
EXTRACTIONS_DIR = CACHE_ROOT / "judgments_extractions"


def _ensure_dirs() -> None:
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)


def html_path(judgment_id: str) -> Path:
    return HTML_DIR / f"{judgment_id}.html.gz"


def extraction_path(judgment_id: str) -> Path:
    return EXTRACTIONS_DIR / f"{judgment_id}.json"


def has_html(judgment_id: str) -> bool:
    return html_path(judgment_id).exists()


def has_extraction(judgment_id: str) -> bool:
    return extraction_path(judgment_id).exists()


def read_html(judgment_id: str) -> Optional[str]:
    path = html_path(judgment_id)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return fh.read()


def write_html_atomic(judgment_id: str, html: str) -> None:
    _ensure_dirs()
    path = html_path(judgment_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, path)


def read_extraction(judgment_id: str) -> Optional[Dict[str, Any]]:
    path = extraction_path(judgment_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_extraction_atomic(judgment_id: str, data: Dict[str, Any]) -> None:
    _ensure_dirs()
    path = extraction_path(judgment_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def list_extracted_ids() -> Iterable[str]:
    if not EXTRACTIONS_DIR.exists():
        return []
    return sorted(p.stem for p in EXTRACTIONS_DIR.glob("*.json"))
