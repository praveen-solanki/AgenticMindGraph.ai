"""
agents/impact_agent.py
=======================
ASEI Impact Agent — traces full downstream impact when any KG node changes.

Triggered by: stale flags, new ingestion, conflict detection, or on-demand.
Writes IMPACT_OF edges from changed nodes to affected nodes, ranked by severity.

Run standalone:
    python -m agents.impact_agent --node-id <id>
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm_json
from utils.neo4j_client import Neo4jClient

log = get_logger("impact_agent")

_IMPACT_SYSTEM = """\
You are an AUTOSAR requirements impact analyst.

You are given a changed node and a list of potentially affected downstream nodes
reachable through the knowledge graph.

For each affected node, rate the impact severity:
  - "critical": direct dependency, system may fail without update
  - "major":    indirect dependency, likely needs review
  - "minor":    loosely related, may need awareness

Return ONLY valid JSON as a list:
[
  {"node_id": "<id>", "severity": "critical|major|minor", "reason": "<one sentence>"},
  ...
]

Include only nodes with genuine impact. Omit nodes with no meaningful connection.
"""

# Edge types ordered by impact weight (higher index = higher weight)
_EDGE_WEIGHTS: dict[str, float] = {
    "MENTIONS":             0.2,
    "SIMILAR_TO":           0.3,
    "SOURCED_FROM":         0.4,
    "HYPOTHESIZES":         0.4,
    "REFERENCES":           0.6,
    "HAS_PARAMETER":        0.6,
    "HAS_REQUIREMENT":      0.8,
    "DEPENDS_ON":           0.9,
    "CONTRADICTS":          1.0,
}


@dataclass
class ImpactReport:
    run_at:          str       = ""
    changed_nodes:   int       = 0
    impacted_nodes:  int       = 0
    edges_written:   int       = 0
    errors:          list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":         self.run_at,
            "changed_nodes":  self.changed_nodes,
            "impacted_nodes": self.impacted_nodes,
            "edges_written":  self.edges_written,
            "errors":         self.errors,
        }


def run(
    neo: Neo4jClient | None = None,
    node_ids: list[str] | None = None,
) -> ImpactReport:
    """
    Trace impact for given node_ids, or auto-detect recently changed/stale nodes.

    Args:
        neo:      Optional shared Neo4jClient.
        node_ids: Specific node IDs to trace. If None, finds recently flagged nodes.
    """
    report    = ImpactReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Impact Agent: starting")

        # If no specific nodes given, use recently stale/conflicted nodes
        if not node_ids:
            node_ids = _find_changed_nodes(neo, report)

        report.changed_nodes = len(node_ids)
        log.info("  %d changed nodes to trace", len(node_ids))

        for nid in node_ids[:settings.ASEI_IMPACT_BATCH]:
            affected = _trace_impact(neo, nid, report)
            if affected:
                _write_impact_edges(neo, nid, affected, report)

        log.info(
            "Impact Agent complete: changed=%d impacted=%d edges=%d",
            report.changed_nodes, report.impacted_nodes, report.edges_written,
        )
    except Exception as exc:
        msg = f"Impact Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


def _find_changed_nodes(neo: Neo4jClient, report: ImpactReport) -> list[str]:
    """Find recently stale or conflict-flagged nodes as impact sources."""
    q = """
    MATCH (n)
    WHERE n.stale = true OR n.self_conflict = true
      AND n.id IS NOT NULL
    RETURN n.id AS node_id
    LIMIT $limit
    """
    try:
        rows = neo.run(q, limit=settings.ASEI_IMPACT_BATCH)
        return [r["node_id"] for r in rows if r.get("node_id")]
    except Exception as exc:
        report.errors.append(f"Changed node fetch failed: {exc}")
        return []


def _trace_impact(neo: Neo4jClient, node_id: str, report: ImpactReport) -> list[dict]:
    """
    Multi-hop traversal from a changed node.
    Returns list of affected nodes with hop count and traversed edge types.
    """
    max_hops = settings.ASEI_IMPACT_MAX_HOPS
    q = f"""
    MATCH path = (start {{id: $node_id}})-[*1..{max_hops}]-(affected)
    WHERE affected.id IS NOT NULL AND affected.id <> $node_id
      AND affected.name IS NOT NULL
    WITH affected,
         min(length(path))                              AS hop_count,
         collect(DISTINCT [r IN relationships(path) | type(r)])[0] AS rel_path
    RETURN
        affected.id                                     AS node_id,
        affected.name                                   AS node_name,
        labels(affected)[0]                             AS node_label,
        coalesce(affected.raw_text, affected.definition,
                 affected.description, '')              AS node_desc,
        hop_count,
        rel_path
    ORDER BY hop_count ASC
    LIMIT 100
    """
    try:
        rows = neo.run(q, node_id=node_id)
        affected = []
        for r in rows:
            # Compute edge-weight-adjusted impact score
            path_types = r.get("rel_path") or []
            weight = max((_EDGE_WEIGHTS.get(t, 0.3) for t in path_types), default=0.3)
            hop_penalty = 1.0 / r["hop_count"]
            impact_score = weight * hop_penalty
            affected.append({
                "node_id":     r["node_id"],
                "node_name":   r["node_name"],
                "node_label":  r["node_label"],
                "node_desc":   str(r.get("node_desc", ""))[:300],
                "hop_count":   r["hop_count"],
                "rel_path":    path_types,
                "impact_score": round(impact_score, 3),
            })
        return affected
    except Exception as exc:
        log.warning("Impact traversal failed for %s: %s", node_id, exc)
        report.errors.append(str(exc))
        return []


def _write_impact_edges(
    neo: Neo4jClient,
    source_id: str,
    affected: list[dict],
    report: ImpactReport,
) -> None:
    """Write IMPACT_OF edges with severity scored by impact_score."""
    if not affected:
        return
    now = _now_iso()

    # Use LLM to classify severity for top affected nodes
    top = affected[:10]
    severity_map = _classify_severity(source_id, top)

    for node in affected:
        nid      = node["node_id"]
        severity = severity_map.get(nid, _score_to_severity(node["impact_score"]))
        q = """
        MATCH (src {id: $src_id})
        MATCH (dst {id: $dst_id})
        MERGE (src)-[r:IMPACT_OF]->(dst)
        SET r.severity     = $severity,
            r.hop_count    = $hops,
            r.impact_score = $score,
            r.detected_at  = $now
        """
        try:
            neo.run(
                q,
                src_id=source_id, dst_id=nid,
                severity=severity,
                hops=node["hop_count"],
                score=node["impact_score"],
                now=now,
            )
            report.edges_written += 1
        except Exception as exc:
            log.debug("Impact edge write failed: %s", exc)

    report.impacted_nodes += len(affected)


def _classify_severity(source_id: str, nodes: list[dict]) -> dict[str, str]:
    """LLM severity classification for top affected nodes."""
    node_list = "\n".join(
        f"- id={n['node_id']} name={n['node_name']} label={n['node_label']} "
        f"hops={n['hop_count']} desc={n['node_desc'][:100]}"
        for n in nodes
    )
    user = (
        f"Changed node ID: {source_id}\n\n"
        f"Potentially affected nodes:\n{node_list}\n\n"
        "Rate the impact severity for each node."
    )
    try:
        result = call_agent_llm_json("impact", _IMPACT_SYSTEM, user, max_tokens=512)
        if isinstance(result, list):
            return {item["node_id"]: item.get("severity", "minor") for item in result if "node_id" in item}
    except Exception as exc:
        log.debug("LLM severity classification failed: %s", exc)
    return {}


def _score_to_severity(score: float) -> str:
    if score >= 0.7:
        return "critical"
    if score >= 0.4:
        return "major"
    return "minor"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ASEI Impact Agent")
    p.add_argument("--node-id", nargs="*", help="Node IDs to trace impact for")
    return p.parse_args()


if __name__ == "__main__":
    import json
    args = _parse_args()
    report = run(node_ids=args.node_id or None)
    print(json.dumps(report.to_dict(), indent=2))
