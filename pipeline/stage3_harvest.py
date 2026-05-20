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

# # Cross-ref validation prompt
# _XREF_SYSTEM = """You are an AUTOSAR specification expert.
# Given a page of AUTOSAR specification text and a list of requirement ID pairs
# that co-occur on the page, determine which pairs represent GENUINE reference
# relationships — i.e. one requirement explicitly references, satisfies,
# refines, or is derived from the other in the text.

# Return ONLY a JSON array of validated pairs:
# [{"from": "ID_A", "to": "ID_B", "rel_type": "REFERENCES"|"DERIVED_FROM"|"TRACES_TO"|"REFINES"}, ...]

# Return an empty array [] if no genuine relationships exist.
# Do NOT include pairs that just happen to appear on the same page without
# an explicit textual relationship."""

_XREF_SYSTEM = """You are a high-precision AUTOSAR requirement relationship extraction engine.

    You are given:
    1. A page of AUTOSAR specification text
    2. A list of requirement ID pairs that co-occur on the page

    Your task is to identify ONLY genuine semantic requirement relationships that are EXPLICITLY stated or clearly expressed in the text.

    You must determine whether one requirement:
    - references
    - derives from
    - traces to
    - refines
    another requirement.

    Return ONLY a JSON array in this exact format:
    [
    {
        "from": "ID_A",
        "to": "ID_B",
        "rel_type": "REFERENCES" | "DERIVED_FROM" | "TRACES_TO" | "REFINES"
    }
    ]

    Return:
    []

    if no valid explicit relationships exist.

    STRICT OUTPUT RULES:
    - Output ONLY valid JSON
    - No markdown
    - No explanations
    - No comments
    - No extra keys
    - No trailing text

    IMPORTANT EXTRACTION RULES:

    1. ONLY extract EXPLICIT relationships
    The relationship must be clearly supported by textual evidence on the page.

    Valid signals include:
    - "derived from"
    - "refines"
    - "satisfies"
    - "references"
    - "traces to"
    - "based on"
    - "according to"
    - "fulfills"
    - explicit dependency wording
    - explicit traceability wording
    - explicit requirement linkage wording

    2. DO NOT infer relationships
    Do NOT create relationships based on:
    - co-occurrence on the same page
    - topic similarity
    - nearby placement
    - numbering similarity
    - shared section membership
    - assumptions
    - AUTOSAR naming conventions alone

    3. RELATION TYPE DEFINITIONS

    REFERENCES:
    Use when one requirement explicitly cites, points to, depends on, or refers to another.

    DERIVED_FROM:
    Use when the text explicitly states that a requirement originates from, derives from, or is based on another requirement.

    TRACES_TO:
    Use for explicit traceability relationships, upstream/downstream requirement tracing, or allocation mappings.

    REFINES:
    Use when one requirement narrows, specializes, extends, or concretizes another requirement.

    4. DIRECTIONALITY RULES

    Direction matters.

    If:
    Requirement A derives from Requirement B

    then return:
    {
    "from": "A",
    "to": "B",
    "rel_type": "DERIVED_FROM"
    }

    The "from" requirement is the dependent/specialized/current requirement.
    The "to" requirement is the referenced/source/base requirement.

    5. EVIDENCE REQUIREMENT
    Only include a relationship if the page contains sufficiently clear textual evidence.

    If uncertain, omit the pair.

    6. DUPLICATES
    Do not emit duplicate relationships.

    7. VALIDATION PRIORITY
    Prioritize precision over recall.
    False positives are worse than missed relationships.

    IMPORTANT:
    A pair appearing on the same page DOES NOT imply a relationship.

    Return ONLY the JSON array.
    """

# Requirement body extraction prompt
# _REQ_BODY_SYSTEM = """You are extracting AUTOSAR requirement text.
#     Given a page of AUTOSAR specification text and a requirement ID,
#     return ONLY the normative text of that requirement — the SHALL/SHOULD/MAY
#     statement — stripped of table formatting, cross-reference noise, and
#     footnote markers.

#     Return ONLY the clean requirement text as a plain string.
#     If the requirement body cannot be found, return an empty string."""

_REQ_BODY_SYSTEM = """You are a high-precision AUTOSAR requirement body extraction engine.

    You are given:
    1. A page of AUTOSAR specification text
    2. A target requirement ID

    Your task is to extract ONLY the normative body text belonging to that specific requirement.

    The extracted text should contain the actual requirement statement, including normative language such as:
    - SHALL
    - SHOULD
    - MAY
    - MUST
    - REQUIRED
    - OPTIONAL

    Return ONLY the clean requirement text as a plain string.

    If the requirement body cannot be reliably identified, return:
    ""

    STRICT OUTPUT RULES:
    - Output ONLY the extracted requirement text
    - No markdown
    - No explanations
    - No comments
    - No requirement IDs unless part of the body
    - No surrounding metadata
    - No trailing notes

    EXTRACTION RULES:

    1. EXTRACT ONLY THE TARGET REQUIREMENT
    Do not include:
    - neighboring requirements
    - unrelated paragraphs
    - adjacent table rows
    - section introductions
    - explanatory notes unrelated to the requirement

    2. PRESERVE NORMATIVE CONTENT
    Preserve:
    - SHALL/SHOULD/MAY wording
    - constraints
    - behavioral statements
    - parameter requirements
    - conditional logic
    - technical terminology
    - enumerations belonging to the requirement

    3. REMOVE FORMATTING NOISE
    Remove:
    - table borders
    - markdown separators
    - page artifacts
    - footnote markers
    - repeated headers/footers
    - irrelevant cross-reference clutter

    4. DO NOT REWRITE
    Do NOT:
    - summarize
    - paraphrase
    - simplify
    - normalize wording
    - change capitalization
    - alter technical meaning

    5. TABLE EXTRACTION RULES
    If the requirement body appears inside a table:
    - extract only the meaningful textual content
    - preserve logical reading order
    - exclude separator artifacts

    6. MULTI-LINE REQUIREMENTS
    If the requirement spans multiple lines or paragraphs:
    - combine them into a coherent plain-text requirement body
    - preserve sentence continuity

    7. STOP CONDITIONS
    Stop extraction when:
    - the next requirement begins
    - a new unrelated subsection starts
    - another requirement ID begins
    - unrelated explanatory content starts dominating

    8. PARTIAL EXTRACTION
    If only part of the requirement is visible on the page:
    - return only the visible requirement body text
    - do not hallucinate missing text

    9. CONFIDENCE RULE
    If the target requirement body cannot be reliably isolated, return:
    ""

    IMPORTANT:
    Preserve the exact technical meaning of the requirement.

    Return ONLY the clean requirement body string.
    """

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

                occ = {"source": page["source"], "page": pg}  # 7c fix: full path not bare fname
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
    # Build page text lookup: (source_full_path, page_1idx) → text
    # Bug 7c fix: key on full source path, not bare filename, so same-named
    # PDFs from different directories don't silently overwrite each other.
    page_text_map: dict[tuple, str] = {}
    # Fix 1: also track content_type per (source, page) so index/changelog
    # pages are tried last — they contain the ID but not the normative body.
    page_ct_map: dict[tuple, str] = {}
    for p in pages:
        key = (p["source"], p["page_1idx"])   # 7c: full path, not p["filename"]
        page_text_map[key] = p["markdown"]
        page_ct_map[key]   = p.get("content_type", "content")

    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)

    async def _extract_one(bare_id: str, info: dict) -> None:
        if not info["occurrences"]:
            return

        # Fix 1: sort occurrences so normative content pages come before
        # index_changelog pages.  This prevents the ID's first hit in a
        # change-history table from being the one sent to the LLM.
        _NON_NORMATIVE = {"index_changelog", "bibliography", "abbreviations"}
        occs_sorted = sorted(
            info["occurrences"],
            key=lambda o: 1 if page_ct_map.get((o["source"], o["page"]), "content") in _NON_NORMATIVE else 0,
        )

        for occ in occs_sorted:          # 7b: try every occurrence, not just first
            key       = (occ["source"], occ["page"])
            page_text = page_text_map.get(key, "")
            if not page_text:
                continue

            # 7a: position-aware windowing instead of page_text[:2000]
            context = _extract_id_context(page_text, bare_id, window=1000, budget=4000)

            body = await acall_llm_text(
                system=_REQ_BODY_SYSTEM,
                user=f"Requirement ID: [{bare_id}]\n\nPage text:\n{context}",
                semaphore=semaphore,
            )
            cleaned_body = _clean_req_body(body)
            if _is_meaningful_req_body(cleaned_body):
                info["raw_text"] = cleaned_body[:500]
                return   # found a good body — stop trying other occurrences

    tasks = [
        _extract_one(bare_id, info)
        for bare_id, info in id_inventory.items()
    ]
    await asyncio.gather(*tasks)


def _extract_id_context(page_text: str, bare_id: str, window: int = 1000, budget: int = 4000) -> str:
    """
    Extract a windowed excerpt of page_text centred on bare_id's position.
    Falls back to page_text[:budget] if the ID is not found.

    Bug 7a fix: prevents the hard [:2000] truncation from cutting off
    requirements that appear lower on dense AUTOSAR pages (6,000–15,000 chars).
    """
    needle = f"[{bare_id}]"
    idx = page_text.find(needle)
    if idx < 0:
        # ID not found as bracketed form — fall back to top of page
        return page_text[:budget]
    start = max(0, idx - window)
    end   = min(len(page_text), idx + len(needle) + window)
    excerpt = page_text[start:end]
    # If we have budget left and didn't start at 0, prepend a little leading context
    if start > 0 and len(excerpt) < budget:
        extra = page_text[:min(start, budget - len(excerpt))]
        excerpt = extra + "\n...\n" + excerpt
    return excerpt[:budget]


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
