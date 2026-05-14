"""
agents/conflict_agent.py
========================
ASEI Conflict Agent — detects and records contradictions in the KG.

Responsibilities
----------------
1. Structural conflict detection (pure Cypher, no LLM):
   - Bidirectional dependency loops (A DEPENDS_ON B AND B DEPENDS_ON A)
   - Self-referential REFERENCES edges (requirement cites itself)
   - Duplicate HAS_REQUIREMENT edges from >1 module to the same requirement
     (a requirement allocated to two different modules simultaneously)

2. Semantic conflict detection (LLM-assisted):
   - For pairs of Entity/Concept nodes that share a strong SIMILAR_TO path
     but carry contradictory textual definitions, the LLM classifies whether
     the contradiction is genuine or just terminology variation.

3. For each confirmed conflict, write a CONTRADICTS edge to Neo4j with:
   - `conflict_type`  : 'structural' | 'semantic'
   - `evidence`       : brief human-readable description
   - `confidence`     : 0.0–1.0
   - `detected_at`    : ISO-8601 timestamp
   - `agent_version`  : settings.PIPELINE_VERSION

4. Return a ConflictReport for the orchestrator.

Design notes
------------
- Structural detection: O(E) Cypher passes — fast, zero LLM cost.
- Semantic detection: batched LLM calls, bounded by ASEI_CONFLICT_SEMANTIC_LIMIT.
- Idempotent: existing CONTRADICTS edges are refreshed (MERGE + SET), not duplicated.
- The CONTRADICTS relationship type is already in settings.ALLOWED_RELATIONSHIPS
  (LLM extraction uses it too), so no schema change needed.

Run standalone:
    python -m agents.conflict_agent
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm_json
from utils.neo4j_client import Neo4jClient

log = get_logger("conflict_agent")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ConflictReport:
    run_at:               str       = ""
    structural_conflicts: int       = 0
    semantic_conflicts:   int       = 0
    total_conflicts:      int       = 0
    conflict_pairs:       list[dict] = field(default_factory=list)
    errors:               list[str]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":               self.run_at,
            "structural_conflicts": self.structural_conflicts,
            "semantic_conflicts":   self.semantic_conflicts,
            "total_conflicts":      self.total_conflicts,
            "conflict_pairs":       self.conflict_pairs[:50],  # cap for logging
            "errors":               self.errors,
        }


# ── LLM prompt for semantic conflict classification ───────────────────────────

_CONFLICT_SYSTEM = """\
You are a knowledge graph quality analyst for AUTOSAR technical specifications.

You are given two concept definitions that appear semantically similar (high
embedding cosine similarity) but may or may not be contradictory.

Classify the relationship as one of:
  - "synonym"      : same concept, different wording → should be merged
  - "contradiction": genuinely opposing or mutually exclusive definitions
  - "complementary": related but non-contradictory — both are correct

Return ONLY valid JSON, no markdown, no explanation:
{
  "classification": "synonym" | "contradiction" | "complementary",
  "confidence":     <float 0.0-1.0>,
  "evidence":       "<one sentence explaining the classification>"
}
"""


# ── Main agent entry point ────────────────────────────────────────────────────

def run(neo: Neo4jClient | None = None) -> ConflictReport:
    """
    Execute one full conflict detection cycle.

    Args:
        neo: Optional Neo4jClient; created internally if not provided.

    Returns:
        ConflictReport with conflict counts and pairs.
    """
    report   = ConflictReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Conflict Agent: starting detection")

        # ── Structural pass (Cypher only) ─────────────────────────────────────
        struct_pairs = _detect_structural_conflicts(neo, report)
        report.structural_conflicts = len(struct_pairs)
        _write_conflict_edges(neo, struct_pairs, conflict_type="structural")

        # ── Semantic pass (LLM-assisted) ──────────────────────────────────────
        sem_pairs = _detect_semantic_conflicts(neo, report)
        report.semantic_conflicts = len(sem_pairs)
        _write_conflict_edges(neo, sem_pairs, conflict_type="semantic")

        # ── Aggregate ─────────────────────────────────────────────────────────
        all_pairs = struct_pairs + sem_pairs
        report.total_conflicts = len(all_pairs)
        report.conflict_pairs  = [
            {"from": p["from_id"], "to": p["to_id"],
             "type": p["conflict_type"], "evidence": p["evidence"]}
            for p in all_pairs
        ]

        log.info(
            "Conflict Agent complete: %d conflicts (structural=%d, semantic=%d)",
            report.total_conflicts,
            report.structural_conflicts,
            report.semantic_conflicts,
        )

    except Exception as exc:
        msg = f"Conflict Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


# ══════════════════════════════════════════════════════════════════════════════
# Structural conflict detection
# ══════════════════════════════════════════════════════════════════════════════

def _detect_structural_conflicts(
    neo: Neo4jClient,
    report: ConflictReport,
) -> list[dict]:
    """
    Pure-Cypher structural anomaly detection. Returns list of conflict dicts.
    Each dict has: from_id, to_id, conflict_type, evidence, confidence.
    """
    pairs: list[dict] = []
    now = _now_iso()

    # ── 1. Bidirectional dependency loops (A DEPENDS_ON B AND B DEPENDS_ON A) ─
    q_bidir = """
    MATCH (a)-[:DEPENDS_ON]->(b)-[:DEPENDS_ON]->(a)
    WHERE elementId(a) < elementId(b)
    RETURN a.id AS from_id, b.id AS to_id,
           a.name AS from_name, b.name AS to_name
    LIMIT $limit
    """
    try:
        rows = neo.run(q_bidir, limit=settings.ASEI_CONFLICT_STRUCT_LIMIT)
        for r in rows:
            pairs.append({
                "from_id":      r["from_id"],
                "to_id":        r["to_id"],
                "conflict_type": "structural",
                "evidence":     (
                    f"Bidirectional DEPENDS_ON loop: "
                    f"'{r['from_name']}' and '{r['to_name']}' each depend on the other"
                ),
                "confidence":   1.0,
            })
        log.info("  Structural: %d bidirectional dependency loops", len(rows))
    except Exception as exc:
        msg = f"Bidir loop detection failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)

    # ── 2. Self-referential REFERENCES edges ──────────────────────────────────
    q_self = """
    MATCH (r:Requirement)-[:REFERENCES]->(r)
    RETURN r.id AS node_id, r.name AS name
    LIMIT $limit
    """
    try:
        rows = neo.run(q_self, limit=settings.ASEI_CONFLICT_STRUCT_LIMIT)
        for r in rows:
            pairs.append({
                "from_id":      r["node_id"],
                "to_id":        r["node_id"],
                "conflict_type": "structural",
                "evidence":     f"Self-referential REFERENCES: requirement '{r['name']}' cites itself",
                "confidence":   1.0,
            })
        log.info("  Structural: %d self-referential REFERENCES", len(rows))
    except Exception as exc:
        msg = f"Self-reference detection failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)

    # ── 3. Requirement allocated to >1 module (dual-allocation conflict) ───────
    q_dual = """
    MATCH (m1:Module)-[:HAS_REQUIREMENT]->(r:Requirement)<-[:HAS_REQUIREMENT]-(m2:Module)
    WHERE elementId(m1) < elementId(m2)
    RETURN r.id AS req_id, r.name AS req_name,
           m1.name AS mod1, m2.name AS mod2
    LIMIT $limit
    """
    try:
        rows = neo.run(q_dual, limit=settings.ASEI_CONFLICT_STRUCT_LIMIT)
        for r in rows:
            pairs.append({
                "from_id":      r["req_id"],
                "to_id":        r["req_id"],   # conflict is on the requirement itself
                "conflict_type": "structural",
                "evidence":     (
                    f"Dual-allocation: requirement '{r['req_name']}' is claimed by "
                    f"both module '{r['mod1']}' and '{r['mod2']}'"
                ),
                "confidence":   0.90,
            })
        log.info("  Structural: %d dual-allocation conflicts", len(rows))
    except Exception as exc:
        msg = f"Dual-allocation detection failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)

    return pairs


# ══════════════════════════════════════════════════════════════════════════════
# Semantic conflict detection (LLM-assisted)
# ══════════════════════════════════════════════════════════════════════════════

def _detect_semantic_conflicts(
    neo: Neo4jClient,
    report: ConflictReport,
) -> list[dict]:
    """
    Find Entity/Concept pairs that are highly similar (via SIMILAR_TO on their
    source Chunks) but have textually contradictory definitions.
    Uses LLM to classify each candidate pair.
    """
    limit   = settings.ASEI_CONFLICT_SEMANTIC_LIMIT
    pairs: list[dict] = []

    # Find Concept pairs whose source chunks are highly similar.
    # Neo4j 5+: relationship properties need a named rel variable, not inline map syntax.
    # Use elementId() instead of the deprecated id() function.
    q_candidates = """
    MATCH (ca:Concept)<-[:MENTIONS]-(ch1:Chunk)-[sim:SIMILAR_TO]-(ch2:Chunk)-[:MENTIONS]->(cb:Concept)
    WHERE elementId(ca) < elementId(cb)
      AND sim.score >= $min_score
      AND ca.name IS NOT NULL
      AND cb.name IS NOT NULL
      AND ca.name <> cb.name
    RETURN ca.id AS from_id, cb.id AS to_id,
           ca.name AS from_name, cb.name AS to_name,
           coalesce(ca.definition, ca.text, ca.summary, \'\') AS from_def,
           coalesce(cb.definition, cb.text, cb.summary, \'\') AS to_def,
           sim.score AS score
    ORDER BY sim.score DESC
    LIMIT $limit
    """
    try:
        rows = neo.run(
            q_candidates,
            min_score=settings.ASEI_CONFLICT_SIMILARITY_THRESHOLD,
            limit=limit,
        )
    except Exception as exc:
        msg = f"Semantic candidate query failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)
        return []

    log.info("  Semantic: %d candidate pairs to classify", len(rows))
    if not rows:
        return []

    # Classify each candidate with LLM
    for row in rows:
        result = _classify_conflict(row)
        if result and result.get("classification") == "contradiction":
            pairs.append({
                "from_id":      row["from_id"],
                "to_id":        row["to_id"],
                "conflict_type": "semantic",
                "evidence":     result.get("evidence", "Semantic contradiction detected"),
                "confidence":   float(result.get("confidence", 0.75)),
            })

    log.info("  Semantic: %d genuine contradictions from %d candidates", len(pairs), len(rows))
    return pairs


def _classify_conflict(row: dict) -> dict | None:
    """Call LLM to classify one candidate conflict pair — uses heavy_reasoning provider."""
    user = (
        f"Concept A: '{row['from_name']}'\n"
        f"Definition A: {row['from_def'][:600]}\n\n"
        f"Concept B: '{row['to_name']}'\n"
        f"Definition B: {row['to_def'][:600]}\n\n"
        f"Embedding similarity score: {row.get('score', 'unknown'):.3f}"
    )
    try:
        return call_agent_llm_json("heavy_reasoning", _CONFLICT_SYSTEM, user, max_tokens=256)
    except Exception as exc:
        log.debug("LLM classification failed: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Write conflict edges
# ══════════════════════════════════════════════════════════════════════════════

def _write_conflict_edges(
    neo: Neo4jClient,
    pairs: list[dict],
    conflict_type: str,
) -> None:
    """
    Write CONTRADICTS edges to Neo4j for each detected conflict pair.
    Uses MERGE so re-runs update rather than duplicate edges.
    Self-conflicts (from_id == to_id) are written as a property flag
    on the node itself rather than as a self-loop edge.
    """
    if not pairs:
        return

    now = _now_iso()
    version = settings.PIPELINE_VERSION

    # ── Self-conflicts: flag the node directly ─────────────────────────────
    self_conflicts = [p for p in pairs if p["from_id"] == p["to_id"]]
    if self_conflicts:
        rows = [
            {"node_id": p["from_id"], "evidence": p["evidence"], "confidence": p["confidence"]}
            for p in self_conflicts
        ]
        cypher_self = """
        UNWIND $rows AS row
        MATCH (n {id: row.node_id})
        SET n.self_conflict       = true,
            n.self_conflict_reason = row.evidence,
            n.self_conflict_conf   = row.confidence,
            n.self_conflict_at     = $now
        """
        try:
            neo.run_batch(cypher_self, rows)
            # Manually pass `now` — run_batch doesn't support extra kwargs,
            # so we execute directly for the small self-conflict set.
            with neo.session() as s:
                s.run(cypher_self, rows=rows, now=now)
        except Exception as exc:
            log.warning("Self-conflict write failed: %s", exc)

    # ── Pair conflicts: write CONTRADICTS edge ─────────────────────────────
    edge_pairs = [p for p in pairs if p["from_id"] != p["to_id"]]
    if not edge_pairs:
        return

    rows = [
        {
            "from_id":      p["from_id"],
            "to_id":        p["to_id"],
            "conflict_type": conflict_type,
            "evidence":     p["evidence"],
            "confidence":   p["confidence"],
            "detected_at":  now,
            "agent_version": version,
        }
        for p in edge_pairs
    ]

    cypher = """
    UNWIND $rows AS row
    OPTIONAL MATCH (a {id: row.from_id})
    OPTIONAL MATCH (b {id: row.to_id})
    WITH a, b, row WHERE a IS NOT NULL AND b IS NOT NULL
    MERGE (a)-[r:CONTRADICTS]->(b)
    SET r.conflict_type = row.conflict_type,
        r.evidence      = row.evidence,
        r.confidence    = row.confidence,
        r.detected_at   = row.detected_at,
        r.agent_version = row.agent_version
    """
    try:
        n = neo.run_batch(cypher, rows)
        log.info(
            "  CONTRADICTS edges written: %d (%s)", n, conflict_type
        )
    except Exception as exc:
        log.warning("CONTRADICTS edge write failed (%s): %s", conflict_type, exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Conflict Agent").parse_args()
    report = run()
    print(json.dumps(report.to_dict(), indent=2))
