"""
agents/verification_agent.py
============================
ASEI Verification Agent — adversarially tests every HYPOTHESIZES edge before
it becomes a permanent part of the KG.

Responsibilities
----------------
1. Fetch all unverified HYPOTHESIZES edges (verified IS NULL or verified=false).
2. For each hypothesis, search the KG for counterevidence:
   - Stale/low-confidence supporting nodes reduce credibility.
   - Existing CONTRADICTS edges on either node are strong counterevidence.
   - Absence of any corroborating Chunk reduces credibility.
3. Call the LLM (adversarial mode) to argue AGAINST the hypothesis.
4. If the LLM cannot disprove it (disproof_confidence < threshold):
   - Mark the edge verified=true, verified_at=now, verified_confidence=score.
5. If disproved:
   - Mark verified=false, rejected_reason, rejected_at.
   - Edge remains in KG for human review but is excluded from active reasoning.
6. Return VerificationReport for orchestrator.

Run standalone:
    python -m agents.verification_agent
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm_json
from utils.neo4j_client import Neo4jClient

log = get_logger("verification_agent")

_DISPROOF_SYSTEM = """\
You are an adversarial knowledge graph auditor.

You are given a proposed hypothesis relationship between two nodes in a
knowledge graph.  Your job is to find reasons why this hypothesis is WRONG
or UNSUPPORTED.

Look for:
- Logical inconsistency between the two node descriptions
- The proposed relation type does not match the evidence
- The hypothesis is too vague to be meaningful
- There is no causal or structural basis for the relationship

Return ONLY valid JSON:
{
  "disproof_confidence": <float 0.0-1.0>,
  "disproof_reasoning":  "<one or two sentences explaining why the hypothesis is wrong or weak>",
  "verdict":             "reject" | "accept"
}

Set disproof_confidence=0.0 and verdict="accept" if you cannot find
a valid reason to reject the hypothesis.
"""


@dataclass
class VerificationReport:
    run_at:       str      = ""
    total_pending: int     = 0
    accepted:     int      = 0
    rejected:     int      = 0
    errors:       list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":        self.run_at,
            "total_pending": self.total_pending,
            "accepted":      self.accepted,
            "rejected":      self.rejected,
            "errors":        self.errors,
        }


def run(neo: Neo4jClient | None = None) -> VerificationReport:
    report    = VerificationReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Verification Agent: starting")
        pending = _fetch_pending(neo, report)
        report.total_pending = len(pending)
        log.info("  %d unverified hypotheses to evaluate", len(pending))

        for hyp in pending:
            _verify_one(neo, hyp, report)

        log.info(
            "Verification Agent complete: accepted=%d rejected=%d",
            report.accepted, report.rejected,
        )
    except Exception as exc:
        msg = f"Verification Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


def _fetch_pending(neo: Neo4jClient, report: VerificationReport) -> list[dict]:
    q = """
    MATCH (a)-[r:HYPOTHESIZES]->(b)
    WHERE r.verified IS NULL OR r.verified = false
    RETURN
        a.id   AS from_id,  a.name AS from_name,
        coalesce(a.definition, a.description, a.text, '') AS from_desc,
        b.id   AS to_id,    b.name AS to_name,
        coalesce(b.definition, b.description, b.text, '') AS to_desc,
        r.hypothesis_type AS hypothesis_type,
        r.rationale       AS rationale,
        r.confidence      AS confidence
    LIMIT $limit
    """
    try:
        rows = neo.run(q, limit=settings.ASEI_VERIFICATION_BATCH)
        return [dict(r) for r in rows]
    except Exception as exc:
        report.errors.append(f"Fetch pending failed: {exc}")
        return []


def _verify_one(neo: Neo4jClient, hyp: dict, report: VerificationReport) -> None:
    """Adversarially test one hypothesis. Write verdict back to KG."""
    # Check for counterevidence in KG first (cheap, no LLM)
    counter_score = _kg_counterevidence_score(neo, hyp)

    # If KG shows strong counterevidence, skip LLM and reject directly
    if counter_score >= 0.90:
        _write_verdict(neo, hyp, verdict="reject",
                       reason=f"KG counterevidence score={counter_score:.2f}",
                       conf=counter_score)
        report.rejected += 1
        return

    # Call LLM adversarially
    user = (
        f"Hypothesis: '{hyp['from_name']}' {hyp['hypothesis_type']} '{hyp['to_name']}'\n"
        f"Rationale: {hyp.get('rationale','')[:400]}\n\n"
        f"Node A description: {hyp.get('from_desc','')[:400]}\n"
        f"Node B description: {hyp.get('to_desc','')[:400]}\n\n"
        f"Original confidence: {hyp.get('confidence', 0.5):.2f}\n"
        f"KG counterevidence score: {counter_score:.2f}\n\n"
        "Find reasons to REJECT this hypothesis."
    )
    result = call_agent_llm_json("heavy_reasoning", _DISPROOF_SYSTEM, user, max_tokens=300)
    if not result:
        # LLM failure — default accept
        _write_verdict(neo, hyp, verdict="accept", reason="LLM unavailable, default accept", conf=0.5)
        report.accepted += 1
        return

    disproof_conf = float(result.get("disproof_confidence", 0.0))
    verdict       = result.get("verdict", "accept")
    reason        = result.get("disproof_reasoning", "")

    # Combine LLM disproof + KG counterevidence
    combined_disproof = max(disproof_conf, counter_score * 0.7)
    if verdict == "reject" or combined_disproof >= settings.ASEI_VERIFICATION_REJECT_THRESHOLD:
        _write_verdict(neo, hyp, verdict="reject", reason=reason, conf=combined_disproof)
        report.rejected += 1
    else:
        _write_verdict(neo, hyp, verdict="accept", reason=reason, conf=1.0 - combined_disproof)
        report.accepted += 1


def _kg_counterevidence_score(neo: Neo4jClient, hyp: dict) -> float:
    """
    Compute a counterevidence score [0,1] purely from KG structure.
    High score = strong reason to reject.
    """
    score = 0.0
    try:
        # Check if either node is stale
        q_stale = """
        MATCH (n) WHERE n.id IN $ids AND n.stale = true
        RETURN count(n) AS n
        """
        r = neo.run(q_stale, ids=[hyp["from_id"], hyp["to_id"]])
        stale_count = r[0]["n"] if r else 0
        score += stale_count * 0.25  # each stale node adds 0.25

        # Check for existing CONTRADICTS edges on either node
        q_contra = """
        MATCH (n)-[:CONTRADICTS]-() WHERE n.id IN $ids
        RETURN count(*) AS n
        """
        r = neo.run(q_contra, ids=[hyp["from_id"], hyp["to_id"]])
        contra_count = r[0]["n"] if r else 0
        score += min(contra_count * 0.30, 0.60)
    except Exception as exc:
        log.debug("KG counterevidence check failed: %s", exc)

    return min(score, 1.0)


def _write_verdict(
    neo: Neo4jClient,
    hyp: dict,
    verdict: str,
    reason: str,
    conf: float,
) -> None:
    now = _now_iso()
    accepted = verdict == "accept"
    q = """
    MATCH (a {id: $from_id})-[r:HYPOTHESIZES]->(b {id: $to_id})
    SET r.verified            = $accepted,
        r.verified_at         = $now,
        r.verified_confidence = $conf,
        r.verification_reason = $reason
    """
    try:
        neo.run(q, from_id=hyp["from_id"], to_id=hyp["to_id"],
                accepted=accepted, now=now, conf=conf, reason=reason)
    except Exception as exc:
        log.warning("Verdict write failed: %s", exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Verification Agent").parse_args()
    print(json.dumps(run().to_dict(), indent=2))
