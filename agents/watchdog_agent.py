"""
agents/watchdog_agent.py
========================
ASEI Watchdog Agent — monitors all other agents, records metrics to Neo4j,
and auto-adjusts thresholds when agents degrade.

Zero LLM calls — pure Python metric tracking and Neo4j writes.

Run standalone:
    python -m agents.watchdog_agent
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.neo4j_client import Neo4jClient

log = get_logger("watchdog_agent")


@dataclass
class AgentMetric:
    agent_name:      str
    hypothesis_count: int   = 0
    rejection_count:  int   = 0
    conflict_count:   int   = 0
    error_count:      int   = 0
    avg_confidence:   float = 0.0
    rejection_rate:   float = 0.0
    error_rate:       float = 0.0
    health:           str   = "ok"    # ok | degraded | critical


@dataclass
class WatchdogReport:
    run_at:    str             = ""
    metrics:   list[dict]     = field(default_factory=list)
    alerts:    list[str]      = field(default_factory=list)
    actions:   list[str]      = field(default_factory=list)
    errors:    list[str]      = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":  self.run_at,
            "metrics": self.metrics,
            "alerts":  self.alerts,
            "actions": self.actions,
            "errors":  self.errors,
        }


def run(
    cycle_state: dict | None = None,
    neo: Neo4jClient | None = None,
) -> WatchdogReport:
    """
    Compute agent health metrics from the current cycle state and KG data.

    Args:
        cycle_state: OrchestratorState.to_dict() from the current cycle.
        neo:         Optional shared Neo4jClient.
    """
    report    = WatchdogReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Watchdog Agent: computing metrics")

        metrics = _compute_metrics(neo, cycle_state or {}, report)
        for m in metrics:
            report.metrics.append(m.__dict__ if hasattr(m, '__dict__') else m)
            _check_thresholds(m, report)
            _write_metric_node(neo, m, report)

        log.info(
            "Watchdog Agent complete: %d agents monitored, %d alerts",
            len(metrics), len(report.alerts),
        )
    except Exception as exc:
        msg = f"Watchdog Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


def _compute_metrics(
    neo: Neo4jClient,
    cycle_state: dict,
    report: WatchdogReport,
) -> list[AgentMetric]:
    """Compute per-agent metrics from cycle state + KG stats."""
    metrics: list[AgentMetric] = []

    # ── Synthesis Agent ───────────────────────────────────────────────────────
    syn = AgentMetric(agent_name="synthesis_agent")
    try:
        # Total hypothesis edges (all time)
        r = neo.run("MATCH ()-[r:HYPOTHESIZES]->() RETURN count(r) AS n")
        syn.hypothesis_count = r[0]["n"] if r else 0
        # Rejected hypotheses
        r = neo.run("MATCH ()-[r:HYPOTHESIZES]->() WHERE r.verified = false RETURN count(r) AS n")
        syn.rejection_count = r[0]["n"] if r else 0
        # Avg confidence
        r = neo.run("MATCH ()-[r:HYPOTHESIZES]->() WHERE r.confidence IS NOT NULL RETURN avg(r.confidence) AS avg")
        syn.avg_confidence = round(float(r[0]["avg"] or 0), 3) if r else 0.0
        if syn.hypothesis_count > 0:
            syn.rejection_rate = round(syn.rejection_count / syn.hypothesis_count, 3)
    except Exception as exc:
        report.errors.append(f"Synthesis metrics failed: {exc}")
    metrics.append(syn)

    # ── Conflict Agent ────────────────────────────────────────────────────────
    con = AgentMetric(agent_name="conflict_agent")
    try:
        r = neo.run("MATCH ()-[r:CONTRADICTS]->() RETURN count(r) AS n")
        con.conflict_count = r[0]["n"] if r else 0
    except Exception as exc:
        report.errors.append(f"Conflict metrics failed: {exc}")
    metrics.append(con)

    # ── Evolution Agent ───────────────────────────────────────────────────────
    evo = AgentMetric(agent_name="evolution_agent")
    try:
        r = neo.run("MATCH (n) WHERE n.stale = true RETURN count(n) AS n")
        evo.hypothesis_count = r[0]["n"] if r else 0  # reusing field for stale count
    except Exception as exc:
        report.errors.append(f"Evolution metrics failed: {exc}")
    metrics.append(evo)

    # ── Errors from cycle state ───────────────────────────────────────────────
    cycle_errors = cycle_state.get("errors", [])
    for m in metrics:
        # Assign cycle errors proportionally (rough approximation)
        m.error_count = len([e for e in cycle_errors if m.agent_name.split("_")[0] in e.lower()])

    return metrics


def _check_thresholds(m: AgentMetric, report: WatchdogReport) -> None:
    """Check metric thresholds and generate alerts + automatic actions."""
    rejection_ceiling = settings.ASEI_WATCHDOG_REJECTION_CEILING
    error_ceiling     = settings.ASEI_WATCHDOG_ERROR_CEILING

    if m.rejection_rate >= rejection_ceiling:
        alert = (
            f"ALERT: {m.agent_name} rejection rate {m.rejection_rate:.0%} "
            f">= ceiling {rejection_ceiling:.0%}"
        )
        log.warning(alert)
        report.alerts.append(alert)
        m.health = "degraded"

        # Auto-action: raise synthesis confidence threshold for next cycle
        if m.agent_name == "synthesis_agent":
            action = (
                f"ACTION: Raising ASEI_SYNTHESIS_MIN_CONFIDENCE from "
                f"{settings.ASEI_SYNTHESIS_MIN_CONFIDENCE:.2f} to "
                f"{min(settings.ASEI_SYNTHESIS_MIN_CONFIDENCE + 0.05, 0.95):.2f}"
            )
            log.info(action)
            report.actions.append(action)
            # Note: runtime settings change — persists for this process only.
            # Operator should update settings.py or env var for permanent change.
            settings.ASEI_SYNTHESIS_MIN_CONFIDENCE = min(
                settings.ASEI_SYNTHESIS_MIN_CONFIDENCE + 0.05, 0.95
            )

    total_ops = max(m.hypothesis_count + m.conflict_count, 1)
    m.error_rate = round(m.error_count / total_ops, 3)
    if m.error_rate >= error_ceiling:
        alert = f"ALERT: {m.agent_name} error rate {m.error_rate:.0%} >= ceiling {error_ceiling:.0%}"
        log.warning(alert)
        report.alerts.append(alert)
        m.health = "critical" if m.error_rate >= 0.5 else "degraded"


def _write_metric_node(neo: Neo4jClient, m: AgentMetric, report: WatchdogReport) -> None:
    """Write/update an AgentMetrics node in Neo4j for trend tracking."""
    now = _now_iso()
    q = """
    MERGE (am:AgentMetrics {agent_name: $name})
    SET am.hypothesis_count = $hyp,
        am.rejection_count  = $rej,
        am.conflict_count   = $conf,
        am.error_count      = $err,
        am.avg_confidence   = $avg_conf,
        am.rejection_rate   = $rej_rate,
        am.error_rate       = $err_rate,
        am.health           = $health,
        am.last_updated_at  = $now
    """
    try:
        neo.run(
            q,
            name=m.agent_name,
            hyp=m.hypothesis_count,
            rej=m.rejection_count,
            conf=m.conflict_count,
            err=m.error_count,
            avg_conf=m.avg_confidence,
            rej_rate=m.rejection_rate,
            err_rate=m.error_rate,
            health=m.health,
            now=now,
        )
    except Exception as exc:
        log.debug("Metric node write failed for %s: %s", m.agent_name, exc)
        report.errors.append(str(exc))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Watchdog Agent").parse_args()
    print(json.dumps(run().to_dict(), indent=2))
