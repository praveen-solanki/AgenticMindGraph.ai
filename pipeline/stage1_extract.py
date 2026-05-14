# """
# pipeline/stage1_extract.py
# ==========================
# Stage 1: PDF → per-page Markdown using pymupdf4llm.

# Output schema (list of dicts, one per page):
#     {
#         "source":      "path/to/file.pdf",
#         "filename":    "AUTOSAR_SWS_ComM.pdf",
#         "page":        3,           # 0-indexed
#         "page_1idx":   4,           # 1-indexed (human-readable)
#         "markdown":    "## 3.2 ...",
#         "char_count":  1842,
#     }
# """

# from __future__ import annotations

# import sys
# from pathlib import Path
# from typing import TYPE_CHECKING

# from utils.logger import get_logger
# from config import settings

# if TYPE_CHECKING:
#     pass

# log = get_logger("stage1")


# def run(pdf_dir: Path) -> list[dict]:
#     """
#     Extract all PDFs under pdf_dir.
#     Returns a flat list of page dicts (one entry per page).
#     """
#     try:
#         import pymupdf4llm
#         import pymupdf  # fitz
#     except ImportError:
#         sys.exit("pymupdf4llm not installed. Run: pip install pymupdf4llm")

#     pdfs = sorted(pdf_dir.glob("**/*.pdf"))
#     if not pdfs:
#         sys.exit(f"No PDFs found in {pdf_dir}")

#     log.info("Found %d PDF(s) in %s", len(pdfs), pdf_dir)

#     all_pages: list[dict] = []
#     skipped_files = 0

#     for pdf_path in pdfs:
#         log.info("Extracting: %s", pdf_path.name)
#         try:
#             pages = _extract_pdf(pdf_path)
#             all_pages.extend(pages)
#             log.info("  → %d pages extracted", len(pages))
#         except Exception as exc:
#             log.warning("SKIPPED %s — %s: %s", pdf_path.name, type(exc).__name__, exc)
#             skipped_files += 1

#     log.info(
#         "Stage 1 complete: %d pages from %d file(s) (%d skipped)",
#         len(all_pages), len(pdfs) - skipped_files, skipped_files,
#     )
#     return all_pages


# def _extract_pdf(pdf_path: Path) -> list[dict]:
#     """Extract one PDF, return list of page dicts."""
#     import pymupdf4llm
#     import pymupdf

#     doc = pymupdf.open(str(pdf_path))
#     n_pages = len(doc)
#     doc.close()

#     # pymupdf4llm.to_markdown with page_chunks=True returns one dict per page.
#     # margins: (left, top, right, bottom) as fractions 0-1 of page dimensions.
#     # We crop top and bottom to remove running headers and footers.
#     page_chunks = pymupdf4llm.to_markdown(
#         str(pdf_path),
#         page_chunks=True,          # one dict per page
#         margins=(                  # (left, top, right, bottom) fractions
#             0,
#             settings.PDF_HEADER_MARGIN,
#             0,
#             settings.PDF_FOOTER_MARGIN,
#         ),
#         show_progress=False,
#     )

#     pages: list[dict] = []
#     for chunk in page_chunks:
#         # page_chunks returns dicts with keys: 'metadata', 'text'
#         # metadata contains: 'page' (0-indexed), 'file_path', etc.
#         meta     = chunk.get("metadata", {})
#         page_idx = meta.get("page", len(pages))       # 0-indexed
#         text     = chunk.get("text", "")

#         pages.append({
#             "source":    str(pdf_path),
#             "filename":  pdf_path.name,
#             "page":      page_idx,
#             "page_1idx": page_idx + 1,
#             "markdown":  text,
#             "char_count": len(text),
#         })

#     return pages


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

_CLASSIFY_SYSTEM = """You are processing AUTOSAR specification PDF pages.
Classify each page into exactly one type.

Return ONLY a JSON object: {"type": "<type>", "confidence": 0.0-1.0}
No markdown, no explanation.

Types:
- content         Real specification text with requirements, descriptions, API specs
- toc             Table of contents (entries with page numbers and dots/tabs)
- cover           Document cover page / metadata table (Document Title, Owner, ID, Status)
- revision        Revision/change history table (dates, versions, authors, descriptions)
- legal           Legal disclaimer, copyright, confidentiality notice
- index_changelog Annex/appendix listing added/changed/deleted spec items as a flat table
- diagram         Page dominated by figures, UML diagrams; very little text
- abbreviations   Abbreviations, acronyms, or glossary list
- bibliography    References, bibliography, normative/informative references list"""

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