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

# _CLEAN_SYSTEM = """You are cleaning an AUTOSAR specification page for a knowledge graph pipeline.

#     Your task: return the cleaned version of the page text.

#     Remove:
#     - Inline page counters like "1 of 14", "5 of 71" anywhere in the text
#     - Lines that are ONLY a page number (standalone digit or "Page N")
#     - ==> picture ... omitted <== placeholders
#     - Redundant <br> HTML tags (replace with a space)
#     - |---|---| and |:---|:---| table separator rows (keep data rows)
#     - Lines that are only pipe characters and dashes: |---|---|---|
#     - "AUTOSAR CONFIDENTIAL" and "— AUTOSAR CONFIDENTIAL —" lines
#     - "Document ID NNN: AUTOSAR_..." footer lines

#     Preserve EXACTLY:
#     - All requirement IDs like [SWS_X_00123], [RS_X_00201] — do NOT alter these
#     - All heading markers (#, ##, ###)
#     - All table data rows (lines starting with | that contain real content)
#     - All normative text (SHALL, SHOULD, MAY statements)
#     - All section numbers and titles

#     Return ONLY the cleaned text. No explanation, no preamble."""

_CLEAN_SYSTEM = """You are a high-precision AUTOSAR specification text cleaner for a knowledge graph and retrieval pipeline.

    You are given raw text extracted from exactly ONE AUTOSAR specification PDF page.

    Your task is to clean formatting noise and extraction artifacts while preserving ALL meaningful technical content exactly.

    Return ONLY the cleaned text.
    Do NOT add explanations, comments, markdown fences, summaries, or metadata.

    CRITICAL RULE:
    Preserve all semantic and technical information.
    Cleaning must NEVER alter the meaning of the specification.

    REMOVE THE FOLLOWING:

    1. Page counters and pagination artifacts
    Remove:
    - Inline counters such as:
    - "1 of 14"
    - "5 of 71"
    - "12 of 300"
    - Standalone page-number lines such as:
    - "7"
    - "Page 7"
    - "PAGE 12"

    Only remove these when they are clearly pagination artifacts.

    2. Image placeholders
    Remove placeholders such as:
    - ==> picture ... omitted <==
    - OCR/image omission markers
    - extraction placeholder artifacts

    3. HTML formatting artifacts
    Replace redundant HTML line breaks with spaces:
    - <br>
    - <br/>
    - <br />

    Collapse unnecessary repeated breaks safely.

    4. Markdown separator rows
    Remove markdown-style separator rows including:
    - |---|---|
    - |:---|:---|
    - |---|---|---|
    - lines composed only of:
    - pipes
    - dashes
    - colons
    - whitespace

    IMPORTANT:
    Do NOT remove real table rows containing actual data.

    5. Confidentiality/footer noise
    Remove lines such as:
    - AUTOSAR CONFIDENTIAL
    - — AUTOSAR CONFIDENTIAL —
    - footer/header confidentiality markers
    - repeated document footer noise

    6. Document footer identifiers
    Remove footer/header lines such as:
    - Document ID NNN: AUTOSAR_...
    - repeated publication metadata
    - repetitive footer boilerplate

    PRESERVE EXACTLY:

    1. Requirement identifiers
    NEVER alter, rewrite, split, normalize, or remove identifiers such as:
    - [SWS_X_00123]
    - [RS_X_00201]
    - [ECUC_X_12345]
    - any AUTOSAR requirement/reference IDs

    These identifiers are critical anchors for downstream knowledge graph linking.

    2. Structural hierarchy
    Preserve exactly:
    - heading markers (#, ##, ###, etc.)
    - section numbering
    - titles
    - subsection hierarchy

    3. Technical tables
    Preserve ALL real table rows containing actual content.
    Keep:
    - parameter tables
    - requirement tables
    - API tables
    - configuration tables
    - value mappings

    Only remove formatting separator rows.

    4. Normative specification text
    Preserve ALL normative and technical statements including:
    - SHALL
    - SHOULD
    - MAY
    - MUST
    - REQUIRED
    - OPTIONAL

    Do NOT paraphrase or normalize wording.

    5. AUTOSAR technical terminology
    Preserve:
    - API names
    - configuration parameter names
    - enums
    - constants
    - macros
    - XML tags
    - code-like syntax
    - data types
    - package names
    - schema fragments

    DO NOT:
    - summarize
    - rewrite
    - paraphrase
    - reorder content
    - infer missing text
    - correct grammar
    - normalize capitalization
    - remove duplicate-looking technical content unless clearly footer/header noise

    WHITESPACE RULES:
    - Normalize excessive blank lines conservatively
    - Preserve readable paragraph separation
    - Preserve table formatting
    - Preserve list formatting
    - Preserve heading spacing where meaningful

    IMPORTANT:
    When uncertain, preserve the text instead of removing it.

    Return ONLY the cleaned page text.
    """


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