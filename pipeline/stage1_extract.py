"""
pipeline/stage1_extract.py
==========================
Stage 1: PDF → per-page Markdown + LLM page-type classification.

Two sub-steps:
  1a. pymupdf4llm extraction (deterministic, fast)
  1b. LLM page-type classification for every page (concurrent, async)

Page types assigned by LLM:
    content          — real specification content, keep and chunk
    toc              — table of contents, drop
    cover            — document metadata cover page, drop
    revision         — revision/change history table, drop
    legal            — legal disclaimer / copyright, drop
    index_changelog  — added/changed/deleted items index tables, keep but
                       flag so Stage 3 skips cross-ref pair generation
    diagram          — mostly figures with little text, keep metadata only
    abbreviations    — abbreviations/glossary list, keep as glossary type
    bibliography     — references/bibliography section, keep as bibliography

Output page dict adds:
    "page_type":     one of the types above
    "content_type":  mapped from page_type for downstream compatibility
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from utils.logger import get_logger
from utils.llm_client import acall_llm_json
from config import settings

log = get_logger("stage1")

# _CLASSIFY_SYSTEM = """You are processing AUTOSAR specification PDF pages.
# Classify each page into exactly one type.

# Return ONLY a JSON object: {"type": "<type>", "confidence": 0.0-1.0}
# No markdown, no explanation.

# Types:
# - content         Real specification text with requirements, descriptions, API specs
# - toc             Table of contents (entries with page numbers and dots/tabs)
# - cover           Document cover page / metadata table (Document Title, Owner, ID, Status)
# - revision        Revision/change history table (dates, versions, authors, descriptions)
# - legal           Legal disclaimer, copyright, confidentiality notice
# - index_changelog Annex/appendix listing added/changed/deleted spec items as a flat table
# - diagram         Page dominated by figures, UML diagrams; very little text
# - abbreviations   Abbreviations, acronyms, or glossary list
# - bibliography    References, bibliography, normative/informative references list"""

_CLASSIFY_SYSTEM = """You are a high-precision AUTOSAR specification PDF page classifier.

    You are given exactly ONE PDF page at a time.

    Your task is to classify the page into exactly ONE page type based on the page's PRIMARY purpose and DOMINANT content signal.

    You must return ONLY a valid JSON object in this exact format:
    {"type": "<type>", "confidence": 0.0-1.0}

    STRICT OUTPUT RULES:
    - Output ONLY JSON
    - No markdown
    - No explanations
    - No extra keys
    - No comments
    - No trailing text
    - Confidence must be a float between 0.0 and 1.0

    ALLOWED TYPES:
    - content
    - toc
    - cover
    - revision
    - legal
    - index_changelog
    - diagram
    - abbreviations
    - bibliography

    CLASSIFICATION GUIDELINES:

    1. content
    Use when the page primarily contains:
    - AUTOSAR requirements
    - API specifications
    - parameter descriptions
    - behavior definitions
    - architectural explanations
    - protocol descriptions
    - structured technical paragraphs
    - specification tables tied to functionality

    This is the default type for real specification material.

    2. toc
    Use when the page is primarily a table of contents.
    Strong signals:
    - hierarchical section listings
    - dotted leaders or tab spacing
    - many page numbers
    - chapter/subchapter navigation entries

    3. cover
    Use for document identity or metadata pages.
    Strong signals:
    - document title
    - document ID
    - release/status
    - owner information
    - AUTOSAR logo/header metadata
    - approval metadata
    - document information tables

    4. revision
    Use for revision history or change tracking pages.
    Strong signals:
    - version history tables
    - change descriptions
    - dates
    - authors
    - release history
    - modification logs

    5. legal
    Use for legal or compliance pages.
    Strong signals:
    - copyright notices
    - confidentiality statements
    - licensing text
    - liability disclaimers
    - legal restrictions
    - trademarks

    6. index_changelog
    Use for annex/index-style pages listing changed items.
    Strong signals:
    - flat structured tables
    - added/modified/deleted entries
    - requirement IDs
    - change indexes
    - appendix-style delta listings

    Do NOT use for normal revision history pages.

    7. diagram
    Use when the page is visually dominated by:
    - UML diagrams
    - flowcharts
    - architecture figures
    - block diagrams
    - sequence diagrams
    - graphical relationships

    The page should contain relatively little continuous prose.

    8. abbreviations
    Use for glossary-like pages.
    Strong signals:
    - acronym expansions
    - abbreviation tables
    - terminology definitions
    - glossary entries
    - term-definition mappings

    9. bibliography
    Use for references or cited materials.
    Strong signals:
    - normative references
    - informative references
    - standards lists
    - external documents
    - citations
    - bibliography sections

    IMPORTANT DECISION RULES:
    - Choose EXACTLY ONE type
    - Use the DOMINANT page purpose
    - Ignore headers/footers/page numbers unless they dominate the page
    - If mixed content exists, classify using the majority signal
    - Prefer "content" when real specification text dominates
    - Prefer "diagram" only if graphics dominate the page visually
    - Prefer "revision" over "index_changelog" for normal version history tables
    - Prefer "index_changelog" only for annex-style changed-item listings
    - Do not infer document-level meaning from neighboring pages
    - Classify ONLY the current page

    CONFIDENCE GUIDELINES:
    - 0.95-1.00 = extremely clear classification
    - 0.80-0.94 = strong signal with minor ambiguity
    - 0.60-0.79 = moderate ambiguity
    - 0.40-0.59 = weak or mixed signals

    Return ONLY the JSON object.
    """

_TYPE_TO_CONTENT_TYPE = {
    "content":         "content",
    "index_changelog": "index_changelog",
    "diagram":         "diagram",
    "abbreviations":   "glossary",
    "bibliography":    "bibliography",
    "toc":      None,
    "cover":    None,
    "revision": None,
    "legal":    None,
}


def run(pdf_dir: Path) -> list[dict]:
    try:
        import pymupdf4llm
    except ImportError:
        sys.exit("pymupdf4llm not installed. Run: pip install pymupdf4llm")

    pdfs = sorted(pdf_dir.glob("**/*.pdf"))
    if not pdfs:
        sys.exit(f"No PDFs found in {pdf_dir}")

    log.info("Found %d PDF(s) in %s", len(pdfs), pdf_dir)

    all_pages: list[dict] = []
    skipped_files = 0

    for pdf_path in pdfs:
        log.info("Extracting: %s", pdf_path.name)
        try:
            pages = _extract_pdf(pdf_path)
            all_pages.extend(pages)
            log.info("  %d pages extracted", len(pages))
        except Exception as exc:
            log.warning("SKIPPED %s — %s: %s", pdf_path.name, type(exc).__name__, exc)
            skipped_files += 1

    log.info(
        "Extraction complete: %d pages from %d file(s) (%d skipped)",
        len(all_pages), len(pdfs) - skipped_files, skipped_files,
    )

    log.info("Classifying %d pages with LLM ...", len(all_pages))
    all_pages = asyncio.run(_classify_all_pages(all_pages))

    from collections import Counter
    type_counts = Counter(p["page_type"] for p in all_pages)
    for ptype, n in type_counts.most_common():
        log.info("  %-20s %d pages", ptype, n)

    log.info("Stage 1 complete: %d pages classified", len(all_pages))
    return all_pages


def _extract_pdf(pdf_path: Path) -> list[dict]:
    import pymupdf4llm

    page_chunks = pymupdf4llm.to_markdown(
        str(pdf_path),
        page_chunks=True,
        margins=(0, settings.PDF_HEADER_MARGIN, 0, settings.PDF_FOOTER_MARGIN),
        show_progress=False,
    )

    pages: list[dict] = []
    for chunk in page_chunks:
        meta     = chunk.get("metadata", {})
        page_idx = meta.get("page", len(pages))
        text     = chunk.get("text", "")
        text     = _strip_inline_noise(text)

        pages.append({
            "source":        str(pdf_path),
            "filename":      pdf_path.name,
            "page":          page_idx,
            "page_1idx":     page_idx + 1,
            "markdown":      text,
            "char_count":    len(text),
            "page_type":     "content",
            "content_type":  "content",
        })

    return pages


def _strip_inline_noise(text: str) -> str:
    """Remove inline patterns that margin cropping cannot catch."""
    # "N of M" page counters
    text = re.sub(r"\b\d{1,4}\s+of\s+\d{1,4}\b\s*\n?", "", text)
    # picture omitted placeholders
    text = re.sub(
        r"==>.*?(?:omitted|intentionally omitted).*?<==\s*\n?",
        "", text, flags=re.IGNORECASE
    )
    # collapse triple+ newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _classify_all_pages(pages: list[dict]) -> list[dict]:
    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    tasks = [_classify_page(page, semaphore) for page in pages]
    return list(await asyncio.gather(*tasks))


async def _classify_page(page: dict, semaphore: asyncio.Semaphore) -> dict:
    excerpt = page["markdown"][:600]
    if not excerpt.strip():
        page["page_type"]    = "content"
        page["content_type"] = "content"
        return page

    result = await acall_llm_json(
        system=_CLASSIFY_SYSTEM,
        user=f"Page {page['page_1idx']} from {page['filename']}:\n\n{excerpt}",
        semaphore=semaphore,
    )

    page_type = "content"
    if result and isinstance(result, dict):
        raw_type = str(result.get("type", "content")).lower().strip()
        if raw_type in _TYPE_TO_CONTENT_TYPE:
            page_type = raw_type

    page["page_type"]    = page_type
    page["content_type"] = _TYPE_TO_CONTENT_TYPE.get(page_type, "content")
    return page