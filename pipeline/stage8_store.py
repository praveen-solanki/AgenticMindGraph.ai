
"""
pipeline/stage8_store.py
========================
Stage 8: Write everything to Neo4j.

Write order (important — constraints must be satisfied):
  1.  Document nodes
  2.  Chunk nodes  (no embedding yet)
  3.  Chunk sequential edges  (NEXT_CHUNK / PREV_CHUNK)
  4.  Requirement nodes
  5.  Module nodes
  6.  ConfigParameter nodes
  7.  DocumentRef nodes
  8.  Other entity nodes  (Concept, StandardRef, etc.)
  9.  Chunk → Entity MENTIONS edges
  10. Entity → Entity domain relationship edges
  11. Module → Requirement HAS_REQUIREMENT edges
  12. Module → Param HAS_PARAMETER edges
  13. Chunk embeddings  (written separately — large payload)
  14. Vector index creation
  15. kNN SIMILAR_TO edges
  16. Chunk → Module SOURCED_FROM edges  ← shortcut for GraphRAG traversal

All writes use MERGE (not CREATE) — idempotent, safe to re-run.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger
from utils.neo4j_client import Neo4jClient
from config import settings

log = get_logger("stage8")


def run(
    chunks:       list[dict],
    entity_data:  dict,
    config_params: list[dict],
    pages:        list[dict],
) -> None:
    """
    Write the full KG to Neo4j.

    Args:
        chunks:        embedded chunk dicts from Stage 7
        entity_data:   resolved nodes + relationships from Stage 6
        config_params: ConfigParameter records from Stage 4
        pages:         cleaned pages from Stage 2 (for Document node metadata)
    """
    with Neo4jClient() as neo:
        # ── Schema ───────────────────────────────────────────────────────────
        log.info("Creating constraints and indexes ...")
        neo.create_constraints_and_indexes()

        # ── Resume guard: warn on partial ingestion from previous run ─────────
        partial = neo.run(
            "MATCH (d:Document) WHERE d.ingestion_complete IS NULL RETURN count(d) AS n"
        )
        if partial and partial[0]["n"] > 0:
            log.warning(
                "  %d Document nodes found without ingestion_complete flag — "
                "possible partial ingestion from a previous run. Consider re-ingesting.",
                partial[0]["n"]
            )

        # ── 1. Document nodes ─────────────────────────────────────────────────
        _write_document_nodes(neo, pages)

        # ── 2 & 3. Chunk nodes + sequential edges ─────────────────────────────
        _write_chunk_nodes(neo, chunks)
        _write_chunk_sequential_edges(neo, chunks)

        # ── 4–8. Entity nodes ─────────────────────────────────────────────────
        nodes = entity_data["nodes"]
        _write_entity_nodes_by_label(neo, nodes)

        # ── 8b. Cross-Track Reconciliation (Non-Destructive) ──────────────────
        _resolve_document_references(neo)

        # ── ConfigParameter nodes from Stage 4 (Issue 8 fix) ──────────────────
        _write_config_param_nodes(neo, config_params)

        # ── 9. Chunk → Entity MENTIONS edges ──────────────────────────────────
        _write_mentions_edges(neo, chunks, entity_data)

        # ── 10–12. All other relationships ────────────────────────────────────
        _rel_total, _skipped_rels = _write_relationships(neo, entity_data["relationships"])
        if _skipped_rels:
            log.warning(
                "  Stage 8 summary: %d relationship edge(s) skipped due to missing nodes "
                "(see WARNING lines above for details)",
                len(_skipped_rels),
            )

        # ── 13. Chunk embeddings ──────────────────────────────────────────────
        _write_chunk_embeddings(neo, chunks)

        # ── 14. Vector indexes (primary + summary) ────────────────────────────
        log.info("Creating vector indexes ...")
        neo.create_vector_index()
        _create_summary_vector_index(neo)

        # ── 15. kNN SIMILAR_TO edges ──────────────────────────────────────────
        _write_knn_edges(neo, chunks)

        # ── 16. Chunk → Module SOURCED_FROM edges ─────────────────────────────
        # Shortcut edge: allows GraphRAG to answer "what does module X require?"
        # in one hop (Chunk → Module) instead of traversing:
        #   Chunk → MENTIONS → Requirement → HAS_REQUIREMENT (reverse) → Module.
        # Derivation: chunk["filename"] maps to a module via the Module nodes
        # that were created from the same document in Stage 5 Track A.
        # This is a pure post-storage Cypher pass — no pipeline data needed.
        _write_chunk_module_edges(neo)

        # ── Mark ingestion complete ────────────────────────────────────────────
        neo.run(
            "MATCH (d:Document) SET d.ingestion_complete = true, d.ingested_at = $ts",
            ts=datetime.now(timezone.utc).isoformat()
        )

        # ── Post-storage graph audit ───────────────────────────────────────────
        _run_graph_audit(neo)

        # ── Summary ───────────────────────────────────────────────────────────
        _print_summary(neo)


# ══════════════════════════════════════════════════════════════════════════════
# 1. Document nodes
# ══════════════════════════════════════════════════════════════════════════════

def _write_document_nodes(neo: Neo4jClient, pages: list[dict]) -> None:
    # Deduplicate by source
    seen: dict[str, dict] = {}
    for p in pages:
        src = p["source"]
        if src not in seen:
            seen[src] = {
                "id":       hashlib.md5(src.encode()).hexdigest()[:16],
                "filename": p["filename"],
                "path":     src,
                "n_pages":  0,
            }
        seen[src]["n_pages"] = max(seen[src]["n_pages"], p["page_1idx"])

    rows = list(seen.values())
    cypher = """
    UNWIND $rows AS row
    MERGE (d:Document {id: row.id})
    SET d.filename = row.filename,
        d.path     = row.path,
        d.n_pages  = row.n_pages
    """
    n = neo.run_batch(cypher, rows)
    log.info("  Documents: %d written", n)


# ══════════════════════════════════════════════════════════════════════════════
# 2. Chunk nodes
# ══════════════════════════════════════════════════════════════════════════════

def _write_chunk_nodes(neo: Neo4jClient, chunks: list[dict]) -> None:
    rows = []
    for c in chunks:
        # doc_id must match the hashed Document ID created in _write_document_nodes
        doc_id = hashlib.md5(c["source"].encode()).hexdigest()[:16]
        rows.append({
            "chunk_id":        c["chunk_id"],
            "text":            c["text"],
            "cleaned_text":    c.get("cleaned_text") or c["text"],
            "summary":         c.get("summary", ""),
            "section_context": c.get("section_context", ""),
            "normative":       c.get("normative", False),
            "source":          c["filename"],
            "page":            c["page"],
            "H1":              c.get("H1") or "",
            "H2":              c.get("H2") or "",
            "H3":              c.get("H3") or "",
            "H4":              c.get("H4") or "",
            "token_count":     c["token_count"],
            "chunk_index":     c["chunk_index"],
            "content_type":    c["content_type"],
            "req_ids":         c.get("req_ids_present", []),
            "doc_id":          doc_id,
            "ingested_at":     datetime.now(timezone.utc).isoformat(),
            "pipeline_version": settings.PIPELINE_VERSION,
        })

    cypher = """
    UNWIND $rows AS row
    MERGE (c:Chunk {id: row.chunk_id})
    SET c.text            = row.text,
        c.cleaned_text    = row.cleaned_text,
        c.summary         = row.summary,
        c.section_context = row.section_context,
        c.normative       = row.normative,
        c.source          = row.source,
        c.page            = row.page,
        c.H1              = row.H1,
        c.H2              = row.H2,
        c.H3              = row.H3,
        c.H4              = row.H4,
        c.token_count     = row.token_count,
        c.chunk_index     = row.chunk_index,
        c.content_type    = row.content_type,
        c.req_ids         = row.req_ids,
        c.ingested_at     = row.ingested_at,
        c.pipeline_version = row.pipeline_version
    WITH c, row
    MATCH (d:Document {id: row.doc_id})
    MERGE (d)-[:HAS_CHUNK]->(c)
    """
    n = neo.run_batch(cypher, rows)
    log.info("  Chunks: %d written", n)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Sequential edges
# ══════════════════════════════════════════════════════════════════════════════

def _write_chunk_sequential_edges(neo: Neo4jClient, chunks: list[dict]) -> None:
    rows = [
        {
            "chunk_id":      c["chunk_id"],
            "prev_chunk_id": c["prev_chunk_id"],
            "next_chunk_id": c["next_chunk_id"],
        }
        for c in chunks
        if c.get("prev_chunk_id") or c.get("next_chunk_id")
    ]

    cypher = """
    UNWIND $rows AS row
    MATCH (c:Chunk {id: row.chunk_id})
    FOREACH (_ IN CASE WHEN row.prev_chunk_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (prev:Chunk {id: row.prev_chunk_id})
        MERGE (c)-[:PREV_CHUNK]->(prev)
    )
    FOREACH (_ IN CASE WHEN row.next_chunk_id IS NOT NULL THEN [1] ELSE [] END |
        MERGE (next:Chunk {id: row.next_chunk_id})
        MERGE (c)-[:NEXT_CHUNK]->(next)
    )
    """
    n = neo.run_batch(cypher, rows)
    log.info("  Sequential edges: %d chunks linked", n)


# ══════════════════════════════════════════════════════════════════════════════
# 4–8. Entity nodes grouped by label
# ══════════════════════════════════════════════════════════════════════════════

# Known label aliases that the LLM sometimes produces with wrong casing
_LABEL_ALIASES: dict[str, str] = {
    # DocumentRef
    "documentref":        "DocumentRef",
    "document_ref":       "DocumentRef",
    # ConfigParameter
    "configparameter":    "ConfigParameter",
    "config_parameter":   "ConfigParameter",
    # StandardRef
    "standardref":        "StandardRef",
    "standard_ref":       "StandardRef",
    # Concept
    "concept":            "Concept",
    "concept_node":       "Concept",
    # Function
    "function":           "Function",
    "func":               "Function",
    # Constraint
    "constraint":         "Constraint",
    # FunctionalCluster
    "functionalcluster":  "FunctionalCluster",
    "functional_cluster": "FunctionalCluster",
    # DataType
    "datatype":           "DataType",
    "data_type":          "DataType",
    # SpecificationItem
    "specificationitem":  "SpecificationItem",
    "specification_item": "SpecificationItem",
    # TestCase
    "testcase":           "TestCase",
    "test_case":          "TestCase",
    # TestSpecification
    "testspecification":  "TestSpecification",
    "test_specification": "TestSpecification",
    # ChangeRecord
    "changerecord":       "ChangeRecord",
    "change_record":      "ChangeRecord",
    # Organization
    "organization":       "Organization",
    "organisation":       "Organization",
    # Class
    "class":              "Class",
    # System
    "system":             "System",
    # Category
    "category":           "Category",
    # Entity (generic fallback)
    "entity":             "Entity",
}

def _normalise_label(label: str) -> str | None:
    """
    Normalise label casing.
    LLMGraphTransformer sometimes returns e.g. "Documentref" instead of
    "DocumentRef". Map known variants to the canonical form so all nodes
    of the same type land in one Neo4j label bucket.

    Fix 3: Step 2 previously used label.title() which corrupts multi-capital
    labels: "APIParameter"→"Apiparameter", "SensorInterface"→"Sensorinterface",
    "UseCase"→"Usecase", "AIDomain"→"Aidomain".  Replaced with a
    case-insensitive lookup that returns the correctly-cased canonical form
    directly from ALLOWED_NODES.
    """
    # Step 1: check alias dict (fastest path for known variants)
    canonical = _LABEL_ALIASES.get(label.lower())
    if canonical:
        return canonical
    # Step 2: case-insensitive search through ALLOWED_NODES so that
    # "apiparameter" matches "APIParameter", "sensorinterface" matches
    # "SensorInterface", etc. — .title() cannot handle these correctly.
    label_lower = label.lower()
    for allowed in settings.ALLOWED_NODES:
        if allowed.lower() == label_lower:
            return allowed          # return the canonical casing from ALLOWED_NODES
    # Step 3: reject — not a known label
    return None


def _write_entity_nodes_by_label(neo: Neo4jClient, nodes: list[dict]) -> None:
    by_label: dict[str, list[dict]] = defaultdict(list)
    rejected = 0
    for node in nodes:
        canonical_label = _normalise_label(node["label"])

        # DEFENSE IN DEPTH: Strict structural labels are reserved for physical ingestion.
        # Even with Stage 5 coercion, we reject any structural labels that bypass it
        # to prevent collision with authoritative structural nodes.
        if canonical_label in settings.STRICT_STRUCTURAL_LABELS:
            log.warning(
                "Rejected protected '%s' node from semantic track: '%s'",
                canonical_label, node.get("properties", {}).get("name", node.get("node_id", "?"))
            )
            rejected += 1
            continue

        if canonical_label is None:
            log.debug(
                "Rejected unknown label '%s' for node '%s' — not in ALLOWED_NODES",
                node["label"], node.get("node_id", "?")
            )
            rejected += 1
            continue
        node = dict(node, label=canonical_label)
        by_label[canonical_label].append(node)

    if rejected:
        log.warning("  %d nodes rejected for unknown label (see DEBUG log for details)", rejected)

    for label, label_nodes in by_label.items():
        if label == "ConfigParameter":
            _write_config_param_nodes_from_llm(neo, label_nodes)
            continue
        _write_nodes_for_label(neo, label, label_nodes)


def _write_nodes_for_label(neo: Neo4jClient, label: str, nodes: list[dict]) -> None:
    """
    Write nodes for a single label using MERGE on node_id.

    Root cause of the ConstraintError that was observed:
      MERGE (n:{label} {id: row.node_id})   ← MERGE key = node_id  e.g. "req_SWS_Crypto_00018"
      SET n += row.properties               ← properties also contains "id": "SWS_Crypto_00018"
                                               which overwrites n.id with the bare ID.
      On re-run MERGE tries to find node where id = "req_SWS_Crypto_00018"
      but the stored value is now "SWS_Crypto_00018" → not found → tries to CREATE
      → constraint violation because "SWS_Crypto_00018" already exists.

    Fix: remove "id" from the properties dict before the SET so the MERGE key
    is never overwritten.  Also remove "name" from Module nodes because Module
    has a UNIQUE constraint on `name`, not `id`, so we MERGE on name instead.
    """
    # Special case: Module nodes have a UNIQUE constraint on `name`, not `id`
    if label == "Module":
        _write_module_nodes(neo, nodes)
        return

    rows = []
    for node in nodes:
        props = dict(node["properties"])
        # Remove keys that are used as the MERGE key — letting SET overwrite
        # them would break idempotency on re-run (root cause of the bug above).
        props.pop("id",   None)   # MERGE key is node_id; don't let SET change it
        props.pop("name", None)   # avoid accidental overwrites for other labels
        rows.append({
            "node_id":          node["node_id"],
            "name":             node["properties"].get("name", node["node_id"]),
            "properties":       props,
            "ingested_at":      datetime.now(timezone.utc).isoformat(),
            "pipeline_version": settings.PIPELINE_VERSION,
        })

    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{label} {{id: row.node_id}})
    
    // 1. Initial creation — set all properties
    ON CREATE SET n = row.properties,
                  n.id   = row.node_id,
                  n.name = row.name,
                  n.ingested_at = row.ingested_at,
                  n.pipeline_version = row.pipeline_version

    // 2. Existing node — apply Governed Merge Policy
    //    We preserve authoritative 'rule_based' metadata and only allow 
    //    LLM extraction to enrich (not overwrite) missing fields.
    ON MATCH SET
        // Metadata Protection: Rule-based track ALWAYS wins
        n.confidence_score = CASE 
            WHEN n.extraction_method = 'rule_based' THEN n.confidence_score
            WHEN row.extraction_method = 'rule_based' THEN row.confidence_score
            ELSE coalesce(row.confidence_score, n.confidence_score) END,
            
        n.extraction_method = CASE
            WHEN n.extraction_method = 'rule_based' THEN 'rule_based'
            ELSE row.extraction_method END,
            
        n.ingested_at = CASE
            WHEN n.extraction_method = 'rule_based' THEN n.ingested_at
            ELSE row.ingested_at END,

        // Semantic Enrichment: Enrich missing names or summaries
        n.name = coalesce(n.name, row.name),
        n.summary = coalesce(n.summary, row.properties.summary),
        n.definition = coalesce(n.definition, row.properties.definition)
    """
    n = neo.run_batch(cypher, rows)
    log.info("  %s nodes: %d written", label, n)


def _write_module_nodes(neo: Neo4jClient, nodes: list[dict]) -> None:
    """Module nodes: UNIQUE constraint is on `name`, not `id`."""
    rows = []
    for node in nodes:
        props = dict(node["properties"])
        name = props.pop("name", node["node_id"])
        props.pop("id", None)
        rows.append({
            "name":             name,
            "node_id":          node["node_id"],
            "properties":       props,
            "ingested_at":      datetime.now(timezone.utc).isoformat(),
            "pipeline_version": settings.PIPELINE_VERSION,
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (n:Module {name: row.name})
    SET n += row.properties,
        n.id               = coalesce(n.id, row.node_id),
        n.ingested_at      = row.ingested_at,
        n.pipeline_version = row.pipeline_version
    """
    n = neo.run_batch(cypher, rows)
    log.info("  Module nodes: %d written", n)


def _write_config_param_nodes(neo: Neo4jClient, config_params: list[dict]) -> None:
    """Write ConfigParameter nodes from Stage 4 regex output (Issue 8 fix)."""
    if not config_params:
        log.info("  ConfigParameters: 0 (none provided)")
        return
    rows = []
    for cp in config_params:
        module = cp.get("module", "")
        name   = cp.get("name", cp.get("param_name", ""))
        node_id = f"cp_{module}_{name}".replace(" ", "_")
        rows.append({
            "node_id":          node_id,
            "name":             name,
            "module":           module,
            "value":            cp.get("value", ""),
            "value_type":       cp.get("value_type", ""),
            "source":           cp.get("source", ""),
            "ingested_at":      datetime.now(timezone.utc).isoformat(),
            "pipeline_version": settings.PIPELINE_VERSION,
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (cp:ConfigParameter {id: row.node_id})
    SET cp.name             = row.name,
        cp.module           = row.module,
        cp.value            = row.value,
        cp.value_type       = row.value_type,
        cp.source           = row.source,
        cp.ingested_at      = row.ingested_at,
        cp.pipeline_version = row.pipeline_version
    WITH cp, row
    WHERE row.module <> ''
    MATCH (m:Module {name: row.module})
    MERGE (m)-[:HAS_PARAMETER]->(cp)
    """
    n = neo.run_batch(cypher, rows)
    log.info("  ConfigParameter nodes: %d written", n)


def _write_config_param_nodes_from_llm(neo: Neo4jClient, nodes: list[dict]) -> None:
    """
    Write ConfigParameter nodes sourced from LLM entity extraction (Issue 9 fix).
    Uses composite ID (module + name) to avoid cross-module collisions.
    """
    rows = []
    for node in nodes:
        props = dict(node["properties"])
        module = props.get("module", "")
        name   = props.get("name", node["node_id"])
        node_id = f"cp_{module}_{name}".replace(" ", "_") if module else node["node_id"]
        props.pop("id",   None)
        props.pop("name", None)
        rows.append({
            "node_id":          node_id,
            "name":             name,
            "module":           module,
            "properties":       props,
            "ingested_at":      datetime.now(timezone.utc).isoformat(),
            "pipeline_version": settings.PIPELINE_VERSION,
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (cp:ConfigParameter {id: row.node_id})
    SET cp += row.properties,
        cp.id               = row.node_id,
        cp.name             = row.name,
        cp.module           = row.module,
        cp.ingested_at      = row.ingested_at,
        cp.pipeline_version = row.pipeline_version
    WITH cp, row
    WHERE row.module <> ''
    MATCH (m:Module {name: row.module})
    MERGE (m)-[:HAS_PARAMETER]->(cp)
    """
    n = neo.run_batch(cypher, rows)
    log.info("  ConfigParameter (LLM) nodes: %d written", n)


# ══════════════════════════════════════════════════════════════════════════════
# 9. Chunk → Entity MENTIONS edges
# ══════════════════════════════════════════════════════════════════════════════

def _write_mentions_edges(
    neo: Neo4jClient,
    chunks: list[dict],
    entity_data: dict,
) -> None:
    """
    For each requirement ID present in a chunk, create a MENTIONS edge
    from the Chunk node to the Requirement node.
    """
    rows = []
    for chunk in chunks:
        for req_id in chunk.get("req_ids_present", []):
            rows.append({
                "chunk_id": chunk["chunk_id"],
                "req_id":   f"req_{req_id}",
            })

    if not rows:
        log.info("  MENTIONS edges: 0 (no req IDs found in chunks)")
        return

    cypher = """
    UNWIND $rows AS row
    MATCH (c:Chunk {id: row.chunk_id})
    MATCH (r:Requirement {id: row.req_id})
    MERGE (c)-[:MENTIONS]->(r)
    """
    n = neo.run_batch(cypher, rows)
    log.info("  MENTIONS edges: %d written", n)


# ══════════════════════════════════════════════════════════════════════════════
# 10–12. All other relationships
# ══════════════════════════════════════════════════════════════════════════════

def _write_relationships(neo: Neo4jClient, relationships: list[dict]) -> tuple[int, list[str]]:
    """
    Write entity → entity relationships.
    Groups by relationship type and writes each type in one batch.

    Two-pass approach (Bug 1 fix):
      Pass 1 — OPTIONAL MATCH lookup to identify and log every missing node ID.
      Pass 2 — strict MATCH so Neo4j raises on any remaining mismatch instead
               of silently skipping edges.

    Returns:
        (total_written, skipped_rels) where skipped_rels is a list of
        human-readable strings describing each skipped edge.
    """
    by_type: dict[str, list[dict]] = defaultdict(list)
    for rel in relationships:
        by_type[rel["type"]].append(rel)

    total        = 0
    skipped_rels: list[str] = []

    for rel_type, rels in by_type.items():
        rows = [
            {
                "from_id": r["from_id"],
                "to_id":   r["to_id"],
                "props":   r.get("properties", {}),
            }
            for r in rels
        ]

        # ── Pass 1: identify missing node IDs ────────────────────────────────
        lookup_cypher = """
        UNWIND $rows AS row
        OPTIONAL MATCH (a {id: row.from_id})
        OPTIONAL MATCH (b {id: row.to_id})
        RETURN row.from_id AS from_id,
               row.to_id   AS to_id,
               a IS NULL   AS src_missing,
               b IS NULL   AS tgt_missing
        """
        try:
            lookup_rows = neo.run(lookup_cypher, rows=rows)
        except Exception as exc:
            log.warning("  %s edges: lookup query failed — skipping batch: %s", rel_type, exc)
            continue

        # Fix 4: track skipped pairs by (from_id, to_id) key to deduplicate
        # the OPTIONAL MATCH results.  When a node `id` property is shared
        # across multiple Neo4j labels the OPTIONAL MATCH returns one row per
        # matching node, so lookup_rows can have MORE entries than rows, causing
        # valid_rows to grow larger than rows and skipped_count to go negative.
        skipped_pairs:   set[tuple] = set()
        valid_pairs_map: dict[tuple, dict] = {}   # (from_id, to_id) → props

        for lr in lookup_rows:
            src_missing = lr.get("src_missing", False)
            tgt_missing = lr.get("tgt_missing", False)
            from_id     = lr["from_id"]
            to_id       = lr["to_id"]
            pair        = (from_id, to_id)

            if src_missing and pair not in valid_pairs_map:
                msg = f"{rel_type} edge skipped: source node '{from_id}' not found in graph"
                if msg not in skipped_rels:          # avoid duplicate warning lines
                    log.warning("  %s", msg)
                    skipped_rels.append(msg)
                skipped_pairs.add(pair)
            if tgt_missing and pair not in valid_pairs_map:
                msg = f"{rel_type} edge skipped: target node '{to_id}' not found in graph"
                if msg not in skipped_rels:
                    log.warning("  %s", msg)
                    skipped_rels.append(msg)
                skipped_pairs.add(pair)

            if not src_missing and not tgt_missing and pair not in valid_pairs_map:
                # Find the original props for this pair
                matched_props = next(
                    (r["props"] for r in rows if r["from_id"] == from_id and r["to_id"] == to_id),
                    {},
                )
                valid_pairs_map[pair] = matched_props

        # Remove any pairs that also appeared as missing (can happen with
        # multi-label nodes where one label match succeeds and another fails)
        for pair in skipped_pairs:
            valid_pairs_map.pop(pair, None)

        valid_rows = [
            {"from_id": from_id, "to_id": to_id, "props": props}
            for (from_id, to_id), props in valid_pairs_map.items()
        ]

        if not valid_rows:
            log.warning("  %s edges: 0 written (all %d skipped — node ID mismatch)", rel_type, len(rows))
            continue

        # ── Pass 2: strict MATCH write on validated rows only ─────────────────
        write_cypher = f"""
        UNWIND $rows AS row
        MATCH (a {{id: row.from_id}})
        MATCH (b {{id: row.to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += row.props
        """
        n = neo.run_batch(write_cypher, valid_rows)
        total += n
        # Fix 4: count from actual input rows, not from the (deduplicated) valid list
        skipped_count = len(rows) - len(valid_rows)
        if skipped_count > 0:
            log.warning(
                "  %s edges: %d written, %d skipped (node ID mismatch — see above)",
                rel_type, n, skipped_count,
            )
        else:
            log.info("  %s edges: %d written", rel_type, n)

    log.info("  Total relationship edges: %d written, %d skipped", total, len(skipped_rels))
    return total, skipped_rels


# ══════════════════════════════════════════════════════════════════════════════
# 13. Chunk embeddings (written separately — large payload)
# ══════════════════════════════════════════════════════════════════════════════

def _write_chunk_embeddings(neo: Neo4jClient, chunks: list[dict]) -> None:
    """Write primary and summary embeddings in smaller batches."""
    rows = [
        {
            "chunk_id":         c["chunk_id"],
            "embedding":        c["embedding"],
            "summary_embedding": c.get("summary_embedding", []),
        }
        for c in chunks
        if "embedding" in c
    ]

    cypher = """
    UNWIND $rows AS row
    MATCH (c:Chunk {id: row.chunk_id})
    SET c.embedding         = row.embedding,
        c.summary_embedding = row.summary_embedding
    """
    batch_size = 100
    total = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        with neo.session() as s:
            s.run(cypher, rows=batch)
        total += len(batch)
        if total % 500 == 0:
            log.info("  Embeddings: %d / %d written", total, len(rows))

    log.info("  Embeddings: %d written (primary + summary)", total)


# ══════════════════════════════════════════════════════════════════════════════
# 15. kNN SIMILAR_TO edges
# ══════════════════════════════════════════════════════════════════════════════

def _write_knn_edges(neo: Neo4jClient, chunks: list[dict]) -> None:
    """
    For each chunk, find top-K most similar chunks via the Neo4j vector index
    and create SIMILAR_TO edges with the similarity score.

    Runs after the vector index is created in Step 14.
    Batches chunk IDs to avoid N+1 round-trips (Issue 10 fix).
    """
    log.info(
        "Computing kNN SIMILAR_TO edges (k=%d, min_score=%.2f) ...",
        settings.KNN_TOP_K, settings.KNN_MIN_SCORE,
    )

    # Wait for vector index to be online
    _wait_for_vector_index(neo)

    chunk_ids = [c["chunk_id"] for c in chunks if "embedding" in c]
    if not chunk_ids:
        log.info("  SIMILAR_TO edges: 0 (no embedded chunks)")
        return

    # Process in batches of 200 to avoid memory pressure
    batch_size = 200
    total_edges = 0
    for i in range(0, len(chunk_ids), batch_size):
        batch = chunk_ids[i : i + batch_size]
        cypher = """
        UNWIND $chunk_ids AS chunk_id
        MATCH (c:Chunk {id: chunk_id})
        CALL db.index.vector.queryNodes(
            'chunk_embedding_index', $k, c.embedding
        ) YIELD node AS neighbor, score
        WHERE neighbor.id <> chunk_id
          AND score >= $min_score
        MERGE (c)-[r:SIMILAR_TO]->(neighbor)
        SET r.score = score
        RETURN count(*) AS n
        """
        try:
            result = neo.run(
                cypher,
                chunk_ids=batch,
                k=settings.KNN_TOP_K + 1,
                min_score=settings.KNN_MIN_SCORE,
            )
            total_edges += result[0].get("n", 0) if result else 0
        except Exception as exc:
            log.warning("kNN batch failed: %s", exc)
        log.info("  kNN: %d / %d chunks processed", min(i + batch_size, len(chunk_ids)), len(chunk_ids))

    log.info("  SIMILAR_TO edges: %d created", total_edges)


# ══════════════════════════════════════════════════════════════════════════════
# 16. Chunk → Module SOURCED_FROM edges
# ══════════════════════════════════════════════════════════════════════════════

def _write_chunk_module_edges(neo: Neo4jClient) -> None:
    """
    Create direct Chunk -[:SOURCED_FROM]-> Module edges.

    Rationale
    ---------
    Without this edge, GraphRAG answering "what does ComM require from the OS?"
    must traverse:
        vector search → Chunk → MENTIONS → Requirement → HAS_REQUIREMENT⁻¹ → Module

    With SOURCED_FROM the same query short-circuits to:
        vector search → Chunk → SOURCED_FROM → Module

    Derivation
    ----------
    A Chunk's module is determined by matching its `source` filename property
    against the DocumentRef nodes (which carry the filename→module mapping built
    in Stage 5 Track A).  We then follow the DocumentRef -[:SPECIFIES]-> Module
    edge that Track A also creates, giving us a fully graph-internal derivation
    that does not require re-reading Python-side data.

    Fallback: if no DocumentRef/SPECIFIES path exists (e.g. corpus without
    a Stage 0 module map), we match Module nodes whose HAS_REQUIREMENT
    requirements appear in the chunk's req_ids list.  This covers the common
    case without requiring a perfect corpus-analysis result.

    The edge carries `method` and `ingested_at` properties so the Evolution
    Agent can distinguish pipeline-derived shortcut edges from domain edges.

    This function is idempotent: MERGE guarantees no duplicate edges on re-run.
    """
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()

    # ── Path 1: Chunk.source → DocumentRef.filename → SPECIFIES → Module ─────
    cypher_via_docref = """
    MATCH (c:Chunk)
    MATCH (dr:DocumentRef {filename: c.source})-[:SPECIFIES]->(m:Module)
    MERGE (c)-[r:SOURCED_FROM]->(m)
    SET r.method     = 'docref_specifies',
        r.ingested_at = $ts
    RETURN count(r) AS n
    """
    try:
        result = neo.run(cypher_via_docref, ts=ts)
        n_docref = result[0]["n"] if result else 0
        log.info("  SOURCED_FROM edges (via DocumentRef): %d written", n_docref)
    except Exception as exc:
        log.warning("  SOURCED_FROM (via DocumentRef) failed: %s", exc)
        n_docref = 0

    # ── Path 2: Chunk req_ids → Requirement → HAS_REQUIREMENT⁻¹ → Module ─────
    # Covers chunks whose source file has no DocumentRef (e.g. non-AUTOSAR docs).
    # Fix (Issue 11): prefix rid with 'req_' to match stored Requirement IDs.
    cypher_via_reqs = """
    MATCH (c:Chunk)
    WHERE NOT (c)-[:SOURCED_FROM]->()      // only for chunks not already linked
      AND size(c.req_ids) > 0
    UNWIND [rid IN c.req_ids | 'req_' + rid] AS prefixed_rid
    MATCH (r:Requirement {id: prefixed_rid})<-[:HAS_REQUIREMENT]-(m:Module)
    MERGE (c)-[rel:SOURCED_FROM]->(m)
    SET rel.method      = 'requirement_inference',
        rel.ingested_at = $ts
    RETURN count(rel) AS n
    """
    try:
        result = neo.run(cypher_via_reqs, ts=ts)
        n_reqs = result[0]["n"] if result else 0
        log.info("  SOURCED_FROM edges (via Requirement inference): %d written", n_reqs)
    except Exception as exc:
        log.warning("  SOURCED_FROM (via Requirement inference) failed: %s", exc)
        n_reqs = 0

    # ── Path 3: Fallback — match unlinked chunks via filename ↔ Module name ───
    # Handles chunks with no req_ids AND no matching DocumentRef (Issue 5 fix).
    cypher_via_filename = """
    MATCH (c:Chunk)
    WHERE NOT (c)-[:SOURCED_FROM]->()
    MATCH (m:Module)
    WHERE c.source CONTAINS m.name
       OR m.name CONTAINS c.source
    MERGE (c)-[r:SOURCED_FROM]->(m)
    SET r.method      = 'filename_inference',
        r.ingested_at = $ts
    RETURN count(r) AS n
    """
    try:
        result = neo.run(cypher_via_filename, ts=ts)
        n_filename = result[0]["n"] if result else 0
        log.info("  SOURCED_FROM edges (via filename inference): %d written", n_filename)
    except Exception as exc:
        log.warning("  SOURCED_FROM (via filename inference) failed: %s", exc)
        n_filename = 0

    log.info(
        "  SOURCED_FROM edges total: %d (docref=%d, inferred=%d, filename=%d)",
        n_docref + n_reqs + n_filename, n_docref, n_reqs, n_filename,
    )


def _wait_for_vector_index(neo: Neo4jClient, timeout: int = 120) -> None:
    """
    Poll until the vector index is ONLINE or timeout.

    Fix: SHOW INDEXES is an admin command in Neo4j 5 and cannot be run
    inside a regular session.run() with YIELD appended inline.
    Must be called as a standalone statement; YIELD is implicit when
    called this way — the result columns are the yielded fields directly.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # SHOW INDEXES is valid as a standalone Cypher admin command.
            # Do NOT append YIELD on the same line — that causes SyntaxError.
            # The driver returns rows where each row is a dict of index fields.
            with neo.session() as s:
                rows = list(s.run("SHOW INDEXES"))
            for row in rows:
                d = dict(row)
                if d.get("name") == "chunk_embedding_index":
                    state = d.get("state", "")
                    if state == "ONLINE":
                        log.info("  Vector index is ONLINE")
                        return
                    else:
                        log.debug("  Vector index state: %s — waiting ...", state)
                        break
        except Exception as exc:
            log.debug("  _wait_for_vector_index poll error: %s", exc)
        time.sleep(3)
    log.warning("Vector index did not come ONLINE within %ds — kNN may fail", timeout)


# ══════════════════════════════════════════════════════════════════════════════
# Summary vector index
# ══════════════════════════════════════════════════════════════════════════════

def _create_summary_vector_index(neo: Neo4jClient) -> None:
    """Create a second vector index on summary_embedding for high-level queries."""
    try:
        cypher = f"""
        CREATE VECTOR INDEX chunk_summary_embedding_index IF NOT EXISTS
        FOR (c:Chunk) ON (c.summary_embedding)
        OPTIONS {{indexConfig: {{
            `vector.dimensions`: {settings.EMBED_DIM},
            `vector.similarity_function`: 'cosine'
        }}}}
        """
        with neo.session() as s:
            s.run(cypher)
        log.info("  Summary vector index created")
    except Exception as exc:
        log.warning("  Summary vector index creation failed (may already exist): %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Post-storage graph audit
# ══════════════════════════════════════════════════════════════════════════════

# _AUDIT_SYSTEM = """You are auditing a Neo4j knowledge graph built from AUTOSAR specifications.
#     Given a list of graph anomalies found by Cypher queries, classify each as:
#     - "expected": this is normal for AUTOSAR documents (e.g. some requirements have no text)
#     - "unexpected": this indicates a pipeline or data quality issue

#     Return ONLY a JSON array:
#     [{"anomaly": "<description>", "classification": "expected"|"unexpected", "reason": "<brief reason>"}, ...]
#     No markdown, no explanation."""

_AUDIT_SYSTEM = """You are a high-precision AUTOSAR knowledge graph audit and quality-control engine.

    You are auditing a Neo4j knowledge graph constructed from AUTOSAR specifications.

    You are given a list of graph anomalies detected using Cypher queries.

    Your task is to classify EACH anomaly as either:

    - "expected"
    = acceptable or explainable behavior commonly occurring in AUTOSAR specifications or extraction pipelines

    - "unexpected"
    = likely pipeline defect, extraction failure, canonicalization issue, graph corruption, ontology violation, or data-quality problem

    Return ONLY a valid JSON array in this exact format:

    [
    {
        "anomaly": "<description>",
        "classification": "expected" | "unexpected",
        "reason": "<brief technical reason>"
    }
    ]

    STRICT OUTPUT RULES:
    - Output ONLY valid JSON
    - No markdown
    - No explanations outside JSON
    - No comments
    - No extra keys
    - No trailing text

    AUDIT PRINCIPLES:

    1. DISTINGUISH DOCUMENT REALITY VS PIPELINE FAILURE

    Some anomalies are normal because AUTOSAR specifications are:
    - incomplete
    - unevenly structured
    - partially traceable
    - inconsistently formatted
    - distributed across multiple documents

    Do NOT classify these as pipeline failures unless evidence strongly suggests corruption.

    2. EXPECTED ANOMALIES — COMMON EXAMPLES

    Classify as "expected" when anomalies are plausibly caused by AUTOSAR document characteristics, including:

    - requirements without extracted body text
    - isolated requirements with no relationships
    - modules referenced but not fully defined
    - sparse traceability coverage
    - partially extracted tables
    - missing reverse trace links
    - glossary entities without edges
    - requirements appearing only once
    - vendor-specific extension inconsistencies
    - adaptive/classic platform asymmetry
    - low-degree nodes from annex/reference sections
    - intentionally standalone concepts

    3. UNEXPECTED ANOMALIES — PIPELINE FAILURE SIGNALS

    Classify as "unexpected" when anomalies indicate likely extraction or graph-quality problems, including:

    - malformed requirement IDs
    - duplicated canonical entities
    - impossible relationship types
    - ontology violations
    - invalid node labels
    - contradictory canonical names
    - massive orphan explosions
    - broken traceability chains caused by extraction
    - cyclic DERIVED_FROM chains when semantically invalid
    - self-referential relationships without justification
    - corrupted identifiers
    - malformed JSON-derived properties
    - extreme duplication
    - invalid AUTOSAR casing normalization
    - conflicting entity merges
    - relationships using disallowed ontology types
    - graph schema inconsistency
    - impossible CP/AP architectural mappings

    4. RELATIONSHIP AUDITING RULES

    Validate:
    - semantic plausibility
    - ontology consistency
    - relationship directionality
    - canonical entity naming
    - AUTOSAR architectural correctness

    Example:
    - CALLS between unrelated modules without API evidence → unexpected
    - isolated node without edges → often expected

    5. CANONICALIZATION RULES

    Unexpected indicators include:
    - ComM + CommunicationManager both existing independently after canonicalization
    - Module_ComM remaining after prefix cleanup
    - conflicting aliases merged incorrectly
    - duplicated requirement nodes differing only by formatting

    6. TRACEABILITY RULES

    Expected:
    - incomplete SRS ↔ SWS coverage
    - sparse trace links

    Unexpected:
    - malformed trace chains
    - invalid trace directionality
    - impossible requirement hierarchy cycles

    7. CONFIDENCE / CONSERVATIVE BEHAVIOR

    When uncertain:
    prefer "expected"

    Only classify as "unexpected" when there is strong evidence of:
    - extraction failure
    - graph corruption
    - ontology inconsistency
    - canonicalization error
    - invalid relationship semantics

    8. REASON FIELD RULES

    The "reason" field must:
    - be concise
    - be technical
    - explain WHY the anomaly is expected or unexpected
    - avoid speculation
    - avoid unnecessary verbosity

    GOOD EXAMPLES:
    - "Sparse traceability is common in AUTOSAR annex sections."
    - "Duplicate canonical entities indicate failed entity resolution."
    - "Malformed requirement ID suggests extraction corruption."

    IMPORTANT:
    Evaluate anomalies ONLY using:
    - AUTOSAR semantics
    - graph integrity expectations
    - ontology consistency
    - realistic specification structure

    Do NOT hallucinate undocumented failures.

    Return ONLY the JSON array.
    """


def _run_graph_audit(neo: Neo4jClient) -> None:
    """Run quality audit queries and classify anomalies with LLM."""
    from utils.llm_client import call_llm_json

    log.info("Running post-storage graph audit ...")
    anomalies: list[str] = []

    audit_queries = [
        ("Isolated nodes (no relationships)",
         "MATCH (n) WHERE NOT (n)--() AND NOT n:Document RETURN labels(n)[0] AS label, count(n) AS cnt"),
        ("Requirement nodes with no raw_text (structural) or name (semantic)",
         "MATCH (r:Requirement) WHERE (r.raw_text IS NULL OR r.raw_text = '') AND (r.name IS NULL OR r.name = '') RETURN count(r) AS cnt"),
        ("Module nodes with no HAS_REQUIREMENT edges",
         "MATCH (m:Module) WHERE NOT (m)-[:HAS_REQUIREMENT]->() RETURN count(m) AS cnt"),
        ("Chunks with no embedding",
         "MATCH (c:Chunk) WHERE c.embedding IS NULL RETURN count(c) AS cnt"),
        ("Chunks with no Document link",
         "MATCH (c:Chunk) WHERE NOT ()-[:HAS_CHUNK]->(c) RETURN count(c) AS cnt"),
        # ── Provenance audit (ASEI readiness) ─────────────────────────────────
        ("Requirement nodes missing ingested_at (provenance gap)",
         "MATCH (r:Requirement) WHERE r.ingested_at IS NULL RETURN count(r) AS cnt"),
        ("Requirement nodes missing confidence_score (provenance gap)",
         "MATCH (r:Requirement) WHERE r.confidence_score IS NULL RETURN count(r) AS cnt"),
        ("Chunks with no SOURCED_FROM module edge (shortcut coverage)",
         "MATCH (c:Chunk) WHERE NOT (c)-[:SOURCED_FROM]->(:Module) RETURN count(c) AS cnt"),
    ]

    for description, cypher in audit_queries:
        try:
            with neo.session() as s:
                rows = list(s.run(cypher))
            if rows:
                row = dict(rows[0])
                count = row.get("cnt", 0)
                if count and count > 0:
                    anomalies.append(f"{description}: {count}")
        except Exception as exc:
            log.debug("Audit query failed (%s): %s", description, exc)

    if not anomalies:
        log.info("  Graph audit: no anomalies found")
        return

    log.info("  Graph audit found %d anomaly type(s):", len(anomalies))
    for a in anomalies:
        log.info("    - %s", a)

    # Ask LLM to classify
    try:
        result = call_llm_json(
            system=_AUDIT_SYSTEM,
            user=f"Anomalies found:\n" + "\n".join(f"- {a}" for a in anomalies),
        )
        if result and isinstance(result, list):
            unexpected = [item for item in result if item.get("classification") == "unexpected"]
            if unexpected:
                log.warning("  Graph audit — UNEXPECTED anomalies (manual review recommended):")
                for item in unexpected:
                    log.warning("    [UNEXPECTED] %s — %s", item.get("anomaly"), item.get("reason"))
            else:
                log.info("  Graph audit: all anomalies classified as expected")
    except Exception as exc:
        log.debug("Audit LLM classification failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Cross-Track Reconciliation
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_document_references(neo: Neo4jClient) -> None:
    """
    Non-destructive Reconciliation Bridge.
    Matches semantic DocumentRef nodes to physical Document nodes.
    If matched, emits a (DocumentRef)-[:RESOLVES_TO]->(Document) edge.
    This bridges the semantic and structural worlds without altering semantic meaning.
    """
    cypher = """
    MATCH (ref:DocumentRef), (doc:Document)
    WHERE NOT (ref)-[:RESOLVES_TO]->(doc)
    WITH ref, doc,
         toLower(replace(doc.filename, '.pdf', '')) AS clean_doc,
         toLower(replace(replace(coalesce(ref.name, ''), ' ', '_'), '-', '_')) AS clean_ref
    WHERE 
        (ref.filename IS NOT NULL AND ref.filename = doc.filename)
        OR (clean_ref <> '' AND clean_doc CONTAINS clean_ref)
        OR (clean_ref <> '' AND clean_ref CONTAINS clean_doc)
    WITH ref, doc
    MERGE (ref)-[r:RESOLVES_TO]->(doc)
    SET r.confidence = 0.8,
        r.method = 'stage8_reconciliation'
    RETURN count(r) AS matched
    """
    try:
        res = neo.run(cypher)
        if res and res[0]["matched"] > 0:
            log.info("  Reconciliation: emitted %d RESOLVES_TO edges for DocumentRefs", res[0]["matched"])
    except Exception as exc:
        log.warning("  Reconciliation failed: %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════

def _print_summary(neo: Neo4jClient) -> None:
    log.info("\n" + "=" * 60)
    log.info(" Neo4j KG Summary")
    log.info("=" * 60)
    for label in [
        "Document", "Chunk", "Requirement", "Module",
        "ConfigParameter", "DocumentRef", "Entity", "Concept",
    ]:
        try:
            n = neo.node_count(label)
            if n > 0:
                log.info("  %-20s %d nodes", label, n)
        except Exception:
            pass

    try:
        total_rels = neo.relationship_count()
        log.info("  %-20s %d", "Total relationships", total_rels)
    except Exception:
        pass

    # ── Shortcut edge coverage ─────────────────────────────────────────────────
    try:
        n_sf = neo.relationship_count("SOURCED_FROM")
        log.info("  %-20s %d  (Chunk→Module shortcut)", "SOURCED_FROM", n_sf)
    except Exception:
        pass

    # ── Provenance coverage ────────────────────────────────────────────────────
    try:
        result = neo.run(
            "MATCH (r:Requirement) WHERE r.ingested_at IS NOT NULL "
            "RETURN count(r) AS n"
        )
        n_prov = result[0]["n"] if result else 0
        result_total = neo.run("MATCH (r:Requirement) RETURN count(r) AS n")
        n_total = result_total[0]["n"] if result_total else 0
        if n_total > 0:
            pct = 100.0 * n_prov / n_total
            log.info(
                "  Provenance coverage   %.0f%% of Requirement nodes have ingested_at",
                pct,
            )
    except Exception:
        pass

    log.info("=" * 60)
    log.info(" Browser: http://localhost:7474")
    log.info(" Useful Cypher snippets:")
    log.info("   // GraphRAG shortcut — what modules does this chunk belong to?")
    log.info("   MATCH (c:Chunk {id: '<id>'})-[:SOURCED_FROM]->(m:Module) RETURN m.name")
    log.info("   // Provenance check — find low-confidence nodes")
    log.info("   MATCH (n) WHERE n.confidence_score < 0.9 RETURN labels(n)[0], count(n)")
    log.info("   // Module coverage — chunks per module via shortcut")
    log.info("   MATCH (m:Module)<-[:SOURCED_FROM]-(c:Chunk) RETURN m.name, count(c) ORDER BY count(c) DESC")
    log.info("=" * 60)