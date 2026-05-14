# """
# pipeline/stage3_harvest.py
# ==========================
# Stage 3: Requirement ID pre-harvesting (AUTOSAR-specific, rule-based).

# Runs BEFORE chunking. Does a regex pass over all cleaned page text and
# builds a complete inventory of every requirement/constraint/parameter ID
# found in the corpus.

# Output schema:
#     {
#         "id_inventory": {
#             "SWS_ComM_00123": {
#                 "full_id":    "[SWS_ComM_00123]",
#                 "id_type":    "SWS",
#                 "module":     "ComM",
#                 "occurrences": [
#                     {"source": "AUTOSAR_SWS_ComM.pdf", "page": 47},
#                     ...
#                 ]
#             },
#             ...
#         },
#         "cross_refs": [
#             {
#                 "from_id":  "SWS_ComM_00123",
#                 "to_id":    "SWS_Can_00456",
#                 "source":   "AUTOSAR_SWS_ComM.pdf",
#                 "page":     47,
#             },
#             ...
#         ],
#         "doc_modules": {
#             "AUTOSAR_SWS_ComM.pdf": "ComM",
#             ...
#         }
#     }
# """

# from __future__ import annotations

# import re
# from collections import defaultdict
# from pathlib import Path

# from utils.logger import get_logger
# from config import settings

# log = get_logger("stage3")

# # ── Compiled patterns ─────────────────────────────────────────────────────────

# # Each pattern captures the full bracketed ID like [SWS_ComM_00123]
# _ID_PATTERNS: list[re.Pattern] = [
#     re.compile(p) for p in settings.REQUIREMENT_ID_PATTERNS
# ]

# # Module name from document filename:
# # AUTOSAR_SWS_CommunicationManager → "CommunicationManager"
# # AUTOSAR_SWS_ComM_v5 → "ComM"
# _DOC_MODULE_RE = re.compile(
#     r"AUTOSAR_(?:SWS|SRS|EXP|TPS|MOD|MMOD)_([A-Za-z0-9]+)",
#     re.IGNORECASE,
# )

# # Extract the bare ID from [SWS_ComM_00123] → "SWS_ComM_00123"
# _BARE_ID_RE = re.compile(r"\[([A-Za-z_]+_\d{4,5})\]")


# def run(pages: list[dict]) -> dict:
#     """
#     Harvest all AUTOSAR requirement IDs from the cleaned page corpus.

#     Returns a dict with three keys:
#         id_inventory   — all unique IDs and where they appear
#         cross_refs     — (from_id, to_id) co-occurrence pairs within same page
#         doc_modules    — filename → module name mapping
#     """
#     log.info("Stage 3: harvesting requirement IDs from %d pages", len(pages))

#     id_inventory: dict[str, dict]          = {}
#     cross_refs:   list[dict]               = []
#     doc_modules:  dict[str, str]           = {}

#     # Infer module name from each document filename
#     sources = {p["source"] for p in pages}
#     for source in sources:
#         fname = Path(source).name
#         m = _DOC_MODULE_RE.search(fname)
#         module = m.group(1) if m else "Unknown"
#         doc_modules[fname] = module

#     for page in pages:
#         if page.get("content_type") in ("diagram",):
#             continue  # skip diagram-only pages

#         text   = page["markdown"]
#         source = page["source"]
#         fname  = Path(source).name
#         pg     = page["page_1idx"]

#         # Find all IDs on this page
#         page_ids: list[str] = []
#         for pattern in _ID_PATTERNS:
#             for match in pattern.finditer(text):
#                 full_id = match.group(0)              # e.g. [SWS_ComM_00123]
#                 bare    = _BARE_ID_RE.match(full_id)
#                 if not bare:
#                     continue
#                 bare_id = bare.group(1)               # e.g. SWS_ComM_00123

#                 # Parse ID components
#                 parts   = bare_id.split("_")
#                 id_type = parts[0] if parts else "UNKNOWN"
#                 module  = parts[1] if len(parts) > 1 else doc_modules.get(fname, "Unknown")

#                 if bare_id not in id_inventory:
#                     id_inventory[bare_id] = {
#                         "full_id":     full_id,
#                         "bare_id":     bare_id,
#                         "id_type":     id_type,
#                         "module":      module,
#                         "occurrences": [],
#                     }

#                 # Record occurrence (avoid duplicates per page)
#                 occ = {"source": fname, "page": pg}
#                 if occ not in id_inventory[bare_id]["occurrences"]:
#                     id_inventory[bare_id]["occurrences"].append(occ)

#                 page_ids.append(bare_id)

#         # Build cross-reference pairs: every pair of IDs co-occurring on the
#         # same page is a potential REFERENCES relationship (validated by LLM later)
#         unique_page_ids = list(dict.fromkeys(page_ids))  # deduplicate, preserve order
#         for i, from_id in enumerate(unique_page_ids):
#             for to_id in unique_page_ids[i + 1 :]:
#                 cross_refs.append({
#                     "from_id": from_id,
#                     "to_id":   to_id,
#                     "source":  fname,
#                     "page":    pg,
#                 })

#     n_ids   = len(id_inventory)
#     n_xrefs = len(cross_refs)
#     log.info(
#         "Stage 3 complete: %d unique IDs, %d cross-reference pairs",
#         n_ids, n_xrefs,
#     )

#     # Log breakdown by type
#     type_counts: dict[str, int] = defaultdict(int)
#     for info in id_inventory.values():
#         type_counts[info["id_type"]] += 1
#     for id_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
#         log.info("  %s: %d IDs", id_type, count)

#     return {
#         "id_inventory": id_inventory,
#         "cross_refs":   cross_refs,
#         "doc_modules":  doc_modules,
#     }


"""
pipeline/stage3_harvest.py
==========================
Stage 3: Requirement ID pre-harvesting + LLM cross-reference validation.

Sub-steps:
  3a. Regex ID harvesting (deterministic, exact — unchanged)
  3b. LLM cross-reference validation (replaces combinatorial co-occurrence)
  3c. LLM requirement body extraction (clean normative text per requirement)

Key improvements over original:
  - doc_modules now comes from Stage 0 corpus analysis (not broken filename regex)
  - index_changelog pages are skipped for cross-ref pair generation entirely
  - Cross-ref pairs are validated by LLM before becoming REFERENCES edges
  - Pages with >15 IDs (index pages that slipped through) are skipped for pairs
  - Requirement body text is extracted by LLM for clean raw_text on nodes
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from utils.logger import get_logger
from utils.llm_client import acall_llm_json, acall_llm_text
from config import settings

log = get_logger("stage3")

# ── Compiled patterns ─────────────────────────────────────────────────────────
_ID_PATTERNS: list[re.Pattern] = [
    re.compile(p) for p in settings.REQUIREMENT_ID_PATTERNS
]
_BARE_ID_RE = re.compile(r"\[([A-Za-z_0-9]+_\d{4,5})\]")

# Cross-ref validation prompt
_XREF_SYSTEM = """You are an AUTOSAR specification expert.
Given a page of AUTOSAR specification text and a list of requirement ID pairs
that co-occur on the page, determine which pairs represent GENUINE reference
relationships — i.e. one requirement explicitly references, satisfies,
refines, or is derived from the other in the text.

Return ONLY a JSON array of validated pairs:
[{"from": "ID_A", "to": "ID_B", "rel_type": "REFERENCES"|"DERIVED_FROM"|"TRACES_TO"|"REFINES"}, ...]

Return an empty array [] if no genuine relationships exist.
Do NOT include pairs that just happen to appear on the same page without
an explicit textual relationship."""

# Requirement body extraction prompt
_REQ_BODY_SYSTEM = """You are extracting AUTOSAR requirement text.
Given a page of AUTOSAR specification text and a requirement ID,
return ONLY the normative text of that requirement — the SHALL/SHOULD/MAY
statement — stripped of table formatting, cross-reference noise, and
footnote markers.

Return ONLY the clean requirement text as a plain string.
If the requirement body cannot be found, return an empty string."""


def run(pages: list[dict], corpus_meta: Optional[dict] = None) -> dict:
    log.info("Stage 3: harvesting requirement IDs from %d pages", len(pages))

    id_inventory: dict[str, dict] = {}
    doc_modules:  dict[str, str]  = {}

    # ── Use Stage 0 module map if available; fall back to ID-based inference ──
    if corpus_meta and corpus_meta.get("doc_modules"):
        doc_modules = corpus_meta["doc_modules"]
        log.info("  Using Stage 0 module map (%d documents)", len(doc_modules))
    else:
        # Fallback: try filename regex, then Unknown
        _DOC_MODULE_RE = re.compile(
            r"AUTOSAR_(?:SWS|SRS|EXP|TPS|MOD|MMOD|RS|TR|PRS|ATS)_([A-Za-z0-9]+)",
            re.IGNORECASE,
        )
        sources = {p["source"] for p in pages}
        for source in sources:
            fname = Path(source).name
            m = _DOC_MODULE_RE.search(fname)
            doc_modules[fname] = m.group(1) if m else "Unknown"

    # ── 3a: Regex ID harvesting ───────────────────────────────────────────────
    candidate_pairs: list[dict] = []  # pairs to be LLM-validated

    for page in pages:
        ct = page.get("content_type", "content")
        if ct in ("diagram",):
            continue

        text  = page["markdown"]
        fname = page["filename"]
        pg    = page["page_1idx"]

        # Harvest IDs — deduplicate across all patterns on this page
        page_ids: list[str] = []
        seen_on_page: set[str] = set()

        for pattern in _ID_PATTERNS:
            for match in pattern.finditer(text):
                full_id = match.group(0)
                bare    = _BARE_ID_RE.match(full_id)
                if not bare:
                    continue
                bare_id = bare.group(1)

                if bare_id in seen_on_page:
                    continue
                seen_on_page.add(bare_id)
                page_ids.append(bare_id)

                parts   = bare_id.split("_")
                id_type = parts[0] if parts else "UNKNOWN"
                # Module: from ID parts if possible, else from doc_modules
                module  = parts[1] if len(parts) > 2 else doc_modules.get(fname, "Unknown")

                if bare_id not in id_inventory:
                    id_inventory[bare_id] = {
                        "full_id":     full_id,
                        "bare_id":     bare_id,
                        "id_type":     id_type,
                        "module":      module,
                        "occurrences": [],
                        "raw_text":    "",  # filled by 3c
                    }

                occ = {"source": fname, "page": pg}
                if occ not in id_inventory[bare_id]["occurrences"]:
                    id_inventory[bare_id]["occurrences"].append(occ)

        # Skip cross-ref pair generation for index_changelog pages and
        # pages with too many IDs (index tables)
        if ct == "index_changelog":
            continue
        if len(page_ids) > settings.MAX_IDS_PER_PAGE_FOR_XREF:
            log.debug(
                "Skipping cross-ref pairs on %s p%d (%d IDs — likely index page)",
                fname, pg, len(page_ids),
            )
            continue

        # Generate pairs for LLM validation
        for i, from_id in enumerate(page_ids):
            for to_id in page_ids[i + 1:]:
                candidate_pairs.append({
                    "from_id": from_id,
                    "to_id":   to_id,
                    "source":  fname,
                    "page":    pg,
                    "text":    text,   # page text for LLM context (removed after validation)
                })

    log.info(
        "  Harvested %d unique IDs, %d candidate cross-ref pairs",
        len(id_inventory), len(candidate_pairs),
    )

    # ── 3b: LLM cross-reference validation ───────────────────────────────────
    log.info("  Validating cross-refs with LLM ...")
    validated_refs = asyncio.run(_validate_all_xrefs(candidate_pairs))
    # Strip the page text from pairs (not needed downstream)
    for p in validated_refs:
        p.pop("text", None)
    log.info("  Validated: %d genuine REFERENCES (from %d candidates)",
             len(validated_refs), len(candidate_pairs))

    # ── 3c: LLM requirement body extraction ───────────────────────────────────
    log.info("  Extracting requirement bodies with LLM ...")
    asyncio.run(_extract_req_bodies(id_inventory, pages))
    filled = sum(1 for v in id_inventory.values() if _is_meaningful_req_body(v.get("raw_text", "")))
    log.info("  Requirement bodies filled: %d / %d", filled, len(id_inventory))

    # Breakdown by type
    type_counts: dict[str, int] = defaultdict(int)
    for info in id_inventory.values():
        type_counts[info["id_type"]] += 1
    for id_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        log.info("  %s: %d IDs", id_type, count)

    log.info(
        "Stage 3 complete: %d unique IDs, %d validated cross-refs",
        len(id_inventory), len(validated_refs),
    )

    return {
        "id_inventory": id_inventory,
        "cross_refs":   validated_refs,
        "doc_modules":  doc_modules,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3b — LLM cross-reference validation
# ══════════════════════════════════════════════════════════════════════════════

async def _validate_all_xrefs(candidates: list[dict]) -> list[dict]:
    """
    Group candidates by (source, page) and validate each page's pairs
    in one LLM call. Semaphore limits concurrent calls.
    """
    if not candidates:
        return []

    # Group by page
    by_page: dict[tuple, list[dict]] = defaultdict(list)
    for c in candidates:
        key = (c["source"], c["page"])
        by_page[key].append(c)

    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    tasks = [
        _validate_page_xrefs(page_key, page_candidates, semaphore)
        for page_key, page_candidates in by_page.items()
    ]
    results = await asyncio.gather(*tasks)
    return [ref for batch in results for ref in batch]


async def _validate_page_xrefs(
    page_key: tuple,
    candidates: list[dict],
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    if not candidates:
        return []

    page_text = candidates[0].get("text", "")
    validated: list[dict] = []

    # Validate every pair, but cap each individual LLM call to a small batch so
    # dense pages do not silently drop candidates after the first 30 pairs.
    batch_size = 30
    source, pg = page_key
    fname = Path(source).name

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        pairs_str = "\n".join(
            f"  {c['from_id']} ↔ {c['to_id']}"
            for c in batch
        )
        context = _build_xref_context(page_text, batch)
        user = (
            f"Page text excerpts around candidate IDs:\n{context}\n\n"
            f"Candidate pairs on this page:\n{pairs_str}"
        )

        result = await acall_llm_json(
            system=_XREF_SYSTEM,
            user=user,
            semaphore=semaphore,
        )

        if result and isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                from_id  = item.get("from", "")
                to_id    = item.get("to", "")
                rel_type = item.get("rel_type", "REFERENCES")
                if from_id and to_id:
                    validated.append({
                        "from_id":  from_id,
                        "to_id":    to_id,
                        "type":     rel_type,
                        "source":   fname,
                        "page":     pg,
                        "method":   "llm_validated",
                    })

    return validated


def _build_xref_context(page_text: str, candidates: list[dict], window: int = 450) -> str:
    """
    Build compact text excerpts around every ID in the candidate batch.

    Using only the first N chars of a page misses references that appear lower
    on dense AUTOSAR pages. Excerpts keep the prompt bounded while making sure
    each candidate ID has local context.
    """
    spans: list[tuple[int, int]] = []
    for c in candidates:
        for bare_id in (c.get("from_id", ""), c.get("to_id", "")):
            if not bare_id:
                continue
            for needle in (f"[{bare_id}]", bare_id):
                idx = page_text.find(needle)
                if idx >= 0:
                    spans.append((max(0, idx - window), min(len(page_text), idx + len(needle) + window)))
                    break

    if not spans:
        return page_text[:2500]

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1] + 80:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    excerpts: list[str] = []
    budget = 3500
    used = 0
    for start, end in merged:
        snippet = re.sub(r"\s+", " ", page_text[start:end]).strip()
        if not snippet:
            continue
        if used + len(snippet) > budget:
            remaining = budget - used
            if remaining <= 200:
                break
            snippet = snippet[:remaining]
        excerpts.append(snippet)
        used += len(snippet)
    return "\n...\n".join(excerpts)


# ══════════════════════════════════════════════════════════════════════════════
# 3c — LLM requirement body extraction
# ══════════════════════════════════════════════════════════════════════════════

async def _extract_req_bodies(
    id_inventory: dict[str, dict],
    pages: list[dict],
) -> None:
    """
    For each requirement ID, find the page where it first appears and
    ask the LLM to extract clean normative text.
    """
    # Build page text lookup: (filename, page_1idx) → text
    page_text_map: dict[tuple, str] = {}
    for p in pages:
        key = (p["filename"], p["page_1idx"])
        page_text_map[key] = p["markdown"]

    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)

    async def _extract_one(bare_id: str, info: dict) -> None:
        if not info["occurrences"]:
            return
        first_occ = info["occurrences"][0]
        key       = (first_occ["source"], first_occ["page"])
        page_text = page_text_map.get(key, "")
        if not page_text:
            return

        body = await acall_llm_text(
            system=_REQ_BODY_SYSTEM,
            user=f"Requirement ID: [{bare_id}]\n\nPage text:\n{page_text[:2000]}",
            semaphore=semaphore,
        )
        cleaned_body = _clean_req_body(body)
        if _is_meaningful_req_body(cleaned_body):
            info["raw_text"] = cleaned_body[:500]

    tasks = [
        _extract_one(bare_id, info)
        for bare_id, info in id_inventory.items()
    ]
    await asyncio.gather(*tasks)


def _clean_req_body(body: str | None) -> str:
    """Normalize empty-string artifacts and light markdown noise from LLM output."""
    if body is None:
        return ""
    text = str(body).strip()
    if text.lower() in {"", '""', "''", "empty string", "null", "none", "n/a"}:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _is_meaningful_req_body(body: str | None) -> bool:
    text = _clean_req_body(body)
    return bool(text and text.lower() not in {"empty", "not found"})
