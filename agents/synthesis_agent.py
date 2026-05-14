"""
agents/synthesis_agent.py
=========================
ASEI Synthesis Agent — discovers non-obvious bridges between KG nodes and
proposes new hypothesis edges with LLM-generated rationale.

Responsibilities
----------------
1. Path mining (Cypher):
   - Find pairs of Entity/Concept nodes that are NOT directly connected but
     share a common intermediate neighbour (2-hop paths).
   - Candidate pairs are ranked by shared-neighbour count (structural bridge
     strength) and filtered by a minimum threshold.

2. Hypothesis generation (LLM):
   - For each candidate pair the LLM receives both node descriptions + the
     shared-neighbour context and proposes:
       * hypothesis_type  : e.g. "INFLUENCES", "CO_OCCURS_WITH", "ENABLES"
       * rationale        : 1–3 sentence justification
       * confidence       : 0.0–1.0

3. KG write-back:
   - Confirmed hypotheses (confidence >= ASEI_SYNTHESIS_MIN_CONFIDENCE) are
     written as HYPOTHESIZES edges with full provenance.

4. Return a SynthesisReport for the orchestrator.

Design notes
------------
- Path mining is purely Cypher — O(E) per hop, bounded by ASEI_SYNTHESIS_CANDIDATE_LIMIT.
- LLM calls are batched and bounded by ASEI_SYNTHESIS_LLM_LIMIT.
- Idempotent: MERGE ensures re-runs refresh rather than duplicate edges.
- The HYPOTHESIZES relationship type is registered in settings.ALLOWED_RELATIONSHIPS.

Run standalone:
    python -m agents.synthesis_agent
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

log = get_logger("synthesis_agent")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SynthesisReport:
    run_at:              str        = ""
    candidates_found:    int        = 0
    hypotheses_proposed: int        = 0
    hypotheses_written:  int        = 0
    hypothesis_edges:    list[dict] = field(default_factory=list)
    errors:              list[str]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":              self.run_at,
            "candidates_found":    self.candidates_found,
            "hypotheses_proposed": self.hypotheses_proposed,
            "hypotheses_written":  self.hypotheses_written,
            "hypothesis_edges":    self.hypothesis_edges[:50],
            "errors":              self.errors,
        }


# ── LLM prompt ────────────────────────────────────────────────────────────────

_SYNTHESIS_SYSTEM = """\
You are a knowledge graph hypothesis engine for a technical knowledge base.

You are given two nodes that are NOT directly connected in the knowledge graph
but share common neighbours (indirect structural bridge). Your task is to
reason whether a meaningful direct relationship exists between them.

If yes, propose a hypothesis relationship with:
  - hypothesis_type : a concise relation label in UPPER_SNAKE_CASE
                      (e.g. "INFLUENCES", "ENABLES", "CO_OCCURS_WITH",
                       "DEPENDS_ON", "CONTRADICTS", "GENERALISES")
  - rationale       : 1–3 sentences explaining the proposed connection
  - confidence      : float 0.0–1.0 (your certainty in the hypothesis)

If no meaningful relationship can be inferred, return confidence=0.0.

Return ONLY valid JSON, no markdown, no explanation:
{
  "hypothesis_type": "<RELATION_LABEL>",
  "rationale":       "<explanation>",
  "confidence":      <float 0.0-1.0>
}
"""


# ── Main agent entry point ────────────────────────────────────────────────────

def run(neo: Neo4jClient | None = None) -> SynthesisReport:
    """
    Execute one full synthesis cycle.

    Args:
        neo: Optional Neo4jClient; created internally if not provided.

    Returns:
        SynthesisReport with hypothesis counts and written edges.
    """
    report    = SynthesisReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Synthesis Agent: starting hypothesis discovery")

        # ── 1. Mine structural bridge candidates ──────────────────────────────
        candidates = _find_bridge_candidates(neo, report)
        report.candidates_found = len(candidates)

        # ── 2. Limit LLM calls ────────────────────────────────────────────────
        llm_limit  = settings.ASEI_SYNTHESIS_LLM_LIMIT
        to_process = candidates[:llm_limit]
        log.info("  Synthesis: %d candidates, processing %d through LLM", len(candidates), len(to_process))

        # ── 3. Generate hypotheses ────────────────────────────────────────────
        hypotheses = _generate_hypotheses(to_process, report)
        report.hypotheses_proposed = len(hypotheses)

        # ── 4. Write confirmed hypotheses to KG ──────────────────────────────
        min_conf = settings.ASEI_SYNTHESIS_MIN_CONFIDENCE
        confirmed = [h for h in hypotheses if h.get("confidence", 0.0) >= min_conf]
        _write_hypothesis_edges(neo, confirmed)
        report.hypotheses_written = len(confirmed)
        report.hypothesis_edges   = [
            {
                "from": h["from_id"],
                "to":   h["to_id"],
                "type": h["hypothesis_type"],
                "conf": h["confidence"],
                "rationale": h.get("rationale", ""),
            }
            for h in confirmed
        ]

        log.info(
            "Synthesis Agent complete: %d candidates → %d proposed → %d written",
            report.candidates_found,
            report.hypotheses_proposed,
            report.hypotheses_written,
        )

    except Exception as exc:
        msg = f"Synthesis Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


# ══════════════════════════════════════════════════════════════════════════════
# Candidate mining
# ══════════════════════════════════════════════════════════════════════════════

def _find_bridge_candidates(
    neo: Neo4jClient,
    report: SynthesisReport,
) -> list[dict]:
    """
    Find Entity/Concept pairs connected via a shared intermediate neighbour
    but NOT directly linked. Returns candidates ranked by bridge strength
    (number of shared neighbours).
    """
    min_bridges = settings.ASEI_SYNTHESIS_MIN_BRIDGE_COUNT
    limit       = settings.ASEI_SYNTHESIS_CANDIDATE_LIMIT

    # 2-hop structural bridge query — pure Cypher, no LLM
    # Finds pairs (a, b) where a and b share at least `min_bridges` neighbours
    # but have no direct relationship between them.
    q = """
    MATCH (a)-[*1..1]-(mid)-[*1..1]-(b)
    WHERE (a:Entity OR a:Concept)
      AND (b:Entity OR b:Concept)
      AND elementId(a) < elementId(b)
      AND a.name IS NOT NULL
      AND b.name IS NOT NULL
      AND NOT (a)-[]-(b)
    WITH a, b, count(DISTINCT mid) AS shared_neighbours
    WHERE shared_neighbours >= $min_bridges
    RETURN
        a.id   AS from_id,
        b.id   AS to_id,
        a.name AS from_name,
        b.name AS to_name,
        coalesce(a.definition, a.description, '') AS from_desc,
        coalesce(b.definition, b.description, '') AS to_desc,
        shared_neighbours
    ORDER BY shared_neighbours DESC
    LIMIT $limit
    """
    try:
        rows = neo.run(q, min_bridges=min_bridges, limit=limit)
        log.info("  Synthesis: %d structural bridge candidates found", len(rows))
        return [dict(r) for r in rows]
    except Exception as exc:
        msg = f"Candidate mining failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Hypothesis generation (LLM)
# ══════════════════════════════════════════════════════════════════════════════

def _generate_hypotheses(
    candidates: list[dict],
    report: SynthesisReport,
) -> list[dict]:
    """
    For each candidate pair, call the LLM to propose a hypothesis relationship.
    Returns only those with confidence > 0.
    """
    results: list[dict] = []
    for row in candidates:
        result = _propose_hypothesis(row)
        if result and float(result.get("confidence", 0.0)) > 0.0:
            results.append({
                "from_id":        row["from_id"],
                "to_id":          row["to_id"],
                "from_name":      row["from_name"],
                "to_name":        row["to_name"],
                "hypothesis_type": result.get("hypothesis_type", "RELATED_TO"),
                "rationale":      result.get("rationale", ""),
                "confidence":     float(result.get("confidence", 0.5)),
                "shared_neighbours": row.get("shared_neighbours", 0),
            })

    log.info(
        "  Synthesis: %d hypotheses proposed from %d candidates",
        len(results), len(candidates),
    )
    return results


def _propose_hypothesis(row: dict) -> dict | None:
    """Call LLM to propose a hypothesis — uses synthesis provider (DeepSeek-V3.2)."""
    user = (
        f"Node A: '{row['from_name']}'\n"
        f"Description A: {str(row.get('from_desc', ''))[:500]}\n\n"
        f"Node B: '{row['to_name']}'\n"
        f"Description B: {str(row.get('to_desc', ''))[:500]}\n\n"
        f"Shared structural neighbours (bridge strength): {row.get('shared_neighbours', 0)}\n\n"
        "Do these two nodes have a meaningful direct relationship "
        "not already captured in the graph?"
    )
    try:
        return call_agent_llm_json("synthesis", _SYNTHESIS_SYSTEM, user, max_tokens=300)
    except Exception as exc:
        log.debug("LLM hypothesis generation failed: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# KG write-back
# ══════════════════════════════════════════════════════════════════════════════

def _write_hypothesis_edges(
    neo: Neo4jClient,
    hypotheses: list[dict],
) -> None:
    """
    Write HYPOTHESIZES edges to Neo4j for each confirmed hypothesis.
    Uses MERGE for idempotency; on re-run, existing edges are refreshed.
    """
    if not hypotheses:
        return

    now     = _now_iso()
    version = settings.PIPELINE_VERSION

    rows = [
        {
            "from_id":        h["from_id"],
            "to_id":          h["to_id"],
            "hypothesis_type": h["hypothesis_type"],
            "rationale":      h.get("rationale", ""),
            "confidence":     h["confidence"],
            "proposed_at":    now,
            "agent_version":  version,
            "bridge_count":   h.get("shared_neighbours", 0),
        }
        for h in hypotheses
    ]

    cypher = """
    UNWIND $rows AS row
    OPTIONAL MATCH (a {id: row.from_id})
    OPTIONAL MATCH (b {id: row.to_id})
    WITH a, b, row WHERE a IS NOT NULL AND b IS NOT NULL
    MERGE (a)-[r:HYPOTHESIZES]->(b)
    SET r.hypothesis_type = row.hypothesis_type,
        r.rationale       = row.rationale,
        r.confidence      = row.confidence,
        r.proposed_at     = row.proposed_at,
        r.agent_version   = row.agent_version,
        r.bridge_count    = row.bridge_count
    """
    try:
        n = neo.run_batch(cypher, rows)
        log.info("  HYPOTHESIZES edges written: %d", n)
    except Exception as exc:
        log.warning("Hypothesis edge write failed: %s", exc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Synthesis Agent").parse_args()
    report = run()
    print(json.dumps(report.to_dict(), indent=2))
