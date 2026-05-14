# """
# pipeline/stage5_extract_entities.py
# ====================================
# Stage 5: Dual-track entity & relation extraction.

# Track A — Rule-based (fast, perfect, free)
#     Builds Requirement nodes, ConfigParameter nodes, DocumentRef nodes,
#     Module nodes, and REFERENCES edges directly from the harvested ID
#     inventory and bibliography sections. Zero LLM calls.

# Track B — LLM-based (semantic, via vLLM)
#     Sends content/requirement/explanatory chunks to LLMGraphTransformer.
#     Extracts Concept nodes, semantic relationships (DEPENDS_ON, IMPLEMENTS,
#     ALLOCATED_TO, CONTRADICTS, SPECIALIZES, DEFINED_BY, etc.).
#     Runs with asyncio + semaphore to respect --max-num-seqs 16.

# Output schema:
#     {
#         "nodes": [
#             {
#                 "node_id":    "req_SWS_ComM_00123",
#                 "label":      "Requirement",
#                 "properties": {
#                     "id":         "SWS_ComM_00123",
#                     "full_id":    "[SWS_ComM_00123]",
#                     "module":     "ComM",
#                     "id_type":    "SWS",
#                     "raw_text":   "...",     # filled if found in a chunk
#                     ...
#                 }
#             },
#             ...
#         ],
#         "relationships": [
#             {
#                 "from_id":  "req_SWS_ComM_00123",
#                 "to_id":    "req_SWS_Can_00456",
#                 "type":     "REFERENCES",
#                 "properties": {"source": "AUTOSAR_SWS_ComM.pdf", "page": 47}
#             },
#             ...
#         ]
#     }
# """

# from __future__ import annotations

# import asyncio
# import re
# import warnings
# from pathlib import Path
# from typing import Any

# from utils.logger import get_logger
# from config import settings

# # Suppress Pydantic serializer warnings from LLMGraphTransformer internals
# # These are cosmetic warnings about DynamicGraph serialization — not errors.
# warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
# warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

# log = get_logger("stage5")

# # ── Requirement text extraction: find the sentence(s) immediately following an ID ─
# _REQ_BODY_RE = re.compile(
#     r"\[(?P<id>[A-Za-z_]+_\d{4,5})\]\s*(?P<body>[^\[]{10,400})",
#     re.DOTALL,
# )


# def run(
#     chunks:       list[dict],
#     harvest:      dict,
#     config_params: list[dict],
# ) -> dict:
#     """
#     Extract all nodes and relationships.

#     Args:
#         chunks:        from Stage 4
#         harvest:       from Stage 3 (id_inventory, cross_refs, doc_modules)
#         config_params: from Stage 4 (structured ECUC param records)

#     Returns:
#         {"nodes": [...], "relationships": [...]}
#     """
#     log.info("Stage 5: entity & relation extraction")

#     nodes:         list[dict] = []
#     relationships: list[dict] = []

#     # ── Track A: Rule-based ───────────────────────────────────────────────────
#     log.info("  Track A: rule-based extraction ...")
#     a_nodes, a_rels = _track_a(harvest, config_params, chunks)
#     nodes.extend(a_nodes)
#     relationships.extend(a_rels)
#     log.info(
#         "  Track A done: %d nodes, %d relationships",
#         len(a_nodes), len(a_rels),
#     )

#     # ── Track B: LLM-based ───────────────────────────────────────────────────
#     log.info("  Track B: LLM-based extraction (%d chunks) ...", len(chunks))
#     b_nodes, b_rels = asyncio.run(_track_b(chunks))
#     nodes.extend(b_nodes)
#     relationships.extend(b_rels)
#     log.info(
#         "  Track B done: %d nodes, %d relationships",
#         len(b_nodes), len(b_rels),
#     )

#     # ── Deduplicate nodes by node_id ──────────────────────────────────────────
#     seen_ids: set[str] = set()
#     unique_nodes: list[dict] = []
#     for node in nodes:
#         nid = node["node_id"]
#         if nid not in seen_ids:
#             seen_ids.add(nid)
#             unique_nodes.append(node)

#     # ── Deduplicate relationships ─────────────────────────────────────────────
#     seen_rels: set[tuple] = set()
#     unique_rels: list[dict] = []
#     for rel in relationships:
#         key = (rel["from_id"], rel["to_id"], rel["type"])
#         if key not in seen_rels:
#             seen_rels.add(key)
#             unique_rels.append(rel)

#     log.info(
#         "Stage 5 complete: %d unique nodes, %d unique relationships",
#         len(unique_nodes), len(unique_rels),
#     )
#     return {"nodes": unique_nodes, "relationships": unique_rels}


# # ══════════════════════════════════════════════════════════════════════════════
# # TRACK A — Rule-based
# # ══════════════════════════════════════════════════════════════════════════════

# def _track_a(
#     harvest:       dict,
#     config_params: list[dict],
#     chunks:        list[dict],
# ) -> tuple[list[dict], list[dict]]:
#     nodes:         list[dict] = []
#     relationships: list[dict] = []

#     id_inventory: dict[str, dict] = harvest["id_inventory"]
#     cross_refs:   list[dict]      = harvest["cross_refs"]
#     doc_modules:  dict[str, str]  = harvest["doc_modules"]

#     # Build a lookup: bare_id → raw_text (first occurrence in chunks)
#     req_text_map: dict[str, str] = _build_req_text_map(chunks, id_inventory)

#     # 1. Requirement nodes
#     for bare_id, info in id_inventory.items():
#         node_id = f"req_{bare_id}"
#         nodes.append({
#             "node_id": node_id,
#             "label":   "Requirement",
#             "properties": {
#                 "id":          bare_id,
#                 "full_id":     info["full_id"],
#                 "module":      info["module"],
#                 "id_type":     info["id_type"],
#                 "raw_text":    req_text_map.get(bare_id, ""),
#                 "sources":     [o["source"] for o in info["occurrences"]],
#                 "pages":       [o["page"]   for o in info["occurrences"]],
#             },
#         })

#     # 2. Module nodes (from doc_modules + from requirement module fields)
#     modules_seen: set[str] = set()
#     for info in id_inventory.values():
#         mod = info["module"]
#         if mod and mod not in modules_seen:
#             modules_seen.add(mod)
#             nodes.append({
#                 "node_id": f"module_{mod}",
#                 "label":   "Module",
#                 "properties": {
#                     "name":      mod,
#                     "full_name": _expand_module_name(mod),
#                 },
#             })

#     # 3. Module → HAS_REQUIREMENT edges
#     for bare_id, info in id_inventory.items():
#         mod = info["module"]
#         if mod:
#             relationships.append({
#                 "from_id":    f"module_{mod}",
#                 "to_id":      f"req_{bare_id}",
#                 "type":       "HAS_REQUIREMENT",
#                 "properties": {},
#             })

#     # 4. REFERENCES edges from co-occurrence cross-refs
#     #    Use only same-module cross-refs as high-confidence; cross-module
#     #    cross-refs are kept but marked lower confidence
#     for xref in cross_refs:
#         from_id = xref["from_id"]
#         to_id   = xref["to_id"]
#         if from_id not in id_inventory or to_id not in id_inventory:
#             continue
#         from_mod = id_inventory[from_id]["module"]
#         to_mod   = id_inventory[to_id]["module"]
#         relationships.append({
#             "from_id":    f"req_{from_id}",
#             "to_id":      f"req_{to_id}",
#             "type":       "REFERENCES",
#             "properties": {
#                 "source":          xref["source"],
#                 "page":            xref["page"],
#                 "cross_module":    from_mod != to_mod,
#                 "method":          "rule_based_cooccurrence",
#             },
#         })

#     # 5. ConfigParameter nodes + HAS_PARAMETER edges
#     for param in config_params:
#         pid = param["param_id"]
#         nodes.append({
#             "node_id": f"param_{pid}",
#             "label":   "ConfigParameter",
#             "properties": {
#                 "id":           pid,
#                 "name":         param["name"],
#                 "type":         param["type"],
#                 "multiplicity": param["multiplicity"],
#                 "range":        param["range"],
#                 "description":  param["description"],
#                 "module":       param["module"],
#                 "source":       param["source"],
#                 "page":         param["page"],
#             },
#         })
#         if param["module"]:
#             relationships.append({
#                 "from_id":    f"module_{param['module']}",
#                 "to_id":      f"param_{pid}",
#                 "type":       "HAS_PARAMETER",
#                 "properties": {},
#             })

#     # 6. DocumentRef nodes from doc_modules
#     for fname, module in doc_modules.items():
#         doc_id = re.sub(r"[^A-Za-z0-9_]", "_", Path(fname).stem)
#         nodes.append({
#             "node_id": f"docref_{doc_id}",
#             "label":   "DocumentRef",
#             "properties": {
#                 "id":       doc_id,
#                 "filename": fname,
#                 "module":   module,
#             },
#         })
#         if module and module in modules_seen:
#             relationships.append({
#                 "from_id":    f"docref_{doc_id}",
#                 "to_id":      f"module_{module}",
#                 "type":       "SPECIFIES",
#                 "properties": {},
#             })

#     return nodes, relationships


# def _build_req_text_map(chunks: list[dict], id_inventory: dict) -> dict[str, str]:
#     """For each requirement ID, find the first sentence that follows its marker in a chunk."""
#     result: dict[str, str] = {}
#     for chunk in chunks:
#         for match in _REQ_BODY_RE.finditer(chunk["text"]):
#             bare_id = match.group("id")
#             if bare_id in id_inventory and bare_id not in result:
#                 body = match.group("body").strip()
#                 # Clean up newlines, keep first 400 chars
#                 body = re.sub(r"\s+", " ", body)[:400]
#                 result[bare_id] = body
#     return result


# def _expand_module_name(abbrev: str) -> str:
#     """Expand known AUTOSAR module abbreviations."""
#     expansions = {
#         "ComM": "Communication Manager",
#         "Can":  "CAN Driver",
#         "NvM":  "NV Memory Manager",
#         "Dcm":  "Diagnostic Communication Manager",
#         "Dem":  "Diagnostic Event Manager",
#         "RTE":  "Runtime Environment",
#         "Os":   "Operating System",
#         "Com":  "Communication",
#         "BSW":  "Basic Software",
#     }
#     return expansions.get(abbrev, abbrev)


# # ══════════════════════════════════════════════════════════════════════════════
# # TRACK B — LLM-based (async)
# # ══════════════════════════════════════════════════════════════════════════════

# async def _track_b(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
#     """
#     Run LLMGraphTransformer on each chunk concurrently.
#     Uses a semaphore to cap concurrent vLLM requests at LLM_MAX_CONCURRENT.
#     """
#     try:
#         from langchain_openai import ChatOpenAI
#         from langchain_experimental.graph_transformers import LLMGraphTransformer
#         from langchain_core.documents import Document as LCDocument
#         import httpx
#     except ImportError as e:
#         log.error("Missing dependency for Track B: %s", e)
#         return [], []

#     llm = ChatOpenAI(
#         model=settings.LLM_MODEL,
#         api_key=settings.VLLM_API_KEY,
#         base_url=settings.VLLM_BASE_URL,
#         temperature=settings.LLM_TEMPERATURE,
#         max_tokens=settings.LLM_MAX_TOKENS,
#         timeout=settings.LLM_TIMEOUT,
#         http_client=httpx.Client(verify=False, timeout=settings.LLM_TIMEOUT),
#     )

#     transformer = LLMGraphTransformer(
#         llm=llm,
#         allowed_nodes=settings.ALLOWED_NODES,
#         allowed_relationships=settings.ALLOWED_RELATIONSHIPS,
#         # Custom prompt snippet to handle AUTOSAR IDs correctly
#         prompt=_build_extraction_prompt(),
#     )

#     semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
#     all_nodes:  list[dict] = []
#     all_rels:   list[dict] = []
#     failures = 0

#     async def _process_one(chunk: dict) -> tuple[list[dict], list[dict]]:
#         nonlocal failures
#         async with semaphore:
#             doc = LCDocument(
#                 page_content=chunk["text"],
#                 metadata={
#                     "source":   chunk["filename"],
#                     "page":     chunk["page"],
#                     "chunk_id": chunk["chunk_id"],
#                 },
#             )
#             try:
#                 graph_docs = await transformer.aconvert_to_graph_documents([doc])
#                 return _graph_docs_to_dicts(graph_docs, chunk)
#             except Exception as exc:
#                 failures += 1
#                 if failures <= 5:
#                     log.warning(
#                         "Track B failed on chunk %s: %s: %.100s",
#                         chunk["chunk_id"], type(exc).__name__, str(exc),
#                     )
#                 return [], []

#     tasks = [_process_one(c) for c in chunks]

#     # Process with progress logging every 50 chunks
#     results = []
#     for i, coro in enumerate(asyncio.as_completed(tasks), 1):
#         n, r = await coro
#         results.append((n, r))
#         if i % 50 == 0:
#             log.info("  Track B: %d / %d chunks processed (%d failures)", i, len(tasks), failures)

#     for n, r in results:
#         all_nodes.extend(n)
#         all_rels.extend(r)

#     if failures:
#         log.warning("Track B: %d chunk(s) failed extraction", failures)

#     return all_nodes, all_rels


# def _graph_docs_to_dicts(
#     graph_docs: list,
#     chunk: dict,
# ) -> tuple[list[dict], list[dict]]:
#     """Convert LangChain GraphDocument objects to our node/relationship dicts."""
#     nodes: list[dict] = []
#     rels:  list[dict] = []

#     for gd in graph_docs:
#         # Node ID: sanitize name + label for consistency
#         node_id_map: dict[str, str] = {}

#         for node in gd.nodes:
#             raw_name = str(node.id).strip()
#             label    = str(node.type).strip() if node.type else "Entity"
#             safe_name = re.sub(r"\s+", "_", raw_name.lower())
#             safe_name = re.sub(r"[^a-z0-9_]", "", safe_name)[:60]
#             node_id = f"{label.lower()}_{safe_name}"
#             node_id_map[node.id] = node_id

#             props = dict(node.properties) if node.properties else {}
#             props.update({
#                 "name":       raw_name,
#                 "source":     chunk["filename"],
#                 "chunk_id":   chunk["chunk_id"],
#             })

#             nodes.append({
#                 "node_id":    node_id,
#                 "label":      label,
#                 "properties": props,
#             })

#         for rel in gd.relationships:
#             from_id = node_id_map.get(rel.source.id, "")
#             to_id   = node_id_map.get(rel.target.id, "")
#             if not from_id or not to_id:
#                 continue
#             rel_type = str(rel.type).upper().replace(" ", "_")

#             rels.append({
#                 "from_id":    from_id,
#                 "to_id":      to_id,
#                 "type":       rel_type,
#                 "properties": {
#                     "source":   chunk["filename"],
#                     "page":     chunk["page"],
#                     "chunk_id": chunk["chunk_id"],
#                     "method":   "llm_based",
#                 },
#             })

#     return nodes, rels


# def _build_extraction_prompt() -> Any:
#     """
#     Build a custom LangChain prompt for AUTOSAR-aware entity extraction.
#     Instructs the LLM to preserve exact requirement IDs and module names.
#     """
#     from langchain_core.prompts import ChatPromptTemplate

#     system = """You are an expert in AUTOSAR (Automotive Open System Architecture) standards.
# Your task is to extract entities and relationships from AUTOSAR specification text.

# CRITICAL RULES:
# 1. AUTOSAR requirement IDs follow the pattern [SWS_ModuleName_NNNNN] or [SRS_ModuleName_NNNNN].
#    Extract them EXACTLY as written — do not paraphrase, abbreviate, or expand them.
# 2. Module names are proper nouns: ComM, NvM, Can, Dcm, Dem, RTE, Os, Com, BSW, MCAL.
#    Preserve exact capitalization — do NOT write "communication manager" instead of "ComM".
# 3. Standard references: ISO 26262, IEC 61508, AUTOSAR_SRS_General. Preserve exactly.
# 4. Only extract entities and relationships that are EXPLICITLY stated in the text.
#    Do NOT infer or hallucinate.
# 5. Use ONLY these node types: {node_types}
# 6. Use ONLY these relationship types: {rel_types}

# Extract entities and relationships now:"""

#     human = "{input}"

#     return ChatPromptTemplate.from_messages([
#         ("system", system.format(
#             node_types=", ".join(settings.ALLOWED_NODES),
#             rel_types=", ".join(settings.ALLOWED_RELATIONSHIPS),
#         )),
#         ("human", human),
#     ])



"""
pipeline/stage5_extract_entities.py
====================================
Stage 5: Dual-track entity & relation extraction.

Track A — Rule-based (fast, perfect, free)
    Builds Requirement nodes, ConfigParameter nodes, DocumentRef nodes,
    Module nodes, and REFERENCES edges directly from the harvested ID
    inventory and bibliography sections. Zero LLM calls.

Track B — LLM-based (semantic, via vLLM)
    Sends content/requirement/explanatory chunks to LLMGraphTransformer.
    Extracts Concept nodes, semantic relationships (DEPENDS_ON, IMPLEMENTS,
    ALLOCATED_TO, CONTRADICTS, SPECIALIZES, DEFINED_BY, etc.).
    Runs with asyncio + semaphore to respect --max-num-seqs 16.

Output schema:
    {
        "nodes": [
            {
                "node_id":    "req_SWS_ComM_00123",
                "label":      "Requirement",
                "properties": {
                    "id":         "SWS_ComM_00123",
                    "full_id":    "[SWS_ComM_00123]",
                    "module":     "ComM",
                    "id_type":    "SWS",
                    "raw_text":   "...",     # filled if found in a chunk
                    ...
                }
            },
            ...
        ],
        "relationships": [
            {
                "from_id":  "req_SWS_ComM_00123",
                "to_id":    "req_SWS_Can_00456",
                "type":     "REFERENCES",
                "properties": {"source": "AUTOSAR_SWS_ComM.pdf", "page": 47}
            },
            ...
        ]
    }
"""

from __future__ import annotations

import asyncio
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from utils.logger import get_logger
from utils.llm_client import acall_llm_json
from config import settings

# Suppress Pydantic serializer warnings from LLMGraphTransformer internals
# These are cosmetic warnings about DynamicGraph serialization — not errors.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

log = get_logger("stage5")

# ── Requirement text extraction: find the sentence(s) immediately following an ID ─
_REQ_BODY_RE = re.compile(
    r"\[(?P<id>[A-Za-z_]+_\d{4,5})\]\s*(?P<body>[^\[]{10,400})",
    re.DOTALL,
)


# ── Provenance helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string (used for ingested_at)."""
    return datetime.now(timezone.utc).isoformat()


def _stamp_node(
    node: dict,
    confidence_score: float,
    source_chunk_id: str | None = None,
    extraction_method: str = "rule_based",
) -> dict:
    """
    Attach provenance properties to a node dict in-place and return it.

    Added properties
    ----------------
    ingested_at       : ISO-8601 UTC timestamp of this pipeline run
    confidence_score  : float 0.0–1.0 indicating extraction confidence
    source_chunk_id   : chunk from which this node was extracted (if known)
    extraction_method : 'rule_based' | 'llm_based' | 'glossary'
    pipeline_version  : from settings.PIPELINE_VERSION
    """
    node["properties"].update({
        "ingested_at":        _now_iso(),
        "confidence_score":   confidence_score,
        "extraction_method":  extraction_method,
        "pipeline_version":   settings.PIPELINE_VERSION,
    })
    if source_chunk_id is not None:
        node["properties"]["source_chunk_id"] = source_chunk_id
    return node


def run(
    chunks:       list[dict],
    harvest:      dict,
    config_params: list[dict],
) -> dict:
    """
    Extract all nodes and relationships.

    Args:
        chunks:        from Stage 4
        harvest:       from Stage 3 (id_inventory, cross_refs, doc_modules)
        config_params: from Stage 4 (structured ECUC param records)

    Returns:
        {"nodes": [...], "relationships": [...]}
    """
    log.info("Stage 5: entity & relation extraction")

    nodes:         list[dict] = []
    relationships: list[dict] = []

    # ── Track A: Rule-based ───────────────────────────────────────────────────
    log.info("  Track A: rule-based extraction ...")
    a_nodes, a_rels = _track_a(harvest, config_params, chunks)
    nodes.extend(a_nodes)
    relationships.extend(a_rels)
    log.info(
        "  Track A done: %d nodes, %d relationships",
        len(a_nodes), len(a_rels),
    )

    # ── Track B: LLM-based ───────────────────────────────────────────────────
    log.info("  Track B: LLM-based extraction (%d chunks) ...", len(chunks))
    b_nodes, b_rels = asyncio.run(_track_b(chunks))
    nodes.extend(b_nodes)
    relationships.extend(b_rels)
    log.info(
        "  Track B done: %d nodes, %d relationships",
        len(b_nodes), len(b_rels),
    )

    # ── Post-extraction relationship validation ───────────────────────────────
    log.info("  Relationship validation pass ...")
    # Only validate LLM-extracted relationships (Track B), not rule-based (Track A)
    a_rels_set = {(r["from_id"], r["to_id"], r["type"]) for r in a_rels}
    b_rels_only = [r for r in relationships if (r["from_id"], r["to_id"], r["type"]) not in a_rels_set]
    if b_rels_only:
        # Build chunk text lookup for validation context
        chunk_text_map = {c["chunk_id"]: c.get("cleaned_text", c.get("text", "")) for c in chunks}
        validated_b_rels = asyncio.run(_validate_relationships(b_rels_only, chunk_text_map))
        log.info(
            "  Validation: kept %d / %d LLM relationships",
            len(validated_b_rels), len(b_rels_only),
        )
        relationships = a_rels + validated_b_rels
    else:
        relationships = a_rels

    # ── Glossary page processing ──────────────────────────────────────────────
    log.info("  Processing glossary pages ...")
    glossary_pages = [c for c in chunks if c.get("content_type") in ("glossary",)]
    if glossary_pages:
        concept_nodes = asyncio.run(_process_glossary_chunks(glossary_pages))
        log.info("  Glossary: %d Concept nodes extracted", len(concept_nodes))
        nodes.extend(concept_nodes)

    # ── Deduplicate nodes by node_id ──────────────────────────────────────────
    seen_ids: set[str] = set()
    unique_nodes: list[dict] = []
    for node in nodes:
        nid = node["node_id"]
        if nid not in seen_ids:
            seen_ids.add(nid)
            unique_nodes.append(node)

    # ── Deduplicate relationships ─────────────────────────────────────────────
    seen_rels: set[tuple] = set()
    unique_rels: list[dict] = []
    for rel in relationships:
        key = (rel["from_id"], rel["to_id"], rel["type"])
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(rel)

    log.info(
        "Stage 5 complete: %d unique nodes, %d unique relationships",
        len(unique_nodes), len(unique_rels),
    )
    return {"nodes": unique_nodes, "relationships": unique_rels}


# ══════════════════════════════════════════════════════════════════════════════
# TRACK A — Rule-based
# ══════════════════════════════════════════════════════════════════════════════

def _track_a(
    harvest:       dict,
    config_params: list[dict],
    chunks:        list[dict],
) -> tuple[list[dict], list[dict]]:
    nodes:         list[dict] = []
    relationships: list[dict] = []

    id_inventory: dict[str, dict] = harvest["id_inventory"]
    cross_refs:   list[dict]      = harvest["cross_refs"]
    doc_modules:  dict[str, str]  = harvest["doc_modules"]

    # Build a lookup: bare_id → raw_text (first occurrence in chunks)
    req_text_map: dict[str, str] = _build_req_text_map(chunks, id_inventory)

    # Build a lookup: bare_id → first chunk_id that contains it (for source_chunk_id)
    req_chunk_map: dict[str, str] = _build_req_chunk_map(chunks, id_inventory)

    # Timestamp shared across all nodes in this pipeline run
    run_ts = _now_iso()

    # 1. Requirement nodes
    for bare_id, info in id_inventory.items():
        node_id = f"req_{bare_id}"
        node = {
            "node_id": node_id,
            "label":   "Requirement",
            "properties": {
                "id":          bare_id,
                "full_id":     info["full_id"],
                "module":      info["module"],
                "id_type":     info["id_type"],
                "raw_text":    req_text_map.get(bare_id, ""),
                "sources":     [o["source"] for o in info["occurrences"]],
                "pages":       [o["page"]   for o in info["occurrences"]],
            },
        }
        _stamp_node(
            node,
            confidence_score=1.0,           # rule-based ID extraction is deterministic
            source_chunk_id=req_chunk_map.get(bare_id),
            extraction_method="rule_based",
        )
        nodes.append(node)

    # 2. Module nodes (from doc_modules + from requirement module fields)
    modules_seen: set[str] = set()
    for info in id_inventory.values():
        mod = info["module"]
        if mod and mod not in modules_seen:
            modules_seen.add(mod)
            node = {
                "node_id": f"module_{mod}",
                "label":   "Module",
                "properties": {
                    "name":      mod,
                    "full_name": _expand_module_name(mod),
                },
            }
            _stamp_node(node, confidence_score=1.0, extraction_method="rule_based")
            nodes.append(node)

    # 3. Module → HAS_REQUIREMENT edges
    for bare_id, info in id_inventory.items():
        mod = info["module"]
        if mod:
            relationships.append({
                "from_id":    f"module_{mod}",
                "to_id":      f"req_{bare_id}",
                "type":       "HAS_REQUIREMENT",
                "properties": {},
            })

    # 4. REFERENCES edges from co-occurrence cross-refs
    #    Use only same-module cross-refs as high-confidence; cross-module
    #    cross-refs are kept but marked lower confidence
    for xref in cross_refs:
        from_id = xref["from_id"]
        to_id   = xref["to_id"]
        if from_id not in id_inventory or to_id not in id_inventory:
            continue
        from_mod = id_inventory[from_id]["module"]
        to_mod   = id_inventory[to_id]["module"]
        relationships.append({
            "from_id":    f"req_{from_id}",
            "to_id":      f"req_{to_id}",
            "type":       "REFERENCES",
            "properties": {
                "source":          xref["source"],
                "page":            xref["page"],
                "cross_module":    from_mod != to_mod,
                "method":          "rule_based_cooccurrence",
            },
        })

    # 5. ConfigParameter nodes + HAS_PARAMETER edges
    for param in config_params:
        pid = param["param_id"]
        node = {
            "node_id": f"param_{pid}",
            "label":   "ConfigParameter",
            "properties": {
                "id":           pid,
                "name":         param["name"],
                "type":         param["type"],
                "multiplicity": param["multiplicity"],
                "range":        param["range"],
                "description":  param["description"],
                "module":       param["module"],
                "source":       param["source"],
                "page":         param["page"],
            },
        }
        _stamp_node(node, confidence_score=1.0, extraction_method="rule_based")
        nodes.append(node)
        if param["module"]:
            relationships.append({
                "from_id":    f"module_{param['module']}",
                "to_id":      f"param_{pid}",
                "type":       "HAS_PARAMETER",
                "properties": {},
            })

    # 6. DocumentRef nodes from doc_modules
    for fname, module in doc_modules.items():
        doc_id = re.sub(r"[^A-Za-z0-9_]", "_", Path(fname).stem)
        node = {
            "node_id": f"docref_{doc_id}",
            "label":   "DocumentRef",
            "properties": {
                "id":       doc_id,
                "filename": fname,
                "module":   module,
            },
        }
        _stamp_node(node, confidence_score=1.0, extraction_method="rule_based")
        nodes.append(node)
        if module and module in modules_seen:
            relationships.append({
                "from_id":    f"docref_{doc_id}",
                "to_id":      f"module_{module}",
                "type":       "SPECIFIES",
                "properties": {},
            })

    return nodes, relationships


def _build_req_text_map(chunks: list[dict], id_inventory: dict) -> dict[str, str]:
    """For each requirement ID, find the first sentence that follows its marker in a chunk."""
    result: dict[str, str] = {}
    for chunk in chunks:
        for match in _REQ_BODY_RE.finditer(chunk["text"]):
            bare_id = match.group("id")
            if bare_id in id_inventory and bare_id not in result:
                body = match.group("body").strip()
                # Clean up newlines, keep first 400 chars
                body = re.sub(r"\s+", " ", body)[:400]
                result[bare_id] = body
    return result


def _build_req_chunk_map(chunks: list[dict], id_inventory: dict) -> dict[str, str]:
    """
    For each requirement ID, return the chunk_id of the first chunk that contains it.
    Used to populate source_chunk_id on Requirement nodes for provenance tracking.
    """
    result: dict[str, str] = {}
    for chunk in chunks:
        for req_id in chunk.get("req_ids_present", []):
            if req_id in id_inventory and req_id not in result:
                result[req_id] = chunk["chunk_id"]
    return result


def _expand_module_name(abbrev: str) -> str:
    """Expand known AUTOSAR module abbreviations."""
    expansions = {
        "ComM": "Communication Manager",
        "Can":  "CAN Driver",
        "NvM":  "NV Memory Manager",
        "Dcm":  "Diagnostic Communication Manager",
        "Dem":  "Diagnostic Event Manager",
        "RTE":  "Runtime Environment",
        "Os":   "Operating System",
        "Com":  "Communication",
        "BSW":  "Basic Software",
    }
    return expansions.get(abbrev, abbrev)


# ══════════════════════════════════════════════════════════════════════════════
# TRACK B — LLM-based (async)
# ══════════════════════════════════════════════════════════════════════════════

async def _track_b(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Run LLMGraphTransformer on each chunk concurrently.
    Uses a semaphore to cap concurrent vLLM requests at LLM_MAX_CONCURRENT.
    """
    try:
        from langchain_openai import ChatOpenAI
        from langchain_experimental.graph_transformers import LLMGraphTransformer
        from langchain_core.documents import Document as LCDocument
        import httpx
    except ImportError as e:
        log.error("Missing dependency for Track B: %s", e)
        return [], []

    llm = ChatOpenAI(
        model=settings.LLM_MODEL,
        api_key=settings.VLLM_API_KEY,
        base_url=settings.VLLM_BASE_URL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        timeout=settings.LLM_TIMEOUT,
        http_client=httpx.Client(verify=False, timeout=settings.LLM_TIMEOUT),
    )

    transformer = LLMGraphTransformer(
        llm=llm,
        allowed_nodes=settings.ALLOWED_NODES,
        allowed_relationships=settings.ALLOWED_RELATIONSHIPS,
        # Custom prompt snippet to handle AUTOSAR IDs correctly
        prompt=_build_extraction_prompt(),
    )

    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    all_nodes:  list[dict] = []
    all_rels:   list[dict] = []
    failures = 0

    async def _process_one(chunk: dict) -> tuple[list[dict], list[dict]]:
        nonlocal failures
        async with semaphore:
            doc = LCDocument(
                page_content=chunk["text"],
                metadata={
                    "source":   chunk["filename"],
                    "page":     chunk["page"],
                    "chunk_id": chunk["chunk_id"],
                },
            )
            try:
                graph_docs = await transformer.aconvert_to_graph_documents([doc])
                return _graph_docs_to_dicts(graph_docs, chunk)
            except Exception as exc:
                failures += 1
                if failures <= 5:
                    log.warning(
                        "Track B failed on chunk %s: %s: %.100s",
                        chunk["chunk_id"], type(exc).__name__, str(exc),
                    )
                return [], []

    tasks = [_process_one(c) for c in chunks]

    # Process with progress logging every 50 chunks
    results = []
    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        n, r = await coro
        results.append((n, r))
        if i % 50 == 0:
            log.info("  Track B: %d / %d chunks processed (%d failures)", i, len(tasks), failures)

    for n, r in results:
        all_nodes.extend(n)
        all_rels.extend(r)

    if failures:
        log.warning("Track B: %d chunk(s) failed extraction", failures)

    return all_nodes, all_rels


def _graph_docs_to_dicts(
    graph_docs: list,
    chunk: dict,
) -> tuple[list[dict], list[dict]]:
    """Convert LangChain GraphDocument objects to our node/relationship dicts."""
    nodes: list[dict] = []
    rels:  list[dict] = []

    for gd in graph_docs:
        # Node ID: sanitize name + label for consistency
        node_id_map: dict[str, str] = {}

        for node in gd.nodes:
            raw_name = str(node.id).strip()
            label    = str(node.type).strip() if node.type else "Entity"
            safe_name = re.sub(r"\s+", "_", raw_name.lower())
            safe_name = re.sub(r"[^a-z0-9_]", "", safe_name)[:60]
            node_id = f"{label.lower()}_{safe_name}"
            node_id_map[node.id] = node_id

            props = dict(node.properties) if node.properties else {}
            props.update({
                "name":       raw_name,
                "source":     chunk["filename"],
                "chunk_id":   chunk["chunk_id"],
            })

            node_dict = {
                "node_id":    node_id,
                "label":      label,
                "properties": props,
            }
            _stamp_node(
                node_dict,
                confidence_score=0.85,          # LLM extraction: high but not certain
                source_chunk_id=chunk["chunk_id"],
                extraction_method="llm_based",
            )
            nodes.append(node_dict)

        for rel in gd.relationships:
            from_id = node_id_map.get(rel.source.id, "")
            to_id   = node_id_map.get(rel.target.id, "")
            if not from_id or not to_id:
                continue
            rel_type = str(rel.type).upper().replace(" ", "_")

            rels.append({
                "from_id":    from_id,
                "to_id":      to_id,
                "type":       rel_type,
                "properties": {
                    "source":   chunk["filename"],
                    "page":     chunk["page"],
                    "chunk_id": chunk["chunk_id"],
                    "method":   "llm_based",
                },
            })

    return nodes, rels


def _build_extraction_prompt() -> Any:
    """
    Deeply AUTOSAR-specific extraction prompt for Qwen2.5-72B.
    Covers CP/AP architecture, exact ID preservation, relationship semantics,
    and explicit CONTRADICTS restriction.
    """
    from langchain_core.prompts import ChatPromptTemplate

    node_types = ", ".join(settings.ALLOWED_NODES)
    rel_types  = ", ".join(settings.ALLOWED_RELATIONSHIPS)

    system = f"""You are a senior AUTOSAR architect extracting a structured knowledge graph
from AUTOSAR specification text. You have deep expertise in both Classic Platform (CP)
and Adaptive Platform (AP).

AUTOSAR ARCHITECTURE CONTEXT:
- CP layered architecture: Application Layer → RTE → BSW (Services/ECU Abstraction/MCAL) → Hardware
- AP functional clusters: Execution Management, Communication Management, Diagnostics,
  Persistency, Platform Health Management, Crypto, Time Synchronization, etc.
- Requirement ID formats: [SWS_X_NNNNN] [SRS_X_NNNNN] [RS_X_NNNNN] [TR_X_NNNNN] [PRS_X_NNNNN]

ALLOWED NODE TYPES: {node_types}
ALLOWED RELATIONSHIP TYPES: {rel_types}

EXTRACTION RULES — FOLLOW EXACTLY:

1. REQUIREMENT IDs: Extract [SWS_X_NNNNN], [RS_X_NNNNN] etc EXACTLY as written.
   Never paraphrase. The ID string IS the node identifier.

2. MODULE NAMES: Use official AUTOSAR abbreviations with correct casing:
   ComM, NvM, Can, CanIf, Dcm, Dem, RTE, Os, Com, BSW, MCAL, CSM, CryIf,
   Crypto, Arti, PHM, EM, CM, Diag, Per, TS, UCM, SM, IAM.
   Never write "communication manager" — write "ComM".

3. RELATIONSHIP SEMANTICS — be precise:
   - CALLS: function A directly invokes function B (API call chain)
   - DEPENDS_ON: module-level architectural coupling
   - IMPLEMENTS: module/function fulfils a requirement
   - DEFINED_BY: concept defined by a standard or organisation
   - DEFINED_IN: concept formally defined in a specific document
   - DERIVED_FROM: child requirement derived from parent (vertical trace)
   - TRACES_TO: SRS → SWS forward tracing
   - ALLOCATED_TO: requirement assigned to a module/cluster

4. CONTRADICTS RESTRICTION:
   ONLY use CONTRADICTS if the text contains one of these exact phrases:
   "conflicts with", "contradicts", "mutually exclusive", "incompatible with".
   Never infer contradiction from indirect language.

5. CONFIDENCE: Only extract what is EXPLICITLY stated. Do NOT infer or hallucinate.

6. CONCEPT_ / MODULE_ PREFIXES: Do NOT emit node names like "Concept_Job" or
   "Module_ComM". Use plain names: "Job", "ComM".

Extract the knowledge graph now."""

    human = "{input}"

    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("human", human),
    ])

# ══════════════════════════════════════════════════════════════════════════════
# Post-extraction relationship validation
# ══════════════════════════════════════════════════════════════════════════════

_VALIDATE_SYSTEM = """You are validating relationships extracted from AUTOSAR specification text.
For each relationship, check whether the source chunk text actually supports it.

Return ONLY a JSON array of booleans, one per relationship (true = keep, false = drop).
Example: [true, false, true, true, false]
No explanation, no markdown fences."""


async def _validate_relationships(
    relationships: list[dict],
    chunk_text_map: dict[str, str],
) -> list[dict]:
    """Validate LLM-extracted relationships in batches of 25."""
    if not relationships:
        return []

    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    BATCH = 25

    async def _validate_batch(batch: list[dict]) -> list[dict]:
        # Group by chunk_id for context
        chunk_id = batch[0].get("properties", {}).get("chunk_id", "")
        context = chunk_text_map.get(chunk_id, "")[:1500]

        rel_lines = []
        for i, r in enumerate(batch):
            rel_lines.append(
                f"{i+1}. {r['from_id']} --[{r['type']}]--> {r['to_id']}"
            )

        user = (
            f"Source chunk text:\n{context}\n\n"
            f"Relationships to validate:\n" + "\n".join(rel_lines)
        )

        result = await acall_llm_json(
            system=_VALIDATE_SYSTEM,
            user=user,
            semaphore=semaphore,
        )

        if result and isinstance(result, list) and len(result) == len(batch):
            return [r for r, keep in zip(batch, result) if keep]
        # On parse failure, keep all (don't drop valid rels due to LLM error)
        return batch

    tasks = []
    for i in range(0, len(relationships), BATCH):
        tasks.append(_validate_batch(relationships[i:i + BATCH]))

    batches = await asyncio.gather(*tasks)
    return [r for batch in batches for r in batch]


# ══════════════════════════════════════════════════════════════════════════════
# Glossary page processing
# ══════════════════════════════════════════════════════════════════════════════

_GLOSSARY_SYSTEM = """You are extracting term definitions from an AUTOSAR glossary or abbreviations page.
Return ONLY a JSON array of term-definition pairs:
[{"term": "<exact term>", "definition": "<full definition text>"}, ...]

Rules:
- Preserve AUTOSAR-specific capitalization (ComM, RTE, PDU, ECU)
- Include both abbreviations and their expanded forms
- If no definitions found, return []
- No markdown fences, no explanation"""


async def _process_glossary_chunks(chunks: list[dict]) -> list[dict]:
    """Extract Concept nodes with definitions from glossary chunks."""
    semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT)
    all_concepts: list[dict] = []

    async def _process_one(chunk: dict) -> list[dict]:
        text = chunk.get("cleaned_text", chunk.get("text", ""))[:2000]
        result = await acall_llm_json(
            system=_GLOSSARY_SYSTEM,
            user=text,
            semaphore=semaphore,
        )
        concepts = []
        if result and isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                term = item.get("term", "").strip()
                defn = item.get("definition", "").strip()
                if not term:
                    continue
                safe = re.sub(r"\s+", "_", term.lower())
                safe = re.sub(r"[^a-z0-9_]", "", safe)[:60]
                node = {
                    "node_id": f"concept_{safe}",
                    "label": "Concept",
                    "properties": {
                        "name": term,
                        "definition": defn,
                        "source": chunk.get("filename", ""),
                        "chunk_id": chunk.get("chunk_id", ""),
                        "from_glossary": True,
                    },
                }
                _stamp_node(
                    node,
                    confidence_score=0.90,      # LLM glossary extraction: high confidence
                    source_chunk_id=chunk.get("chunk_id"),
                    extraction_method="glossary",
                )
                concepts.append(node)
        return concepts

    tasks = [_process_one(c) for c in chunks]
    results = await asyncio.gather(*tasks)
    for batch in results:
        all_concepts.extend(batch)
    return all_concepts
