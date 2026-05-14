"""
agents/gap_detection_agent.py
==============================
ASEI Gap Detection Agent — finds missing requirements in the KG.

Looks for cases where Module A's requirements reference interfaces or behaviours
that Module B should define — but B has no corresponding requirement.
Writes SPEC_GAP edges from the referencing requirement to the module that
should have defined the missing requirement.

Run standalone:
    python -m agents.gap_detection_agent
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm_json
from utils.neo4j_client import Neo4jClient

log = get_logger("gap_detection_agent")

_GAP_SYSTEM = """\
You are a requirements engineering analyst for AUTOSAR specifications.

You are given two module summaries and a list of cross-references from Module A
into Module B's domain.  Determine if Module B is MISSING a requirement that
should correspond to what Module A references.

Return ONLY valid JSON:
{
  "gap_detected": true | false,
  "confidence":   <float 0.0-1.0>,
  "missing_requirement_description": "<what Module B should define, or empty>",
  "severity": "critical" | "major" | "minor"
}
"""


@dataclass
class GapReport:
    run_at:       str       = ""
    gaps_found:   int       = 0
    gaps_written: int       = 0
    errors:       list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":       self.run_at,
            "gaps_found":   self.gaps_found,
            "gaps_written": self.gaps_written,
            "errors":       self.errors,
        }


def run(neo: Neo4jClient | None = None) -> GapReport:
    report    = GapReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Gap Detection Agent: starting")
        candidates = _find_cross_module_refs(neo, report)
        log.info("  %d cross-module reference candidates", len(candidates))

        gaps: list[dict] = []
        for cand in candidates[:settings.ASEI_GAP_CANDIDATE_LIMIT]:
            gap = _analyse_gap(cand)
            if gap and gap.get("gap_detected") and float(gap.get("confidence", 0)) >= settings.ASEI_GAP_MIN_CONFIDENCE:
                gaps.append({**cand, **gap})

        report.gaps_found = len(gaps)
        _write_gap_edges(neo, gaps, report)

        log.info("Gap Detection Agent complete: gaps_found=%d written=%d", report.gaps_found, report.gaps_written)
    except Exception as exc:
        msg = f"Gap Detection Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


def _find_cross_module_refs(neo: Neo4jClient, report: GapReport) -> list[dict]:
    """
    Find requirements in Module A that REFERENCES entities from Module B's domain,
    but Module B has no requirement covering that entity.
    """
    q = """
    MATCH (ma:Module)-[:HAS_REQUIREMENT]->(ra:Requirement)-[:REFERENCES]->(rb:Requirement)<-[:HAS_REQUIREMENT]-(mb:Module)
    WHERE ma <> mb
      AND NOT EXISTS {
          MATCH (mb)-[:HAS_REQUIREMENT]->(rx:Requirement)
          WHERE rx.id CONTAINS rb.module OR rx.raw_text CONTAINS rb.id
      }
    RETURN
        ma.name AS from_module,
        mb.name AS to_module,
        ra.id   AS ref_req_id,
        ra.raw_text AS ref_req_text,
        rb.id   AS target_req_id,
        coalesce(ma.summary, '') AS from_summary,
        coalesce(mb.summary, '') AS to_summary
    LIMIT $limit
    """
    try:
        rows = neo.run(q, limit=settings.ASEI_GAP_CANDIDATE_LIMIT * 3)
        return [dict(r) for r in rows]
    except Exception as exc:
        report.errors.append(f"Cross-module ref query failed: {exc}")
        return []


def _analyse_gap(cand: dict) -> dict | None:
    user = (
        f"Module A: {cand['from_module']}\n"
        f"Module A summary: {cand.get('from_summary','')[:400]}\n\n"
        f"Module B: {cand['to_module']}\n"
        f"Module B summary: {cand.get('to_summary','')[:400]}\n\n"
        f"Module A's requirement {cand['ref_req_id']} references {cand['target_req_id']} "
        f"which belongs to Module B.\n"
        f"Requirement text: {cand.get('ref_req_text','')[:400]}\n\n"
        "Is Module B missing a requirement that should correspond to this reference?"
    )
    try:
        return call_agent_llm_json("gap_detection", _GAP_SYSTEM, user, max_tokens=300)
    except Exception as exc:
        log.debug("Gap analysis failed: %s", exc)
        return None


def _write_gap_edges(neo: Neo4jClient, gaps: list[dict], report: GapReport) -> None:
    if not gaps:
        return
    now = _now_iso()
    for gap in gaps:
        q = """
        MATCH (r:Requirement {id: $req_id})
        MATCH (m:Module {name: $to_module})
        MERGE (r)-[e:SPEC_GAP]->(m)
        SET e.severity           = $severity,
            e.confidence         = $confidence,
            e.missing_desc       = $missing_desc,
            e.detected_at        = $now,
            e.agent_version      = $version
        """
        try:
            neo.run(
                q,
                req_id=gap["ref_req_id"],
                to_module=gap["to_module"],
                severity=gap.get("severity", "minor"),
                confidence=float(gap.get("confidence", 0.0)),
                missing_desc=gap.get("missing_requirement_description", ""),
                now=now,
                version=settings.PIPELINE_VERSION,
            )
            report.gaps_written += 1
        except Exception as exc:
            log.warning("Gap edge write failed: %s", exc)
            report.errors.append(str(exc))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Gap Detection Agent").parse_args()
    print(json.dumps(run().to_dict(), indent=2))
