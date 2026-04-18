"""On-disk cache for Phase 3 summaries.

One layer, parallel to ``extraction_cache.EXTRACTIONS_DIR``:

- ``.cache/judgments_summaries/{id}.json`` — generated summary plus the
  composed input that produced it and the model/endpoint it came from.
  Stored so a crash after the LLM call but before the DB write doesn't
  lose the (often slow + costly) generation work.

All writes go through ``write_summary_atomic`` (tmp file +
``os.replace``) to survive SIGINT and crashes. Corrupt JSON is
quarantined by rename so the next run regenerates rather than spinning
on the same bad file.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

CACHE_ROOT = Path(".cache")
SUMMARIES_DIR = CACHE_ROOT / "judgments_summaries"


def _ensure_dirs() -> None:
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)


def summary_path(judgment_id: str) -> Path:
    return SUMMARIES_DIR / f"{judgment_id}.json"


def _quarantine(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{ts}")
    try:
        os.replace(path, target)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass


def read_summary(judgment_id: str) -> Optional[Dict[str, Any]]:
    """Return cached summary JSON, or None if missing/corrupt."""
    path = summary_path(judgment_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _quarantine(path)
        return None


def write_summary_atomic(judgment_id: str, data: Dict[str, Any]) -> None:
    _ensure_dirs()
    path = summary_path(judgment_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
