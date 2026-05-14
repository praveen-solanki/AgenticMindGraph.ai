"""
agents/evolution_agent.py
=========================
ASEI Evolution Agent — detects staleness and low-confidence nodes in the KG.

Responsibilities
----------------
1. Scan every Requirement, Entity, and Concept node for staleness signals:
   - `ingested_at` older than ASEI_STALENESS_DAYS (configurable in settings)
   - `confidence_score` below ASEI_LOW_CONFIDENCE_THRESHOLD
   - `pipeline_version` != current settings.PIPELINE_VERSION (schema drift)

2. For each flagged node, write back to Neo4j:
   - `stale = True`
   - `staleness_reason` (comma-separated list of triggered signals)
   - `flagged_at` (ISO-8601 timestamp of this agent run)

3. Return a structured EvolutionReport for the orchestrator to log/act on.

Design notes
------------
- Read-heavy: all detection is pure Cypher; no LLM calls in this agent.
- Write-narrow: only updates stale flags, never restructures the graph.
- Idempotent: re-running clears old flags before re-applying (MERGE + SET).
- The orchestrator calls this agent on every ASEI cycle to keep the KG fresh.

Run standalone:
    python -m agents.evolution_agent --output-dir ./output
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from config import settings
from utils.logger import get_logger
from utils.neo4j_client import Neo4jClient

log = get_logger("evolution_agent")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class EvolutionReport:
    run_at:             str          = ""
    stale_count:        int          = 0
    low_confidence_count: int        = 0
    schema_drift_count: int          = 0
    total_flagged:      int          = 0
    total_cleared:      int          = 0
    flagged_ids:        list[str]    = field(default_factory=list)
    errors:             list[str]    = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":               self.run_at,
            "stale_count":          self.stale_count,
            "low_confidence_count": self.low_confidence_count,
            "schema_drift_count":   self.schema_drift_count,
            "total_flagged":        self.total_flagged,
            "total_cleared":        self.total_cleared,
            "flagged_ids":          self.flagged_ids[:100],   # cap for logging
            "errors":               self.errors,
        }


# ── Main agent entry point ────────────────────────────────────────────────────

def run(neo: Neo4jClient | None = None) -> EvolutionReport:
    """
    Execute one full evolution scan cycle.

    Args:
        neo: Optional Neo4jClient; a new one is created if not supplied.
             Callers that own the connection (e.g. orchestrator) should pass it in.

    Returns:
        EvolutionReport with counts and flagged node IDs.
    """
    report = EvolutionReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Evolution Agent: starting scan")

        # 1. Clear stale flags from previous run so we get a clean slate.
        #    Nodes that are no longer stale should not carry old flags.
        cleared = _clear_stale_flags(neo)
        report.total_cleared = cleared
        log.info("  Cleared %d stale flags from previous run", cleared)

        # 2. Flag by staleness (age)
        stale_ids = _flag_by_staleness(neo, report)
        report.stale_count = len(stale_ids)

        # 3. Flag by low confidence score
        low_conf_ids = _flag_by_low_confidence(neo, report)
        report.low_confidence_count = len(low_conf_ids)

        # 4. Flag by schema drift (pipeline_version mismatch)
        drift_ids = _flag_by_schema_drift(neo, report)
        report.schema_drift_count = len(drift_ids)

        # 5. Aggregate unique flagged IDs
        all_flagged = set(stale_ids) | set(low_conf_ids) | set(drift_ids)
        report.total_flagged = len(all_flagged)
        report.flagged_ids   = sorted(all_flagged)

        log.info(
            "Evolution Agent complete: %d flagged "
            "(stale=%d, low_conf=%d, schema_drift=%d)",
            report.total_flagged,
            report.stale_count,
            report.low_confidence_count,
            report.schema_drift_count,
        )

    except Exception as exc:
        msg = f"Evolution Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _staleness_cutoff_iso() -> str:
    """ISO-8601 timestamp of the oldest acceptable ingestion date."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.ASEI_STALENESS_DAYS)
    return cutoff.isoformat()


def _clear_stale_flags(neo: Neo4jClient) -> int:
    """Remove stale=True on all nodes before re-evaluating."""
    cypher = """
    MATCH (n)
    WHERE n.stale = true
    SET n.stale            = false,
        n.staleness_reason = null,
        n.flagged_at       = null
    RETURN count(n) AS n
    """
    result = neo.run(cypher)
    return result[0]["n"] if result else 0


def _flag_by_staleness(neo: Neo4jClient, report: EvolutionReport) -> list[str]:
    """
    Flag nodes whose `ingested_at` is older than ASEI_STALENESS_DAYS.
    Targets: Requirement, Entity, Concept — the three provenance-stamped labels.
    """
    cutoff = _staleness_cutoff_iso()
    now    = _now_iso()

    cypher = """
    UNWIND ['Requirement', 'Entity', 'Concept'] AS lbl
    CALL apoc.cypher.run(
        'MATCH (n:' + lbl + ')
         WHERE n.ingested_at IS NOT NULL
           AND n.ingested_at < $cutoff
         SET n.stale            = true,
             n.staleness_reason = coalesce(n.staleness_reason + \",\", \"\") + \"age_exceeded\",
             n.flagged_at       = $now
         RETURN n.id AS node_id',
        {cutoff: $cutoff, now: $now}
    ) YIELD value
    RETURN value.node_id AS node_id
    """
    # Fallback: APOC may not be available; use per-label queries instead.
    ids: list[str] = []
    for label in ("Requirement", "Entity", "Concept"):
        q = f"""
        MATCH (n:{label})
        WHERE n.ingested_at IS NOT NULL
          AND n.ingested_at < $cutoff
        SET n.stale            = true,
            n.staleness_reason = coalesce(n.staleness_reason + ',', '') + 'age_exceeded',
            n.flagged_at       = $now
        RETURN n.id AS node_id
        """
        try:
            rows = neo.run(q, cutoff=cutoff, now=now)
            ids.extend(r["node_id"] for r in rows if r.get("node_id"))
        except Exception as exc:
            msg = f"Staleness flag failed for {label}: {exc}"
            log.warning(msg)
            report.errors.append(msg)

    log.info("  Staleness (age >%d days): %d nodes flagged", settings.ASEI_STALENESS_DAYS, len(ids))
    return ids


def _flag_by_low_confidence(neo: Neo4jClient, report: EvolutionReport) -> list[str]:
    """
    Flag nodes with confidence_score below ASEI_LOW_CONFIDENCE_THRESHOLD.
    Only LLM-extracted nodes (extraction_method != 'rule_based') are evaluated;
    rule-based extractions are always confidence=1.0 by construction.
    """
    threshold = settings.ASEI_LOW_CONFIDENCE_THRESHOLD
    now       = _now_iso()
    ids: list[str] = []

    for label in ("Requirement", "Entity", "Concept"):
        q = f"""
        MATCH (n:{label})
        WHERE n.confidence_score IS NOT NULL
          AND n.confidence_score < $threshold
          AND n.extraction_method <> 'rule_based'
        SET n.stale            = true,
            n.staleness_reason = coalesce(n.staleness_reason + ',', '') + 'low_confidence',
            n.flagged_at       = $now
        RETURN n.id AS node_id
        """
        try:
            rows = neo.run(q, threshold=threshold, now=now)
            ids.extend(r["node_id"] for r in rows if r.get("node_id"))
        except Exception as exc:
            msg = f"Low-confidence flag failed for {label}: {exc}"
            log.warning(msg)
            report.errors.append(msg)

    log.info(
        "  Low confidence (score < %.2f): %d nodes flagged",
        threshold, len(ids),
    )
    return ids


def _flag_by_schema_drift(neo: Neo4jClient, report: EvolutionReport) -> list[str]:
    """
    Flag nodes extracted by an older pipeline_version.
    These nodes may be missing new properties added in later pipeline versions
    and should be re-extracted when the pipeline is re-run.
    """
    current_version = settings.PIPELINE_VERSION
    now             = _now_iso()
    ids: list[str]  = []

    for label in ("Requirement", "Entity", "Concept"):
        q = f"""
        MATCH (n:{label})
        WHERE n.pipeline_version IS NOT NULL
          AND n.pipeline_version <> $version
        SET n.stale            = true,
            n.staleness_reason = coalesce(n.staleness_reason + ',', '') + 'schema_drift',
            n.flagged_at       = $now
        RETURN n.id AS node_id
        """
        try:
            rows = neo.run(q, version=current_version, now=now)
            ids.extend(r["node_id"] for r in rows if r.get("node_id"))
        except Exception as exc:
            msg = f"Schema drift flag failed for {label}: {exc}"
            log.warning(msg)
            report.errors.append(msg)

    log.info(
        "  Schema drift (pipeline_version != %s): %d nodes flagged",
        current_version, len(ids),
    )
    return ids


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ASEI Evolution Agent — standalone run")
    p.add_argument("--output-dir", default="./output", help="Pipeline output dir (unused; for compat)")
    return p.parse_args()


if __name__ == "__main__":
    _parse_args()
    import json
    report = run()
    print(json.dumps(report.to_dict(), indent=2))
