# """
# pipeline/stage2_clean.py
# ========================
# Stage 2: Three-layer noise removal for AUTOSAR PDFs.

# Layer 1 — Repeated line removal     (running headers / footers)
# Layer 2 — Page-level filters        (TOC, revision history, near-blank, captions)
# Layer 3 — Cross-document boilerplate fingerprinting

# Input:  list of page dicts from Stage 1
# Output: cleaned list of page dicts (dropped pages removed, text cleaned)
#         Each dict gets an added "content_type" field:
#             "content"    — normal extractable content
#             "diagram"    — page is mostly a diagram, kept for metadata only
#             "dropped"    — filtered out (these are excluded from output)
# """

# from __future__ import annotations

# import re
# from collections import Counter
# from pathlib import Path
# from typing import Optional

# from utils.logger import get_logger
# from config import settings

# log = get_logger("stage2")

# # ── Compiled regexes ─────────────────────────────────────────────────────────

# # TOC line: "Some heading text ........ 42" or "Some heading text\t42"
# _TOC_LINE_RE    = re.compile(r".+\.{3,}\s*\d+\s*$|.+\t\d+\s*$")

# # Date patterns: 2023-04-01, 04/2023, April 2023, 2023
# _DATE_RE        = re.compile(
#     r"\b\d{4}-\d{2}-\d{2}\b"
#     r"|\b\d{2}/\d{4}\b"
#     r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b"
#     r"|\b\d{4}\b",
#     re.IGNORECASE,
# )
# # Version patterns: v1.2, Rev. 3, Issue 4, R22-11
# _VERSION_RE     = re.compile(
#     r"\bv\d+\.\d+\b|\bRev\.?\s*\d+\b|\bIssue\s+\d+\b|\bR\d{2}-\d{2}\b",
#     re.IGNORECASE,
# )

# # Orphaned caption: short line starting with Figure/Table/Exhibit/Appendix + number
# _CAPTION_RE     = re.compile(
#     r"^(Figure|Fig\.|Table|Exhibit|Appendix|Annex)\s+[\d\.]+",
#     re.IGNORECASE,
# )

# # AUTOSAR legal boilerplate trigger phrases
# _LEGAL_TRIGGERS = [
#     "no part of this work may be reproduced",
#     "published by autosar",
#     "autosar e.v.",
#     "use is subject to autosar",
#     "this specification and the material",
# ]


# def run(pages: list[dict]) -> list[dict]:
#     """
#     Apply all three cleaning layers.
#     Returns only the pages that pass all filters, with cleaned text.
#     """
#     log.info("Stage 2: noise removal on %d pages", len(pages))

#     # ── Layer 1: repeated lines per document ─────────────────────────────────
#     pages = _remove_repeated_lines(pages)

#     # ── Layer 2: page-level filters ──────────────────────────────────────────
#     pages, dropped_counts = _apply_page_filters(pages)

#     # ── Layer 3: cross-document boilerplate fingerprinting ───────────────────
#     pages = _remove_boilerplate_pages(pages)

#     # ── Final whitespace normalization ───────────────────────────────────────
#     for p in pages:
#         p["markdown"] = _normalize_whitespace(p["markdown"])
#         p["char_count"] = len(p["markdown"])

#     log.info("Stage 2 complete: %d pages kept", len(pages))
#     for reason, n in sorted(dropped_counts.items(), key=lambda x: -x[1]):
#         log.info("  dropped %4d pages :: %s", n, reason)

#     return pages


# # ══════════════════════════════════════════════════════════════════════════════
# # LAYER 1 — Repeated line removal
# # ══════════════════════════════════════════════════════════════════════════════

# def _remove_repeated_lines(pages: list[dict]) -> list[dict]:
#     """
#     Within each source document, find lines that appear on >= threshold
#     fraction of pages and remove them everywhere.
#     """
#     # Group by source file
#     from collections import defaultdict
#     by_source: dict[str, list[dict]] = defaultdict(list)
#     for p in pages:
#         by_source[p["source"]].append(p)

#     result: list[dict] = []
#     for source, doc_pages in by_source.items():
#         n_pages = len(doc_pages)
#         if n_pages == 0:
#             continue

#         # Count how many pages each non-empty stripped line appears on
#         line_page_count: Counter = Counter()
#         for p in doc_pages:
#             seen_on_this_page = set()
#             for line in p["markdown"].splitlines():
#                 stripped = line.strip()
#                 if stripped and stripped not in seen_on_this_page:
#                     line_page_count[stripped] += 1
#                     seen_on_this_page.add(stripped)

#         # Lines appearing on >= threshold of pages are headers/footers
#         threshold_count = max(2, int(n_pages * settings.REPEATED_LINE_THRESHOLD))
#         repeated = {
#             line for line, count in line_page_count.items()
#             if count >= threshold_count
#         }

#         if repeated:
#             log.debug(
#                 "%s: removing %d repeated line(s) from %d pages",
#                 Path(source).name, len(repeated), n_pages,
#             )

#         for p in doc_pages:
#             cleaned_lines = [
#                 line for line in p["markdown"].splitlines()
#                 if line.strip() not in repeated
#             ]
#             p = dict(p)
#             p["markdown"] = "\n".join(cleaned_lines)
#             result.append(p)

#     return result


# # ══════════════════════════════════════════════════════════════════════════════
# # LAYER 2 — Page-level filters
# # ══════════════════════════════════════════════════════════════════════════════

# def _apply_page_filters(pages: list[dict]) -> tuple[list[dict], dict]:
#     kept: list[dict] = []
#     dropped_counts: dict[str, int] = {}

#     for p in pages:
#         text  = p["markdown"]
#         lines = [l for l in text.splitlines() if l.strip()]

#         # ── Near-blank ────────────────────────────────────────────────────
#         if len(text.strip()) < settings.MIN_PAGE_CHARS:
#             dropped_counts["near_blank"] = dropped_counts.get("near_blank", 0) + 1
#             continue

#         # ── Legal / disclaimer boilerplate ────────────────────────────────
#         lower = text.lower()
#         if any(trigger in lower for trigger in _LEGAL_TRIGGERS):
#             dropped_counts["legal_boilerplate"] = dropped_counts.get("legal_boilerplate", 0) + 1
#             continue

#         # ── TOC page ─────────────────────────────────────────────────────
#         toc_heading = re.search(
#             r"^#{1,4}\s*(table of contents|contents|index)\s*$",
#             text, re.IGNORECASE | re.MULTILINE,
#         )
#         if toc_heading:
#             dropped_counts["toc_heading"] = dropped_counts.get("toc_heading", 0) + 1
#             continue

#         if lines:
#             toc_line_ratio = sum(
#                 1 for l in lines if _TOC_LINE_RE.match(l.strip())
#             ) / len(lines)
#             if toc_line_ratio >= settings.TOC_LINE_RATIO_THRESHOLD:
#                 dropped_counts["toc_content"] = dropped_counts.get("toc_content", 0) + 1
#                 continue

#         # ── Revision history page ─────────────────────────────────────────
#         if lines:
#             rev_line_ratio = sum(
#                 1 for l in lines
#                 if _DATE_RE.search(l) or _VERSION_RE.search(l)
#             ) / len(lines)
#             if rev_line_ratio >= settings.REVISION_LINE_RATIO:
#                 dropped_counts["revision_history"] = dropped_counts.get("revision_history", 0) + 1
#                 continue

#         # ── Diagram-only page (very little text) ─────────────────────────
#         # Keep it but mark as diagram so it's excluded from chunking
#         if 0 < len(text.strip()) < 200:
#             p = dict(p, content_type="diagram")
#             kept.append(p)
#             continue

#         # ── Glossary / Abbreviations section ─────────────────────────────
#         # Keep for entity resolution hints but mark separately
#         gloss_heading = re.search(
#             r"^#{1,4}\s*(glossary|abbreviations|acronyms|terms and definitions)\s*$",
#             text, re.IGNORECASE | re.MULTILINE,
#         )
#         if gloss_heading:
#             p = dict(p, content_type="glossary")
#             kept.append(p)
#             continue

#         # ── Bibliography / References section ─────────────────────────────
#         bib_heading = re.search(
#             r"^#{1,4}\s*(bibliography|references|normative references|"
#             r"informative references)\s*$",
#             text, re.IGNORECASE | re.MULTILINE,
#         )
#         if bib_heading:
#             p = dict(p, content_type="bibliography")
#             kept.append(p)
#             continue

#         # ── Remove orphaned captions within the page ──────────────────────
#         cleaned_lines = _remove_orphaned_captions(text.splitlines())
#         p = dict(p, markdown="\n".join(cleaned_lines), content_type="content")
#         kept.append(p)

#     return kept, dropped_counts


# def _remove_orphaned_captions(lines: list[str]) -> list[str]:
#     """
#     Remove lines that are standalone figure/table captions with no
#     surrounding sentence context.
#     """
#     result = []
#     for i, line in enumerate(lines):
#         stripped = line.strip()
#         if (
#             len(stripped) <= settings.CAPTION_MAX_LEN
#             and _CAPTION_RE.match(stripped)
#         ):
#             # Check surrounding lines for context
#             prev_empty = (i == 0 or not lines[i-1].strip())
#             next_empty = (i == len(lines)-1 or not lines[i+1].strip())
#             if prev_empty and next_empty:
#                 continue   # orphaned — skip it
#         result.append(line)
#     return result


# # ══════════════════════════════════════════════════════════════════════════════
# # LAYER 3 — Cross-document boilerplate fingerprinting
# # ══════════════════════════════════════════════════════════════════════════════

# def _remove_boilerplate_pages(pages: list[dict]) -> list[dict]:
#     """
#     Find pages whose text is nearly identical across multiple documents
#     (cosine similarity > threshold) and deduplicate them.
#     Keeps the first occurrence, removes subsequent duplicates.

#     Only applied to 'content' pages — not diagrams or glossaries.
#     """
#     try:
#         from sentence_transformers import SentenceTransformer
#         import numpy as np
#     except ImportError:
#         log.warning("sentence-transformers not installed — skipping Layer 3 boilerplate check")
#         return pages

#     content_pages = [(i, p) for i, p in enumerate(pages) if p.get("content_type") == "content"]
#     if len(content_pages) < 10:
#         # Not enough pages to do meaningful boilerplate detection
#         return pages

#     # Only check the first and last 3 pages of each document (boilerplate location)
#     from collections import defaultdict
#     by_source: dict[str, list[tuple[int, dict]]] = defaultdict(list)
#     for i, p in content_pages:
#         by_source[p["source"]].append((i, p))

#     candidate_indices: list[int] = []
#     candidate_texts:   list[str] = []
#     for doc_pages in by_source.values():
#         for idx, p in doc_pages[:3] + doc_pages[-3:]:
#             candidate_indices.append(idx)
#             candidate_texts.append(p["markdown"][:500])  # first 500 chars is enough

#     if len(candidate_texts) < 4:
#         return pages

#     log.info("Layer 3: fingerprinting %d candidate boilerplate pages ...", len(candidate_texts))

#     model = SentenceTransformer(settings.EMBED_MODEL)
#     embeddings = model.encode(
#         candidate_texts,
#         normalize_embeddings=True,
#         batch_size=32,
#         show_progress_bar=False,
#     )

#     # Find pairs above threshold
#     sim_matrix = embeddings @ embeddings.T  # cosine similarity
#     pages_to_drop: set[int] = set()

#     for i in range(len(candidate_indices)):
#         if candidate_indices[i] in pages_to_drop:
#             continue
#         for j in range(i + 1, len(candidate_indices)):
#             if candidate_indices[j] in pages_to_drop:
#                 continue
#             # Only flag as boilerplate if from different documents
#             p_i = pages[candidate_indices[i]]
#             p_j = pages[candidate_indices[j]]
#             if (
#                 p_i["source"] != p_j["source"]
#                 and sim_matrix[i, j] >= settings.BOILERPLATE_SIM_THRESHOLD
#             ):
#                 pages_to_drop.add(candidate_indices[j])

#     if pages_to_drop:
#         log.info("Layer 3: dropping %d boilerplate page(s)", len(pages_to_drop))

#     return [p for i, p in enumerate(pages) if i not in pages_to_drop]


# # ══════════════════════════════════════════════════════════════════════════════
# # Helpers
# # ══════════════════════════════════════════════════════════════════════════════

# def _normalize_whitespace(text: str) -> str:
#     """Collapse 3+ consecutive blank lines to 2, strip trailing spaces."""
#     text = re.sub(r"\n{3,}", "\n\n", text)
#     text = "\n".join(line.rstrip() for line in text.splitlines())
#     return text.strip()


"""
pipeline/stage2_clean.py
========================
Stage 2: Noise removal — now driven by LLM page-type labels from Stage 1.

Stage 1 already classified every page. Stage 2 now:
  1. Drops pages whose content_type is None (toc, cover, revision, legal)
  2. Runs LLM-based inline text cleaning on remaining content pages
     — strips residual noise the LLM classifier cannot remove in Stage 1
  3. Keeps Layer 1 repeated-line removal (structural, not semantic)
  4. Keeps Layer 3 cross-document boilerplate fingerprinting (BGE-M3)

The old regex heuristics (TOC ratio, revision ratio, legal triggers,
near-blank threshold) are removed — replaced by the Stage 1 LLM labels.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from pathlib import Path

from utils.logger import get_logger
from utils.llm_client import acall_llm_text
from config import settings

log = get_logger("stage2")

# Pages with these content_types are passed through to chunking
_PASSTHROUGH_TYPES = {"content", "index_changelog", "glossary", "bibliography", "diagram"}

_CLEAN_SYSTEM = """You are cleaning an AUTOSAR specification page for a knowledge graph pipeline.

Your task: return the cleaned version of the page text.

Remove:
- Inline page counters like "1 of 14", "5 of 71" anywhere in the text
- Lines that are ONLY a page number (standalone digit or "Page N")
- ==> picture ... omitted <== placeholders
- Redundant <br> HTML tags (replace with a space)
- |---|---| and |:---|:---| table separator rows (keep data rows)
- Lines that are only pipe characters and dashes: |---|---|---|
- "AUTOSAR CONFIDENTIAL" and "— AUTOSAR CONFIDENTIAL —" lines
- "Document ID NNN: AUTOSAR_..." footer lines

Preserve EXACTLY:
- All requirement IDs like [SWS_X_00123], [RS_X_00201] — do NOT alter these
- All heading markers (#, ##, ###)
- All table data rows (lines starting with | that contain real content)
- All normative text (SHALL, SHOULD, MAY statements)
- All section numbers and titles

Return ONLY the cleaned text. No explanation, no preamble."""


def run(pages: list[dict]) -> list[dict]:
    log.info("Stage 2: noise removal on %d pages", len(pages))

    # ── Step 1: Drop pages classified as noise by Stage 1 LLM ────────────────
    pages, drop_counts = _drop_noise_pages(pages)
    log.info("After LLM-type filtering: %d pages remain", len(pages))
    for reason, n in sorted(drop_counts.items(), key=lambda x: -x[1]):
        log.info("  dropped %4d :: %s", n, reason)

    # ── Step 2: Layer 1 — repeated line removal (structural) ──────────────────
    pages = _remove_repeated_lines(pages)

    # ── Step 3: LLM inline cleaning on content pages ──────────────────────────
    content_pages  = [p for p in pages if p.get("content_type") == "content"]
    other_pages    = [p for p in pages if p.get("content_type") != "content"]

    if content_pages:
        log.info("LLM inline cleaning on %d content pages ...", len(content_pages))
        cleaned = asyncio.run(_clean_all_pages(content_pages))
        pages = cleaned + other_pages
        # Re-sort by (source, page) to preserve original order
        pages.sort(key=lambda p: (p["source"], p["page"]))

    # ── Step 4: Layer 3 — cross-document boilerplate dedup (BGE-M3) ──────────
    pages = _remove_boilerplate_pages(pages)

    # ── Final whitespace normalisation ───────────────────────────────────────
    for p in pages:
        p["markdown"]   = _normalize_whitespace(p["markdown"])
        p["char_count"] = len(p["markdown"])

    log.info("Stage 2 complete: %d pages kept", len(pages))
    return pages


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Drop noise pages based on Stage 1 LLM labels
# ══════════════════════════════════════════════════════════════════════════════

def _drop_noise_pages(pages: list[dict]) -> tuple[list[dict], dict]:
    kept: list[dict] = []
    drop_counts: dict[str, int] = {}

    for p in pages:
        ct = p.get("content_type")
        pt = p.get("page_type", "content")

        if ct is None:
            # Stage 1 classified this as noise (toc/cover/revision/legal)
            drop_counts[pt] = drop_counts.get(pt, 0) + 1
            continue

        # Near-blank safety net — if classification returned content but
        # the page is essentially empty after noise stripping, drop it
        if len(p["markdown"].strip()) < 80:
            drop_counts["near_blank"] = drop_counts.get("near_blank", 0) + 1
            continue

        kept.append(p)

    return kept, drop_counts


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Repeated line removal
# ══════════════════════════════════════════════════════════════════════════════

def _remove_repeated_lines(pages: list[dict]) -> list[dict]:
    by_source: dict[str, list[dict]] = defaultdict(list)
    for p in pages:
        by_source[p["source"]].append(p)

    result: list[dict] = []
    for source, doc_pages in by_source.items():
        n_pages = len(doc_pages)
        if n_pages == 0:
            continue

        line_page_count: Counter = Counter()
        for p in doc_pages:
            seen = set()
            for line in p["markdown"].splitlines():
                stripped = line.strip()
                if stripped and stripped not in seen:
                    line_page_count[stripped] += 1
                    seen.add(stripped)

        threshold_count = max(2, int(n_pages * settings.REPEATED_LINE_THRESHOLD))
        repeated = {
            line for line, count in line_page_count.items()
            if count >= threshold_count
        }

        if repeated:
            log.debug(
                "%s: removing %d repeated line(s)",
                Path(source).name, len(repeated),
            )

        for p in doc_pages:
            cleaned = [
                line for line in p["markdown"].splitlines()
                if line.strip() not in repeated
            ]
            p = dict(p, markdown="\n".join(cleaned))
            result.append(p)

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — LLM inline cleaning
# ══════════════════════════════════════════════════════════════════════════════

async def _clean_all_pages(pages: list[dict]) -> list[dict]:
    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    tasks = [_clean_page(p, semaphore) for p in pages]
    return list(await asyncio.gather(*tasks))


async def _clean_page(page: dict, semaphore: asyncio.Semaphore) -> dict:
    text = page["markdown"]
    if len(text.strip()) < 100:
        return page

    cleaned = await acall_llm_text(
        system=_CLEAN_SYSTEM,
        user=text,
        semaphore=semaphore,
    )

    if cleaned and len(cleaned) > 50:
        page = dict(page, markdown=cleaned, char_count=len(cleaned))

    return page


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Cross-document boilerplate dedup (BGE-M3)
# ══════════════════════════════════════════════════════════════════════════════

def _remove_boilerplate_pages(pages: list[dict]) -> list[dict]:
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        log.warning("sentence-transformers not installed — skipping boilerplate dedup")
        return pages

    content_pages = [(i, p) for i, p in enumerate(pages) if p.get("content_type") == "content"]
    if len(content_pages) < 10:
        return pages

    by_source: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for i, p in content_pages:
        by_source[p["source"]].append((i, p))

    candidate_indices: list[int] = []
    candidate_texts:   list[str] = []
    for doc_pages in by_source.values():
        for idx, p in doc_pages[:3] + doc_pages[-3:]:
            candidate_indices.append(idx)
            candidate_texts.append(p["markdown"][:500])

    if len(candidate_texts) < 4:
        return pages

    log.info("Layer 3: fingerprinting %d candidate boilerplate pages ...", len(candidate_texts))
    model      = SentenceTransformer(settings.EMBED_MODEL)
    embeddings = model.encode(
        candidate_texts,
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=False,
    )

    sim_matrix    = embeddings @ embeddings.T
    pages_to_drop: set[int] = set()

    for i in range(len(candidate_indices)):
        if candidate_indices[i] in pages_to_drop:
            continue
        for j in range(i + 1, len(candidate_indices)):
            if candidate_indices[j] in pages_to_drop:
                continue
            p_i = pages[candidate_indices[i]]
            p_j = pages[candidate_indices[j]]
            if (
                p_i["source"] != p_j["source"]
                and sim_matrix[i, j] >= settings.BOILERPLATE_SIM_THRESHOLD
            ):
                pages_to_drop.add(candidate_indices[j])

    if pages_to_drop:
        log.info("Layer 3: dropping %d boilerplate page(s)", len(pages_to_drop))

    return [p for i, p in enumerate(pages) if i not in pages_to_drop]


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()