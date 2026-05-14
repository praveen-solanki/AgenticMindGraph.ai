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

import time
from collections import defaultdict
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

        # ── 1. Document nodes ─────────────────────────────────────────────────
        _write_document_nodes(neo, pages)

        # ── 2 & 3. Chunk nodes + sequential edges ─────────────────────────────
        _write_chunk_nodes(neo, chunks)
        _write_chunk_sequential_edges(neo, chunks)

        # ── 4–8. Entity nodes ─────────────────────────────────────────────────
        nodes = entity_data["nodes"]
        _write_entity_nodes_by_label(neo, nodes)

        # ── 9. Chunk → Entity MENTIONS edges ──────────────────────────────────
        _write_mentions_edges(neo, chunks, entity_data)

        # ── 10–12. All other relationships ────────────────────────────────────
        _write_relationships(neo, entity_data["relationships"])

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
                "id":       Path(src).stem,
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
        doc_id = Path(c["source"]).stem
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
        c.req_ids         = row.req_ids
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
    "documentref":     "DocumentRef",
    "document_ref":    "DocumentRef",
    "configparameter": "ConfigParameter",
    "config_parameter":"ConfigParameter",
    "standardref":     "StandardRef",
    "standard_ref":    "StandardRef",
}

def _normalise_label(label: str) -> str:
    """
    Normalise label casing.
    LLMGraphTransformer sometimes returns e.g. "Documentref" instead of
    "DocumentRef". Map known variants to the canonical form so all nodes
    of the same type land in one Neo4j label bucket.
    """
    return _LABEL_ALIASES.get(label.lower(), label)


def _write_entity_nodes_by_label(neo: Neo4jClient, nodes: list[dict]) -> None:
    by_label: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        canonical_label = _normalise_label(node["label"])
        node = dict(node, label=canonical_label)   # normalise in-place copy
        by_label[canonical_label].append(node)

    for label, label_nodes in by_label.items():
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
            "node_id":    node["node_id"],
            "name":       node["properties"].get("name", node["node_id"]),
            "properties": props,
        })

    cypher = f"""
    UNWIND $rows AS row
    MERGE (n:{label} {{id: row.node_id}})
    SET n += row.properties,
        n.id   = row.node_id,
        n.name = row.name
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
            "name":       name,
            "node_id":    node["node_id"],
            "properties": props,
        })
    cypher = """
    UNWIND $rows AS row
    MERGE (n:Module {name: row.name})
    SET n += row.properties,
        n.id = row.node_id
    """
    n = neo.run_batch(cypher, rows)
    log.info("  Module nodes: %d written", n)


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

def _write_relationships(neo: Neo4jClient, relationships: list[dict]) -> None:
    """
    Write entity → entity relationships.
    Groups by relationship type and writes each type in one batch.
    Uses generic node matching by id property.
    """
    by_type: dict[str, list[dict]] = defaultdict(list)
    for rel in relationships:
        by_type[rel["type"]].append(rel)

    total = 0
    for rel_type, rels in by_type.items():
        rows = [
            {
                "from_id": r["from_id"],
                "to_id":   r["to_id"],
                "props":   r.get("properties", {}),
            }
            for r in rels
        ]
        cypher = f"""
        UNWIND $rows AS row
        OPTIONAL MATCH (a {{id: row.from_id}})
        OPTIONAL MATCH (b {{id: row.to_id}})
        WITH a, b, row WHERE a IS NOT NULL AND b IS NOT NULL
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += row.props
        """
        n = neo.run_batch(cypher, rows)
        total += n
        log.info("  %s edges: %d written", rel_type, n)

    log.info("  Total relationship edges: %d", total)


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
    """
    log.info(
        "Computing kNN SIMILAR_TO edges (k=%d, min_score=%.2f) ...",
        settings.KNN_TOP_K, settings.KNN_MIN_SCORE,
    )

    # Wait for vector index to be online
    _wait_for_vector_index(neo)

    knn_cypher = """
    MATCH (c:Chunk {id: $chunk_id})
    CALL db.index.vector.queryNodes(
        'chunk_embedding_index',
        $k,
        c.embedding
    ) YIELD node AS neighbor, score
    WHERE neighbor.id <> $chunk_id
      AND score >= $min_score
    MERGE (c)-[r:SIMILAR_TO]->(neighbor)
    SET r.score = score
    RETURN count(*) AS n
    """

    total_edges = 0
    for i, chunk in enumerate(chunks):
        if "embedding" not in chunk:
            continue
        try:
            result = neo.run(
                knn_cypher,
                chunk_id=chunk["chunk_id"],
                k=settings.KNN_TOP_K + 1,   # +1 because the chunk itself may appear
                min_score=settings.KNN_MIN_SCORE,
            )
            if result:
                total_edges += result[0].get("n", 0)
        except Exception as exc:
            log.debug("kNN failed for chunk %s: %s", chunk["chunk_id"], exc)

        if (i + 1) % 200 == 0:
            log.info("  kNN: %d / %d chunks processed", i + 1, len(chunks))

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
    # Uses ANY(id IN c.req_ids WHERE ...) to avoid an intermediate node explosion.
    cypher_via_reqs = """
    MATCH (c:Chunk)
    WHERE NOT (c)-[:SOURCED_FROM]->()      // only for chunks not already linked
      AND size(c.req_ids) > 0
    UNWIND c.req_ids AS rid
    MATCH (r:Requirement {id: rid})<-[:HAS_REQUIREMENT]-(m:Module)
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

    log.info(
        "  SOURCED_FROM edges total: %d (docref=%d, inferred=%d)",
        n_docref + n_reqs, n_docref, n_reqs,
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

_AUDIT_SYSTEM = """You are auditing a Neo4j knowledge graph built from AUTOSAR specifications.
Given a list of graph anomalies found by Cypher queries, classify each as:
  - "expected": this is normal for AUTOSAR documents (e.g. some requirements have no text)
  - "unexpected": this indicates a pipeline or data quality issue

Return ONLY a JSON array:
[{"anomaly": "<description>", "classification": "expected"|"unexpected", "reason": "<brief reason>"}, ...]
No markdown, no explanation."""


def _run_graph_audit(neo: Neo4jClient) -> None:
    """Run quality audit queries and classify anomalies with LLM."""
    from utils.llm_client import call_llm_json

    log.info("Running post-storage graph audit ...")
    anomalies: list[str] = []

    audit_queries = [
        ("Isolated nodes (no relationships)",
         "MATCH (n) WHERE NOT (n)--() AND NOT n:Document RETURN labels(n)[0] AS label, count(n) AS cnt"),
        ("Requirement nodes with no raw_text",
         "MATCH (r:Requirement) WHERE r.raw_text IS NULL OR r.raw_text = '' RETURN count(r) AS cnt"),
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