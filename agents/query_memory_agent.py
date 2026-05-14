"""
agents/query_memory_agent.py
============================
ASEI Query Memory Agent — classifies and stores query patterns in the KG.

After every Reasoning Agent session, classifies the query (fast LLM),
stores a QueryPattern node, and identifies: low-confidence hot spots,
frequently asked questions, and volatile knowledge (answers that change).

Run standalone:
    python -m agents.query_memory_agent
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm_json
from utils.neo4j_client import Neo4jClient

log = get_logger("query_memory_agent")

_CLASSIFY_SYSTEM = """\
You are a query classifier for a technical knowledge base system.

Classify the question into a category and identify key entities mentioned.

Return ONLY valid JSON:
{
  "category":        "requirement_lookup" | "dependency_analysis" | "impact_assessment" |
                     "compliance_check"   | "gap_analysis"        | "general",
  "key_entities":    ["<entity name>", ...],
  "complexity":      "simple" | "multi_hop" | "cross_module",
  "confidence_hint": <float 0.0-1.0>
}
"""


@dataclass
class QueryMemoryReport:
    run_at:         str      = ""
    patterns_stored: int     = 0
    hot_spots:      list[str] = field(default_factory=list)
    low_conf_gaps:  list[str] = field(default_factory=list)
    errors:         list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":          self.run_at,
            "patterns_stored": self.patterns_stored,
            "hot_spots":       self.hot_spots,
            "low_conf_gaps":   self.low_conf_gaps,
            "errors":          self.errors,
        }


def run(
    reasoning_result: dict | None = None,
    neo: Neo4jClient | None = None,
) -> QueryMemoryReport:
    """
    Store a QueryPattern node for a completed reasoning session and
    analyse accumulated patterns for hot spots and gaps.

    Args:
        reasoning_result: ReasoningResult.to_dict() from the Reasoning Agent.
        neo:              Optional shared Neo4jClient.
    """
    report    = QueryMemoryReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Query Memory Agent: starting")

        # Store pattern for current query (if provided)
        if reasoning_result and reasoning_result.get("question"):
            _store_pattern(neo, reasoning_result, report)

        # Analyse accumulated patterns
        _analyse_hot_spots(neo, report)
        _analyse_low_confidence_gaps(neo, report)

        log.info(
            "Query Memory Agent: stored=%d hot_spots=%d gaps=%d",
            report.patterns_stored, len(report.hot_spots), len(report.low_conf_gaps),
        )
    except Exception as exc:
        msg = f"Query Memory Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


def _store_pattern(neo: Neo4jClient, result: dict, report: QueryMemoryReport) -> None:
    """Classify the question and write a QueryPattern node."""
    question = result.get("question", "")
    confidence = float(result.get("confidence", 0.0))

    # Classify with fast model (Cerebras Llama 3.1 8B)
    classification = call_agent_llm_json(
        "fast_classify", _CLASSIFY_SYSTEM, f"Question: {question}", max_tokens=200
    )
    category    = "general"
    entities    = []
    complexity  = "simple"

    if classification and isinstance(classification, dict):
        category   = classification.get("category", "general")
        entities   = classification.get("key_entities", [])
        complexity = classification.get("complexity", "simple")

    now = _now_iso()
    q = """
    MERGE (qp:QueryPattern {question: $question})
    SET qp.category       = $category,
        qp.complexity     = $complexity,
        qp.key_entities   = $entities,
        qp.last_confidence = $confidence,
        qp.last_asked_at  = $now,
        qp.ask_count      = coalesce(qp.ask_count, 0) + 1
    """
    try:
        neo.run(
            q, question=question, category=category,
            complexity=complexity, entities=entities,
            confidence=confidence, now=now,
        )
        report.patterns_stored += 1
        log.info("  QueryPattern stored: category=%s complexity=%s", category, complexity)
    except Exception as exc:
        report.errors.append(f"Pattern store failed: {exc}")


def _analyse_hot_spots(neo: Neo4jClient, report: QueryMemoryReport) -> None:
    """Find questions asked >= ASEI_QUERY_MEMORY_HOT_SPOT_COUNT times."""
    q = """
    MATCH (qp:QueryPattern)
    WHERE qp.ask_count >= $threshold
    RETURN qp.question AS question, qp.ask_count AS count
    ORDER BY qp.ask_count DESC
    LIMIT 10
    """
    try:
        rows = neo.run(q, threshold=settings.ASEI_QUERY_MEMORY_HOT_SPOT_COUNT)
        report.hot_spots = [r["question"][:80] for r in rows]
        if report.hot_spots:
            log.info("  Hot spots: %d recurring questions", len(report.hot_spots))
    except Exception as exc:
        report.errors.append(f"Hot spot analysis failed: {exc}")


def _analyse_low_confidence_gaps(neo: Neo4jClient, report: QueryMemoryReport) -> None:
    """
    Find questions that consistently get low-confidence answers.
    These indicate KG gaps that need more content or agent attention.
    """
    q = """
    MATCH (qp:QueryPattern)
    WHERE qp.last_confidence IS NOT NULL
      AND qp.last_confidence < $threshold
      AND qp.ask_count >= 1
    RETURN qp.question AS question, qp.last_confidence AS confidence
    ORDER BY qp.last_confidence ASC
    LIMIT 10
    """
    try:
        rows = neo.run(q, threshold=settings.ASEI_QUERY_MEMORY_LOW_CONF_THRESHOLD)
        report.low_conf_gaps = [
            f"{r['question'][:60]} (conf={r['confidence']:.2f})" for r in rows
        ]
        if report.low_conf_gaps:
            log.info(
                "  Low-confidence gaps: %d questions need better KG coverage",
                len(report.low_conf_gaps),
            )
    except Exception as exc:
        report.errors.append(f"Low-confidence gap analysis failed: {exc}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Query Memory Agent").parse_args()
    print(json.dumps(run().to_dict(), indent=2))
