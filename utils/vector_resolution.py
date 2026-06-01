"""
utils/vector_resolution.py
============================
Graph-native vector search for entity resolution.

Replaces the O(N²) dense matrix approach in stage6_resolve.py with
Neo4j's native vector index for scalable nearest-neighbor entity matching.

Architecture:
  1. Upload entity name embeddings to temporary EntityCandidate nodes in Neo4j
  2. Create a vector index on EntityCandidate.name_embedding
  3. For each candidate, query nearest neighbors via db.index.vector.queryNodes()
  4. Apply same-label and antonym guards
  5. Return merge candidates for Union-Find clustering
  6. Clean up temporary nodes after resolution

This scales to 100K+ entities with O(N log N) performance vs the previous
O(N²) Python matrix multiplication + for-loop approach.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from utils.logger import get_logger
from utils.neo4j_client import Neo4jClient
from config import settings

log = get_logger("vector_resolution")

# ── Constants ─────────────────────────────────────────────────────────────────

_ENTITY_VECTOR_INDEX = "entity_name_embedding_index"
_CANDIDATE_LABEL = "EntityCandidate"
_TOP_K = 10  # Nearest neighbors to check per entity


def create_entity_vector_index(neo: Neo4jClient) -> None:
    """Create a vector index on EntityCandidate.name_embedding for resolution."""
    cypher = f"""
    CREATE VECTOR INDEX {_ENTITY_VECTOR_INDEX} IF NOT EXISTS
    FOR (n:{_CANDIDATE_LABEL}) ON (n.name_embedding)
    OPTIONS {{indexConfig: {{
      `vector.dimensions`: $dim,
      `vector.similarity_function`: 'cosine'
    }}}}
    """
    try:
        neo.run(cypher, dim=settings.EMBED_DIM)
        log.info("Entity vector index created (dim=%d)", settings.EMBED_DIM)
    except Exception as exc:
        log.warning("Entity vector index creation failed (may already exist): %s", exc)


def drop_entity_vector_index(neo: Neo4jClient) -> None:
    """Drop the temporary entity vector index after resolution."""
    try:
        neo.run(f"DROP INDEX {_ENTITY_VECTOR_INDEX} IF EXISTS")
    except Exception:
        pass


def upload_entity_candidates(
    neo: Neo4jClient,
    nodes: list[dict],
    embeddings: list[list[float]],
) -> None:
    """
    Upload entity candidates with their name embeddings to Neo4j as
    temporary EntityCandidate nodes for vector-indexed resolution.
    """
    rows = []
    for i, node in enumerate(nodes):
        rows.append({
            "candidate_id": i,
            "node_id":      node["node_id"],
            "name":         str(node["properties"].get("name", node["node_id"])),
            "label":        node.get("label", "Entity"),
            "embedding":    embeddings[i],
        })

    cypher = f"""
    UNWIND $rows AS row
    CREATE (n:{_CANDIDATE_LABEL} {{
        candidate_id:   row.candidate_id,
        node_id:        row.node_id,
        name:           row.name,
        entity_label:   row.label,
        name_embedding: row.embedding
    }})
    """
    neo.run_batch(cypher, rows)
    log.info("  Uploaded %d entity candidates to Neo4j", len(rows))


def find_similar_entities(
    neo: Neo4jClient,
    nodes: list[dict],
    embeddings: list[list[float]],
    threshold: float,
    antonym_check_fn: Any = None,
) -> list[tuple[int, int, float]]:
    """
    Use Neo4j vector index to find similar entity pairs.

    Returns list of (idx_a, idx_b, similarity_score) tuples where
    similarity >= threshold and same-label + antonym guards pass.

    This replaces the O(N²) dense matrix approach with O(N * K) where
    K = _TOP_K nearest neighbors per entity.
    """
    pairs: list[tuple[int, int, float]] = []
    seen_pairs: set[tuple[int, int]] = set()

    # Query nearest neighbors for each candidate
    cypher = f"""
    CALL db.index.vector.queryNodes('{_ENTITY_VECTOR_INDEX}', $top_k, $embedding)
    YIELD node AS candidate, score
    WHERE candidate.candidate_id <> $self_id
      AND score >= $threshold
      AND candidate.entity_label = $label
    RETURN candidate.candidate_id AS neighbor_id,
           candidate.name AS neighbor_name,
           score
    ORDER BY score DESC
    """

    for i, node in enumerate(nodes):
        try:
            results = neo.run(
                cypher,
                top_k=_TOP_K,
                embedding=embeddings[i],
                self_id=i,
                threshold=threshold,
                label=node.get("label", "Entity"),
            )
        except Exception as exc:
            # Vector index may not be ready yet (async build)
            if i == 0:
                log.warning("Vector search failed (index may be building): %s", exc)
            continue

        name_i = str(node["properties"].get("name", ""))
        for row in results:
            j = row["neighbor_id"]
            score = row["score"]

            # Deduplicate: only keep (min, max) pair
            pair_key = (min(i, j), max(i, j))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)

            # Antonym guard
            if antonym_check_fn:
                name_j = row["neighbor_name"]
                if antonym_check_fn(name_i, name_j):
                    continue

            pairs.append((i, j, score))

    log.info("  Vector search found %d candidate pairs (threshold=%.2f)", len(pairs), threshold)
    return pairs


def cleanup_candidates(neo: Neo4jClient) -> None:
    """Remove all temporary EntityCandidate nodes after resolution."""
    try:
        neo.run(f"MATCH (n:{_CANDIDATE_LABEL}) DETACH DELETE n")
        log.info("  Cleaned up temporary EntityCandidate nodes")
    except Exception as exc:
        log.warning("Candidate cleanup failed: %s", exc)


def resolve_entities_via_vector_index(
    neo: Neo4jClient,
    nodes: list[dict],
    antonym_check_fn: Any = None,
    llm_pick_canonical_fn: Any = None,
) -> tuple[list[dict], dict[str, str]]:
    """
    Full graph-native entity resolution pipeline:
    1. Embed entity names
    2. Upload to Neo4j as temporary nodes
    3. Create vector index
    4. Query nearest neighbors
    5. Cluster with Union-Find
    6. Clean up

    Args:
        neo: Neo4jClient instance
        nodes: List of entity node dicts
        antonym_check_fn: Function(name_a, name_b) -> bool
        llm_pick_canonical_fn: Function(names) -> str | None

    Returns:
        (canonical_nodes, remap_dict)
    """
    if len(nodes) < 2:
        return nodes, {}

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        log.warning("sentence-transformers not installed — skipping vector resolution")
        return nodes, {}

    names = [str(n["properties"].get("name", n["node_id"])) for n in nodes]

    # Step 1: Embed entity names
    log.info("  Vector resolution: embedding %d entity names ...", len(names))
    model = SentenceTransformer(settings.EMBED_MODEL)
    embeddings_np = model.encode(
        names,
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=False,
    )
    embeddings = [e.tolist() for e in embeddings_np]

    # Step 2: Upload candidates to Neo4j
    upload_entity_candidates(neo, nodes, embeddings)

    # Step 3: Create vector index
    create_entity_vector_index(neo)

    # Step 4: Wait briefly for index to become available (Neo4j builds async)
    import time
    time.sleep(2)

    # Step 5: Find similar pairs via vector search
    threshold = settings.ENTITY_RESOLUTION_THRESHOLD
    pairs = find_similar_entities(
        neo, nodes, embeddings, threshold, antonym_check_fn
    )

    # Step 6: Cluster with Union-Find
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    for i, j, score in pairs:
        union(i, j)

    # Group by cluster root
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(nodes)):
        clusters[find(i)].append(i)

    remap: dict[str, str] = {}
    canonical_nodes: list[dict] = []

    for root, members in clusters.items():
        if len(members) == 1:
            canonical_nodes.append(nodes[members[0]])
            continue

        # Pick canonical name
        member_names = [names[i] for i in members]
        canonical_name = (
            (llm_pick_canonical_fn(member_names) if llm_pick_canonical_fn else None)
            or max(set(member_names), key=member_names.count)
        )
        label = nodes[members[0]].get("label", "Entity")
        safe = re.sub(r"\s+", "_", canonical_name.lower())
        safe = re.sub(r"[^a-z0-9_]", "", safe)[:60]
        canonical_id = f"{label.lower()}_{safe}"

        # Merge properties
        merged_props: dict = {}
        all_aliases: list[str] = []
        for idx in members:
            merged_props.update(nodes[idx]["properties"])
            n = names[idx]
            if n not in all_aliases:
                all_aliases.append(n)

        merged_props["name"] = canonical_name
        merged_props["aliases"] = all_aliases

        canonical_nodes.append({
            "node_id":    canonical_id,
            "label":      label,
            "properties": merged_props,
        })

        for idx in members:
            old_id = nodes[idx]["node_id"]
            if old_id != canonical_id:
                remap[old_id] = canonical_id

    # Step 7: Cleanup temporary nodes
    cleanup_candidates(neo)
    drop_entity_vector_index(neo)

    return canonical_nodes, remap
