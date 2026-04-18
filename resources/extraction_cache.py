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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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


def _quarantine(path: Path, reason: str) -> None:
    """Rename a corrupt cache file out of the way so the next run re-parses.

    Quarantined files keep their original extension so we can grep for them
    but are suffixed with ``.corrupt-<ts>``. Callers should treat the cache
    miss as 'no data' and re-fetch / re-parse from scratch.
    """
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{ts}")
    try:
        os.replace(path, target)
    except OSError:
        # If we can't rename, best effort: unlink so the next run doesn't
        # keep hitting the same corrupt file.
        try:
            path.unlink()
        except OSError:
            pass


def read_html(judgment_id: str) -> Optional[str]:
    """Return cached HTML, or None if missing/corrupt.

    Corrupt gzip (truncated, not-a-gzip) is quarantined so the next run
    re-fetches from the server rather than re-hitting the same bad file.
    """
    path = html_path(judgment_id)
    if not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, EOFError, UnicodeDecodeError) as exc:
        _quarantine(path, f"gzip read: {exc}")
        return None


def write_html_atomic(judgment_id: str, html: str) -> None:
    _ensure_dirs()
    path = html_path(judgment_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.write(html)
    os.replace(tmp, path)


def read_extraction(judgment_id: str) -> Optional[Dict[str, Any]]:
    """Return cached extraction JSON, or None if missing/corrupt.

    Truncated or otherwise-invalid JSON is quarantined (not raised) so a
    transient filesystem issue doesn't burn the row's retry budget on
    every run forever.
    """
    path = extraction_path(judgment_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        _quarantine(path, f"json parse: {exc}")
        return None


def write_extraction_atomic(judgment_id: str, data: Dict[str, Any]) -> None:
    _ensure_dirs()
    path = extraction_path(judgment_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
