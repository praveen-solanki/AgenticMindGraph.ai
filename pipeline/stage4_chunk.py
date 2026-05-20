"""
pipeline/stage4_chunk.py
========================
Stage 4: AUTOSAR-aware chunking + LLM chunk enrichment.

Structural splitting is unchanged (MarkdownHeaderTextSplitter + size cap).
New: LLM enrichment pass after splitting adds to every chunk:
    cleaned_text     — Markdown noise stripped (for clean embedding)
    summary          — one-sentence description (searchable property)
    chunk_type       — granular type classification
    section_context  — inferred section/topic (fills H1/H2/H3=None gaps)
    normative        — bool, contains SHALL/SHOULD/MAY requirements
    embed_text       — alias for cleaned_text, used by Stage 7 for embedding

Also fixed:
  - _REQ_ID_RE expanded to cover RS_, TR_, PRS_, EXP_ prefixes (was SWS only)
  - Heading metadata keys corrected to match LangChain output format
  - glossary/bibliography pages now pass through to chunking
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from utils.logger import get_logger
from utils.llm_client import acall_llm_json
from config import settings

log = get_logger("stage4")

# ── Regex helpers ─────────────────────────────────────────────────────────────
_TABLE_START_RE = re.compile(r"^\|.+\|")

# Expanded to cover all ID types from settings
_REQ_ID_RE = re.compile(
    r"\[(?:"
    + "|".join(
        p.replace(r"\[", "").replace(r"\]", "").split("_")[0]
        for p in settings.REQUIREMENT_ID_PATTERNS[:20]
        if "_" in p
    )
    + r")[A-Za-z_]*_?\d{4,5}\]"
)

# Simpler fallback that catches everything
_REQ_ID_FALLBACK_RE = re.compile(r"\[[A-Z][A-Za-z0-9]+_[A-Za-z0-9]+_\d{4,5}\]")
_REQ_ID_PATTERNS: list[re.Pattern] = [
    re.compile(p) for p in settings.REQUIREMENT_ID_PATTERNS
]
_BARE_REQ_ID_RE = re.compile(r"\[([A-Za-z0-9_]+_\d{4,5})\]")

_CONSTR_RE = re.compile(r"\[constr_\d{4}\]")

_ECUC_HEADER_RE = re.compile(
    r"(SWS Item|ECUC Param|Parameter Name|Multiplicity|Type|Range|Description)",
    re.IGNORECASE,
)

# Heading key variants LangChain may produce
_H_KEYS = [
    ("H1", ["Header 1", "Header1", "h1", "H1"]),
    ("H2", ["Header 2", "Header2", "h2", "H2"]),
    ("H3", ["Header 3", "Header3", "h3", "H3"]),
    ("H4", ["Header 4", "Header4", "h4", "H4"]),
    ("H5", ["Header 5", "Header5", "h5", "H5"]),
]

# _ENRICH_SYSTEM = """You are processing an AUTOSAR specification text chunk.
#     Return ONLY a JSON object with these fields:

#     {
#     "cleaned_text": "<text with table markdown stripped, <br> replaced by space, **bold** markers removed, but ALL requirement IDs like [SWS_X_00123] preserved exactly>",
#     "summary": "<one sentence: what is this chunk about>",
#     "chunk_type": "requirement" | "constraint" | "explanation" | "api_definition" | "configuration" | "example" | "tracing_table" | "general",
#     "section_context": "<inferred section topic e.g. 'Buffer Handling in Transformer Module' — infer from content if headings are missing>",
#     "normative": true | false
#     }

#     Rules:
#     - cleaned_text: remove |---|---| rows, **bold**, <br>, but keep all IDs and requirement text
#     - summary: max 20 words, precise, use AUTOSAR terminology
#     - normative: true only if text contains SHALL, SHOULD, or MAY as normative keywords
#     - Return valid JSON only, no markdown fences"""

_ENRICH_SYSTEM = """You are a high-precision AUTOSAR specification chunk enrichment engine for a knowledge graph and retrieval pipeline.

    You are given a chunk of AUTOSAR specification text.

    Your task is to analyze the chunk and return a STRICT JSON object with the following exact schema:

    {
    "cleaned_text": "<cleaned specification text>",
    "summary": "<single concise sentence>",
    "chunk_type": "requirement" | "constraint" | "explanation" | "api_definition" | "configuration" | "example" | "tracing_table" | "general",
    "section_context": "<high-level inferred section topic>",
    "normative": true | false
    }

    STRICT OUTPUT RULES:
    - Return ONLY valid JSON
    - No markdown
    - No explanations
    - No comments
    - No extra keys
    - No trailing text
    - All fields are mandatory
    - JSON must be syntactically valid

    FIELD DEFINITIONS:

    1. cleaned_text
    Return the cleaned version of the chunk.

    CLEANING RULES:
    - Remove markdown separator rows such as:
    - |---|---|
    - |:---|:---|
    - Remove markdown bold markers:
    - **text**
    - Replace:
    - <br>
    - <br/>
    - <br />
    with spaces
    - Remove obvious formatting artifacts and extraction noise

    IMPORTANT PRESERVATION RULES:
    - Preserve ALL AUTOSAR identifiers exactly
    - Preserve ALL requirement IDs exactly, including:
    - [SWS_X_00123]
    - [RS_X_00201]
    - [ECUC_X_12345]
    - Preserve normative wording:
    - SHALL
    - SHOULD
    - MAY
    - MUST
    - Preserve technical terminology
    - Preserve API names
    - Preserve configuration parameter names
    - Preserve XML/schema fragments
    - Preserve table data rows containing real content

    DO NOT:
    - paraphrase
    - summarize
    - normalize terminology
    - alter capitalization of technical terms
    - remove meaningful technical content

    2. summary
    Generate ONE concise sentence describing the chunk.

    SUMMARY RULES:
    - Maximum 20 words
    - Precise and information-dense
    - Use AUTOSAR terminology where appropriate
    - Describe the main technical purpose of the chunk
    - Avoid generic phrases such as:
    - "This chunk describes..."
    - "This section explains..."

    GOOD EXAMPLES:
    - "Defines Transformer buffer handling requirements for segmented transmission."
    - "Specifies DEM event reporting API behavior and timeout constraints."

    3. chunk_type
    Classify the chunk into EXACTLY ONE type:

    requirement:
    - primary content contains explicit requirements
    - SHALL/SHOULD/MAY statements dominate

    constraint:
    - restrictions, limitations, conditions, forbidden states, validation rules

    explanation:
    - descriptive or architectural explanatory text
    - conceptual behavior descriptions

    api_definition:
    - API signatures
    - function descriptions
    - interface contracts
    - service operation definitions

    configuration:
    - ECUC parameters
    - configuration containers
    - configurable behavior
    - parameter metadata

    example:
    - example flows
    - sample usage
    - illustrative scenarios
    - pseudo examples

    tracing_table:
    - requirement traceability mappings
    - reference matrices
    - derived/satisfied mappings
    - compliance tables

    general:
    - fallback category for mixed or uncategorizable content

    CLASSIFICATION RULE:
    Choose the DOMINANT semantic purpose of the chunk.

    4. section_context
    Infer the broader technical section/topic represented by the chunk.

    RULES:
    - Infer context even if headings are missing
    - Use concise AUTOSAR-oriented phrasing
    - Prefer subsystem/module-level meaning

    GOOD EXAMPLES:
    - "Buffer Handling in Transformer Module"
    - "DEM Event Debouncing"
    - "CAN Interface Transmission Processing"
    - "ECUC Configuration Parameters"

    DO NOT:
    - copy raw headings blindly if noisy
    - generate vague contexts like:
    - "General Information"
    - "Technical Description"

    5. normative
    Return:
    - true  -> if the chunk contains normative requirement language
    - false -> otherwise

    Set normative=true ONLY if the chunk contains explicit normative keywords such as:
    - SHALL
    - SHOULD
    - MAY
    - MUST
    - REQUIRED

    Ignore non-normative incidental wording.

    IMPORTANT BEHAVIOR RULES:
    - Prioritize semantic preservation over aggressive cleaning
    - When uncertain, preserve content instead of deleting it
    - Do not hallucinate missing information
    - Do not infer unsupported technical details
    - Keep outputs deterministic and compact
    - Preserve downstream KG compatibility

    Return ONLY the JSON object.
    """

def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _extract_heading(meta: dict, level: str) -> str | None:
    keys = dict(_H_KEYS).get(level, [level])
    for k in keys:
        v = meta.get(k)
        if v:
            return str(v)
    return None


def run(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    log.info("Stage 4: chunking %d pages", len(pages))

    all_chunks:    list[dict] = []
    config_params: list[dict] = []
    doc_chunk_order: dict[str, list[str]] = {}

    # Include glossary and bibliography pages now (they were silently discarded before)
    content_pages = [
        p for p in pages
        if p.get("content_type") in (
            "content", "requirement_chunk", "glossary",
            "bibliography", "index_changelog", None
        )
    ]

    for page in content_pages:
        filename = page["filename"]
        page_chunks, page_params = _chunk_page(page)
        config_params.extend(page_params)

        if filename not in doc_chunk_order:
            doc_chunk_order[filename] = []

        for chunk in page_chunks:
            doc_chunk_order[filename].append(chunk["chunk_id"])
            all_chunks.append(chunk)

    # Quality gate MUST run before sequential links are assigned.
    # Dropped chunks must not appear as prev/next references in surviving chunks.
    before = len(all_chunks)
    all_chunks = _quality_gate(all_chunks)
    dropped = before - len(all_chunks)

    # Rebuild doc_chunk_order to contain only surviving chunk IDs, then link.
    surviving_ids: set[str] = {c["chunk_id"] for c in all_chunks}
    filtered_order: dict = {
        fname: [cid for cid in ids if cid in surviving_ids]
        for fname, ids in doc_chunk_order.items()
    }
    _apply_sequential_links(all_chunks, filtered_order)

    log.info(
        "Chunking done: %d chunks (%d dropped), %d config params",
        len(all_chunks), dropped, len(config_params),
    )

    # ── LLM enrichment ────────────────────────────────────────────────────────
    log.info("LLM enrichment on %d chunks ...", len(all_chunks))
    all_chunks = asyncio.run(_enrich_all_chunks(all_chunks))

    log.info("Stage 4 complete: %d enriched chunks", len(all_chunks))
    _log_chunk_type_breakdown(all_chunks)
    return all_chunks, config_params


# ══════════════════════════════════════════════════════════════════════════════
# Per-page chunking
# ══════════════════════════════════════════════════════════════════════════════

def _chunk_page(page: dict) -> tuple[list[dict], list[dict]]:
    from langchain_text_splitters import MarkdownHeaderTextSplitter
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    text     = page["markdown"]
    filename = page["filename"]
    source   = page["source"]
    pg       = page["page_1idx"]
    page_ct  = page.get("content_type", "content")

    text, config_params = _extract_config_param_tables(text, page)

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=settings.SPLIT_HEADERS,
        strip_headers=False,
    )
    sections = splitter.split_text(text)

    char_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_MAX_TOKENS * 4,
        chunk_overlap=settings.CHUNK_OVERLAP_TOKENS * 4,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    raw_chunks: list[tuple[str, dict]] = []

    for section in sections:
        sec_text = section.page_content
        sec_meta = section.metadata

        if _approx_tokens(sec_text) <= settings.CHUNK_TABLE_MAX_TOKENS:
            raw_chunks.append((sec_text, sec_meta))
        else:
            for sub in _split_respecting_tables(sec_text, char_splitter):
                raw_chunks.append((sub, sec_meta))

    chunks: list[dict] = []
    for idx, (chunk_text, meta) in enumerate(raw_chunks):
        chunk_text = chunk_text.strip()
        if not chunk_text:
            continue

        req_ids = _extract_req_ids(chunk_text)
        ctype   = _classify_chunk(chunk_text, req_ids, page_ct)

        # Always include a short hash of the full source path to guarantee
        # global uniqueness across same-named PDFs in different directories
        # (Bug 2 fix). Stem capped at 50 chars to keep total ID length reasonable.
        import hashlib as _hashlib
        _stem       = Path(filename).stem
        safe_fname  = re.sub(r"[^A-Za-z0-9]", "_", _stem)[:50]
        _path_hash  = _hashlib.md5(source.encode()).hexdigest()[:8]
        chunk_id    = f"{safe_fname}_{_path_hash}_p{pg:04d}_c{idx:03d}"

        chunks.append({
            "chunk_id":        chunk_id,
            "text":            chunk_text,
            "cleaned_text":    chunk_text,  # overwritten by LLM enrichment
            "embed_text":      chunk_text,  # overwritten by LLM enrichment
            "summary":         "",          # filled by LLM enrichment
            "section_context": "",          # filled by LLM enrichment
            "normative":       False,       # filled by LLM enrichment
            "source":          source,
            "filename":        filename,
            "page":            pg,
            "H1":              _extract_heading(meta, "H1"),
            "H2":              _extract_heading(meta, "H2"),
            "H3":              _extract_heading(meta, "H3"),
            "H4":              _extract_heading(meta, "H4"),
            "H5":              _extract_heading(meta, "H5"),
            "token_count":     _approx_tokens(chunk_text),
            "chunk_index":     idx,
            "content_type":    ctype,
            "page_content_type": page_ct,
            "prev_chunk_id":   None,
            "next_chunk_id":   None,
            "req_ids_present": req_ids,
        })

    return chunks, config_params


# ══════════════════════════════════════════════════════════════════════════════
# LLM chunk enrichment
# ══════════════════════════════════════════════════════════════════════════════

async def _enrich_all_chunks(chunks: list[dict]) -> list[dict]:
    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    tasks = [_enrich_chunk(c, semaphore) for c in chunks]
    return list(await asyncio.gather(*tasks))


async def _enrich_chunk(chunk: dict, semaphore: asyncio.Semaphore) -> dict:
    # Include heading context in the prompt to help section_context inference
    heading_ctx = " > ".join(filter(None, [
        chunk.get("H1"), chunk.get("H2"), chunk.get("H3")
    ]))
    user = (
        f"Document: {chunk['filename']}\n"
        f"Heading path: {heading_ctx or 'unknown'}\n\n"
        f"Chunk text:\n{chunk['text'][:1200]}"
    )

    result = await acall_llm_json(
        system=_ENRICH_SYSTEM,
        user=user,
        semaphore=semaphore,
    )

    if result and isinstance(result, dict):
        cleaned = result.get("cleaned_text", "")
        if cleaned and len(cleaned) > 30:
            chunk["cleaned_text"] = cleaned
            chunk["embed_text"]   = cleaned

        summary = result.get("summary", "")
        if summary:
            chunk["summary"] = summary[:200]

        ctx = result.get("section_context", "")
        if ctx:
            chunk["section_context"] = ctx[:200]
            # Backfill missing headings with LLM-inferred context
            if not chunk["H1"] and not chunk["H2"]:
                chunk["H1"] = ctx

        chunk["normative"] = bool(result.get("normative", False))

        llm_type = result.get("chunk_type", "")
        if llm_type:
            # Preserve structural page classes from Stage 1/2. Bibliography,
            # changelog, and glossary chunks should not be reclassified as
            # ordinary semantic content just because the enrichment model found
            # explanatory prose inside them.
            if chunk.get("page_content_type") not in {
                "bibliography", "index_changelog", "glossary", "abbreviations"
            }:
                chunk["content_type"] = llm_type

    return chunk


# ══════════════════════════════════════════════════════════════════════════════
# Table-safe splitting, ECUC extraction, helpers
# ══════════════════════════════════════════════════════════════════════════════

def _split_respecting_tables(text: str, splitter) -> list[str]:
    segments: list[tuple[str, bool]] = []
    lines  = text.splitlines(keepends=True)
    buf:   list[str] = []
    in_table = False

    for line in lines:
        if _TABLE_START_RE.match(line.rstrip()):
            if not in_table:
                if buf:
                    segments.append(("".join(buf), False))
                    buf = []
                in_table = True
            buf.append(line)
        else:
            if in_table:
                segments.append(("".join(buf), True))
                buf = []
                in_table = False
            buf.append(line)

    if buf:
        segments.append(("".join(buf), in_table))

    result: list[str] = []
    for seg_text, is_table in segments:
        if is_table:
            result.append(seg_text)
        else:
            result.extend(splitter.split_text(seg_text))

    return [s for s in result if s.strip()]


def _extract_config_param_tables(text: str, page: dict) -> tuple[str, list[dict]]:
    config_params: list[dict] = []
    lines     = text.splitlines(keepends=True)
    out_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _TABLE_START_RE.match(line) and _ECUC_HEADER_RE.search(line):
            table_lines: list[str] = []
            while i < len(lines) and (
                _TABLE_START_RE.match(lines[i]) or lines[i].strip() == ""
            ):
                table_lines.append(lines[i])
                i += 1
            config_params.extend(_parse_ecuc_table(table_lines, page))
        else:
            out_lines.append(line)
            i += 1
    return "".join(out_lines), config_params


def _parse_ecuc_table(table_lines: list[str], page: dict) -> list[dict]:
    params: list[dict] = []
    rows:   list[list[str]] = []
    for line in table_lines:
        line = line.strip()
        if not line or set(line) <= set("|-: "):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)
    if len(rows) < 2:
        return params
    headers = [h.lower() for h in rows[0]]

    def _col(row, *names):
        for name in names:
            for i, h in enumerate(headers):
                if name in h and i < len(row):
                    return row[i]
        return ""

    m = re.search(r"AUTOSAR_(?:SWS|SRS)_([A-Za-z0-9]+)", page.get("filename", ""))
    module_name = m.group(1) if m else "Unknown"

    for row in rows[1:]:
        if len(row) < 2:
            continue
        name = _col(row, "name", "parameter", "sws item")
        if not name:
            continue
        params.append({
            "param_id":     f"ECUC_{module_name}_{name}",
            "name":         name,
            "type":         _col(row, "type", "category"),
            "multiplicity": _col(row, "multiplicity", "mult"),
            "range":        _col(row, "range", "value"),
            "description":  _col(row, "description", "desc"),
            "module":       module_name,
            "source":       page["filename"],
            "page":         page["page_1idx"],
        })
    return params


def _extract_req_ids(text: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    for pattern in _REQ_ID_PATTERNS:
        for match in pattern.finditer(text):
            full_id = match.group(0)
            bare = _BARE_REQ_ID_RE.match(full_id)
            if not bare:
                continue
            bare_id = bare.group(1)
            if bare_id not in seen:
                seen.add(bare_id)
                ids.append(bare_id)

    # Keep the generic fallback for uncommon AUTOSAR-style prefixes that are
    # not yet listed in settings.REQUIREMENT_ID_PATTERNS.
    for match in _REQ_ID_FALLBACK_RE.finditer(text):
        bare_id = match.group(0)[1:-1]
        if bare_id not in seen:
            seen.add(bare_id)
            ids.append(bare_id)

    return ids


def _classify_chunk(text: str, req_ids: list[str], page_ct: str) -> str:
    if page_ct in ("glossary",):
        return "glossary"
    if page_ct in ("bibliography",):
        return "bibliography"
    if req_ids:
        return "requirement_chunk"
    if _CONSTR_RE.search(text):
        return "constraint_chunk"
    lower = text.lower()
    if any(kw in lower for kw in ("shall", "should", "may", "must")):
        return "requirement_chunk"
    if any(kw in lower for kw in ("figure", "example", "note:", "rationale", "background")):
        return "explanatory_chunk"
    return "general_chunk"


def _apply_sequential_links(chunks: list[dict], doc_chunk_order: dict) -> None:
    id_to_chunk = {c["chunk_id"]: c for c in chunks}
    for filename, ordered_ids in doc_chunk_order.items():
        for i, cid in enumerate(ordered_ids):
            chunk = id_to_chunk.get(cid)
            if chunk is None:
                continue
            chunk["prev_chunk_id"] = ordered_ids[i - 1] if i > 0 else None
            chunk["next_chunk_id"] = ordered_ids[i + 1] if i < len(ordered_ids) - 1 else None


def _quality_gate(chunks: list[dict]) -> list[dict]:
    kept: list[dict] = []
    for c in chunks:
        text = c["text"]
        if c["token_count"] < settings.CHUNK_MIN_TOKENS:
            continue
        words = re.findall(r"\b\w+\b", text.lower())
        if words and len(set(words)) / len(words) < settings.MIN_UNIQUE_WORD_RATIO:
            continue
        non_heading = "\n".join(
            l for l in text.splitlines() if not l.startswith("#")
        ).strip()
        if len(non_heading) < 30:
            continue
        kept.append(c)
    return kept


def _log_chunk_type_breakdown(chunks: list[dict]) -> None:
    from collections import Counter
    counts = Counter(c["content_type"] for c in chunks)
    for ctype, n in counts.most_common():
        log.info("  %-25s %d chunks", ctype, n)