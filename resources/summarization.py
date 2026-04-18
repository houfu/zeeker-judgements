"""Pure-function prompt composition + single LLM call for Phase 3.

IO is narrow: one ``OpenAI.chat.completions.create`` call in ``summarise``
and reads of environment variables in ``make_client``. Everything else
(scoring, packing, document-order re-emission) is pure so it can be
unit-tested without the OpenAI SDK.

See CLAUDE.md "Phase 3" for why we don't use the zeeker-source-creator
skill's ``text[:4000]`` truncation: the holding of a judgment usually
lives at the end, and a flat truncation throws it away. This module
replaces that strategy with a fragment-weighted sampler that keeps the
summary-relevant parts regardless of where they sit in the document.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

# SUMMARY_SYSTEM_PROMPT is iterated manually after the first few real
# summaries. Keep it terse and grounded in the fields a legal-research
# user would want back out.
SUMMARY_SYSTEM_PROMPT = """You are a legal research expert summarising Singapore court judgments.

Produce a single paragraph of at most 100 words that emphasises:
- the court and the level of the decision (e.g. Court of Appeal, High Court)
- the parties and the nature of their dispute
- the key legal issues the court had to decide
- the court's holding and its reasoning in brief
- any precedents cited or distinguished

Write in a plain, information-dense style suitable for a legal-research
search index. Use terms a legal researcher would search for. Do not
include disclaimers, speculation, editorial commentary, or text beyond
the summary paragraph.
"""

# Patterns applied to `section_heading` (case-insensitive). Ordered by
# descending weight — first match wins in ``score_fragment``.
_DISPOSITIVE_RE = re.compile(r"conclusion|decision|holding|disposition|order", re.IGNORECASE)
_ANALYSIS_RE = re.compile(r"issue|analysis|reasoning", re.IGNORECASE)

_HEADING_PREFIX = "Judg-Heading-"
_NUMBERED_CLASSES = {"Judg-1", "Judg-1-firstpara"}


def score_fragment(frag: Dict[str, Any]) -> float:
    """Rank a fragment by its likely value to a summary.

    Signals (additive):
      +2.0  has_footnotes         — heavily-cited paragraphs tend to
                                    carry the court's legal reasoning.
      +3.0  dispositive heading   — conclusion / decision / holding /
                                    disposition / order sections.
      +1.5  analysis heading      — issue / analysis / reasoning.
      +0.5  has_table             — often damages schedules / quantum.
      up to +0.5  length bonus    — mild bias toward substantive text
                                    (capped so a single monster para
                                    can't dominate).
    """
    score = 0.0
    if frag.get("has_footnotes"):
        score += 2.0
    heading = (frag.get("section_heading") or "").strip()
    if heading:
        if _DISPOSITIVE_RE.search(heading):
            score += 3.0
        elif _ANALYSIS_RE.search(heading):
            score += 1.5
    if frag.get("has_table"):
        score += 0.5
    text_len = len(frag.get("content_text") or "")
    score += 0.1 * min(text_len, 500) / 100
    return score


def _is_heading(frag: Dict[str, Any]) -> bool:
    return (frag.get("class_name") or "").startswith(_HEADING_PREFIX)


def _is_numbered(frag: Dict[str, Any]) -> bool:
    return frag.get("class_name") in _NUMBERED_CLASSES and frag.get("paragraph_number") is not None


def _render_fragment(frag: Dict[str, Any]) -> str:
    text = (frag.get("content_text") or "").strip()
    if not text:
        return ""
    if _is_heading(frag):
        return f"## {text}"
    pn = frag.get("paragraph_number")
    if pn is not None:
        return f"[{pn}] {text}"
    return text


def compose_summary_input(
    row: Dict[str, Any],
    fragments: List[Dict[str, Any]],
    max_chars: int,
) -> str:
    """Build a fragment-weighted LLM input within ``max_chars``.

    Always-keep set (unconditional): row['court_summary'] if non-empty,
    every Judg-Heading-*, the first numbered paragraph (smallest
    paragraph_number), and the last three numbered paragraphs (largest
    paragraph_number). The remaining budget is packed with the highest-
    scored non-heading/non-numbered fragments. Kept fragments are then
    re-emitted in document order so the LLM sees a coherent narrative.

    If the row has zero fragments (shouldn't happen for has_content=1,
    but we guard) the fallback is ``row['content_text'][:max_chars]``.
    """
    # Stable ordering by ordinal — the caller passes whatever the DB
    # returned and we don't want to depend on sqlite_utils row order.
    fragments = sorted(fragments, key=lambda f: f.get("ordinal") or 0)

    if not fragments:
        fallback = (row.get("content_text") or "").strip()
        return fallback[:max_chars]

    # Always-keep membership by ordinal (the natural fragment PK).
    keep_ordinals: set = set()
    headings = [f for f in fragments if _is_heading(f)]
    for f in headings:
        keep_ordinals.add(f["ordinal"])

    numbered = [f for f in fragments if _is_numbered(f)]
    if numbered:
        numbered_sorted = sorted(numbered, key=lambda f: f["paragraph_number"])
        keep_ordinals.add(numbered_sorted[0]["ordinal"])
        for f in numbered_sorted[-3:]:
            keep_ordinals.add(f["ordinal"])

    def _length_of(fragment: Dict[str, Any]) -> int:
        return len(_render_fragment(fragment)) + 2  # +2 for "\n\n"

    def _total_kept_chars() -> int:
        total = 0
        for f in fragments:
            if f["ordinal"] in keep_ordinals:
                total += _length_of(f)
        if court_summary:
            total += len(court_summary) + 2
        return total

    court_summary = (row.get("court_summary") or "").strip()

    # Pack remainder by score, largest first, as long as there's budget.
    remainder = [
        f
        for f in fragments
        if f["ordinal"] not in keep_ordinals and not _is_heading(f) and not _is_numbered(f)
    ]
    remainder.sort(key=score_fragment, reverse=True)

    for f in remainder:
        if _total_kept_chars() + _length_of(f) > max_chars:
            continue
        keep_ordinals.add(f["ordinal"])

    # If even the always-keep set blows the budget, trim numbered paras
    # from the tail (keep first + headings), then the first para, then
    # the court summary. The goal is to deliver something rather than
    # refuse — the LLM will truncate the paragraph it writes anyway.
    if _total_kept_chars() > max_chars and numbered:
        keep_last = sorted(numbered, key=lambda f: f["paragraph_number"])[-3:]
        for f in keep_last:
            if _total_kept_chars() <= max_chars:
                break
            keep_ordinals.discard(f["ordinal"])

    # Re-emit in document order.
    parts: List[str] = []
    if court_summary:
        parts.append(f"## Court Summary\n{court_summary}")
    for f in fragments:
        if f["ordinal"] not in keep_ordinals:
            continue
        rendered = _render_fragment(f)
        if rendered:
            parts.append(rendered)

    composed = "\n\n".join(parts)
    # Hard cap — covers the edge case where the always-keep set alone is
    # still over budget after trimming.
    return composed[:max_chars]


def make_client():
    """Build an OpenAI-compatible client, or return None when unconfigured.

    Returning None lets the caller log "Phase 3: LLM not configured"
    and skip the run without importing openai at module-load time if the
    env var isn't set. ``LLM_API_KEY`` defaults to ``"not-needed"`` so
    local servers (Ollama, vLLM) work without setting it.
    """
    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    if not base_url:
        return None
    # Import lazily so unconfigured environments don't need openai
    # installed at import time (keeps the test matrix lighter too).
    from openai import OpenAI

    api_key = os.environ.get("LLM_API_KEY", "").strip() or "not-needed"
    return OpenAI(base_url=base_url, api_key=api_key)


def summarise(
    input_text: str,
    model: str,
    client,
    *,
    timeout: float = 120.0,
    max_tokens: int = 512,
    temperature: float = 0.2,
) -> str:
    """Single LLM call → trimmed summary paragraph.

    ``max_tokens=512`` gives headroom above a 100-word paragraph
    (~150 tokens) and accommodates thinking models (e.g. Gemma4:26b)
    where reasoning tokens can consume the original 220-token budget
    entirely, leaving empty content. ``think=False`` via ``extra_body``
    disables chain-of-thought for Ollama thinking models — summaries
    don't need extended reasoning. ``temperature=0.2`` keeps output
    information-dense rather than creative. Raises whatever the OpenAI
    SDK raises — the caller (``_summarise_row``) handles retry /
    quarantine.
    """
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": input_text},
        ],
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        extra_body={"think": False},
    )
    choice = response.choices[0]
    content = getattr(choice.message, "content", "") or ""
    if not content:
        finish_reason = getattr(choice, "finish_reason", "unknown")
        raise ValueError(f"LLM returned empty content (finish_reason={finish_reason})")
    return content.strip()


def resolve_model(default: str = "llama3.1:8b") -> str:
    return os.environ.get("LLM_MODEL", "").strip() or default
