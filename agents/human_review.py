"""
agents/human_review.py
=======================
ASEI Human-in-the-Loop Review Gate for Hypothesis Verification.

High-impact hypotheses (contradictions, cross-module structural changes,
high-confidence relationship modifications) are flagged for human review
before being permanently committed to the Knowledge Graph.

Architecture:
  1. The Verification Agent marks hypotheses as `review_status = "pending_review"`
     instead of immediately accepting/rejecting when they meet HITL criteria
  2. ReviewItem nodes are created in Neo4j with full context for the reviewer
  3. A human reviewer uses the CLI or future web UI to approve/reject
  4. Approved items are committed; rejected items are permanently rejected

HITL Criteria (any one triggers human review):
  - hypothesis_type == "CONTRADICTS" (safety-critical in AUTOSAR)
  - confidence in the "uncertain zone" (0.40 <= combined_score <= 0.60)
  - affects nodes with > 5 downstream dependencies (high blast radius)
  - cross-module relationships (Module A → Module B)

Usage:
    # List pending reviews
    python asei_runner.py review --list

    # Approve a specific review
    python asei_runner.py review --approve <review_id>

    # Reject a specific review
    python asei_runner.py review --reject <review_id> --reason "Not valid because..."

    # Approve all pending (batch mode for trusted environments)
    python asei_runner.py review --approve-all
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.neo4j_client import Neo4jClient

log = get_logger("human_review")

# ── HITL Thresholds ───────────────────────────────────────────────────────────

# Confidence zone where human review is triggered (too uncertain for auto-decision)
HITL_UNCERTAIN_LOW  = 0.40
HITL_UNCERTAIN_HIGH = 0.60

# Minimum downstream dependencies to trigger blast-radius review
HITL_BLAST_RADIUS_THRESHOLD = 5

# Hypothesis types that ALWAYS require human review
HITL_ALWAYS_REVIEW_TYPES = {"CONTRADICTS"}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ReviewItem:
    review_id:       str   = ""
    from_id:         str   = ""
    from_name:       str   = ""
    to_id:           str   = ""
    to_name:         str   = ""
    hypothesis_type: str   = ""
    rationale:       str   = ""
    confidence:      float = 0.0
    trigger_reason:  str   = ""
    review_status:   str   = "pending"  # pending | approved | rejected
    reviewer_note:   str   = ""
    created_at:      str   = ""
    reviewed_at:     str   = ""

    def to_dict(self) -> dict:
        return {
            "review_id":       self.review_id,
            "from_id":         self.from_id,
            "from_name":       self.from_name,
            "to_id":           self.to_id,
            "to_name":         self.to_name,
            "hypothesis_type": self.hypothesis_type,
            "rationale":       self.rationale,
            "confidence":      self.confidence,
            "trigger_reason":  self.trigger_reason,
            "review_status":   self.review_status,
            "reviewer_note":   self.reviewer_note,
            "created_at":      self.created_at,
            "reviewed_at":     self.reviewed_at,
        }


@dataclass
class ReviewReport:
    pending_count:  int        = 0
    approved_count: int        = 0
    rejected_count: int        = 0
    items:          list[dict] = field(default_factory=list)
    errors:         list[str]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pending_count":  self.pending_count,
            "approved_count": self.approved_count,
            "rejected_count": self.rejected_count,
            "items":          self.items,
            "errors":         self.errors,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Core HITL Logic — called by Verification Agent
# ══════════════════════════════════════════════════════════════════════════════

def should_require_human_review(
    hyp: dict,
    combined_disproof: float,
    neo: Neo4jClient | None = None,
) -> tuple[bool, str]:
    """
    Determine if a hypothesis requires human review before commitment.

    Args:
        hyp: Hypothesis dict with from_id, to_id, hypothesis_type, confidence
        combined_disproof: The combined LLM + KG counterevidence score
        neo: Optional Neo4jClient for blast-radius check

    Returns:
        (should_review: bool, reason: str)
    """
    hypothesis_type = hyp.get("hypothesis_type", "")

    # Rule 1: CONTRADICTS always requires human review (safety-critical)
    if hypothesis_type.upper() in HITL_ALWAYS_REVIEW_TYPES:
        return True, f"hypothesis_type='{hypothesis_type}' requires human review (safety-critical)"

    # Rule 2: Uncertain zone — model can't decide with confidence
    if HITL_UNCERTAIN_LOW <= combined_disproof <= HITL_UNCERTAIN_HIGH:
        return True, f"combined_disproof={combined_disproof:.2f} is in uncertain zone [{HITL_UNCERTAIN_LOW}, {HITL_UNCERTAIN_HIGH}]"

    # Rule 3: High blast radius — affects many downstream nodes
    if neo:
        blast_radius = _compute_blast_radius(neo, hyp)
        if blast_radius >= HITL_BLAST_RADIUS_THRESHOLD:
            return True, f"blast_radius={blast_radius} >= threshold ({HITL_BLAST_RADIUS_THRESHOLD})"

    return False, ""


def create_review_item(
    neo: Neo4jClient,
    hyp: dict,
    trigger_reason: str,
    disproof_conf: float,
) -> str:
    """
    Create a ReviewItem node in Neo4j for human review.
    Returns the review_id.
    """
    now = _now_iso()
    review_id = f"review_{hyp['from_id']}_{hyp['to_id']}_{now.replace(':', '').replace('-', '')[:15]}"

    cypher = """
    CREATE (r:ReviewItem {
        review_id:       $review_id,
        from_id:         $from_id,
        from_name:       $from_name,
        to_id:           $to_id,
        to_name:         $to_name,
        hypothesis_type: $hypothesis_type,
        rationale:       $rationale,
        confidence:      $confidence,
        trigger_reason:  $trigger_reason,
        review_status:   'pending',
        created_at:      $now
    })
    """
    try:
        neo.run(
            cypher,
            review_id=review_id,
            from_id=hyp.get("from_id", ""),
            from_name=hyp.get("from_name", ""),
            to_id=hyp.get("to_id", ""),
            to_name=hyp.get("to_name", ""),
            hypothesis_type=hyp.get("hypothesis_type", ""),
            rationale=hyp.get("rationale", "")[:500],
            confidence=disproof_conf,
            trigger_reason=trigger_reason,
            now=now,
        )
        log.info("  HITL: created review item %s (%s)", review_id, trigger_reason)

        # Also mark the hypothesis edge as pending_review
        mark_cypher = """
        MATCH (a {id: $from_id})-[r:HYPOTHESIZES]->(b {id: $to_id})
        SET r.review_status = 'pending_review',
            r.review_id     = $review_id
        """
        neo.run(mark_cypher, from_id=hyp["from_id"], to_id=hyp["to_id"], review_id=review_id)

    except Exception as exc:
        log.warning("Failed to create review item: %s", exc)

    return review_id


# ══════════════════════════════════════════════════════════════════════════════
# Review Management — CLI operations
# ══════════════════════════════════════════════════════════════════════════════

def list_pending_reviews(neo: Neo4jClient | None = None) -> ReviewReport:
    """List all pending review items."""
    report = ReviewReport()
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        cypher = """
        MATCH (r:ReviewItem)
        WHERE r.review_status = 'pending'
        RETURN r.review_id AS review_id,
               r.from_id AS from_id,
               r.from_name AS from_name,
               r.to_id AS to_id,
               r.to_name AS to_name,
               r.hypothesis_type AS hypothesis_type,
               r.rationale AS rationale,
               r.confidence AS confidence,
               r.trigger_reason AS trigger_reason,
               r.created_at AS created_at
        ORDER BY r.created_at DESC
        """
        rows = neo.run(cypher)
        report.pending_count = len(rows)
        report.items = [dict(r) for r in rows]

        # Also count approved/rejected
        counts = neo.run("""
        MATCH (r:ReviewItem)
        RETURN r.review_status AS status, count(r) AS n
        """)
        for row in counts:
            if row["status"] == "approved":
                report.approved_count = row["n"]
            elif row["status"] == "rejected":
                report.rejected_count = row["n"]

    except Exception as exc:
        report.errors.append(f"List reviews failed: {exc}")
    finally:
        if close_neo:
            neo.close()

    return report


def approve_review(
    review_id: str,
    reviewer_note: str = "",
    neo: Neo4jClient | None = None,
) -> bool:
    """
    Approve a pending review item and commit the hypothesis to the graph.
    Sets verified=true on the HYPOTHESIZES edge.
    """
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        now = _now_iso()

        # Update the ReviewItem
        neo.run("""
        MATCH (r:ReviewItem {review_id: $review_id})
        SET r.review_status = 'approved',
            r.reviewer_note = $note,
            r.reviewed_at   = $now
        """, review_id=review_id, note=reviewer_note, now=now)

        # Commit the hypothesis: set verified=true
        neo.run("""
        MATCH (a)-[r:HYPOTHESIZES]->(b)
        WHERE r.review_id = $review_id
        SET r.verified            = true,
            r.verified_at         = $now,
            r.review_status       = 'approved',
            r.verification_reason = 'Human approved: ' + $note
        """, review_id=review_id, now=now, note=reviewer_note)

        log.info("  Review %s APPROVED", review_id)
        return True

    except Exception as exc:
        log.error("Approve failed for %s: %s", review_id, exc)
        return False
    finally:
        if close_neo:
            neo.close()


def reject_review(
    review_id: str,
    reason: str = "",
    neo: Neo4jClient | None = None,
) -> bool:
    """
    Reject a pending review item. Marks the hypothesis as rejected.
    """
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        now = _now_iso()

        # Update the ReviewItem
        neo.run("""
        MATCH (r:ReviewItem {review_id: $review_id})
        SET r.review_status = 'rejected',
            r.reviewer_note = $reason,
            r.reviewed_at   = $now
        """, review_id=review_id, reason=reason, now=now)

        # Reject the hypothesis: set verified=false
        neo.run("""
        MATCH (a)-[r:HYPOTHESIZES]->(b)
        WHERE r.review_id = $review_id
        SET r.verified            = false,
            r.verified_at         = $now,
            r.review_status       = 'rejected',
            r.verification_reason = 'Human rejected: ' + $reason
        """, review_id=review_id, now=now, reason=reason)

        log.info("  Review %s REJECTED: %s", review_id, reason)
        return True

    except Exception as exc:
        log.error("Reject failed for %s: %s", review_id, exc)
        return False
    finally:
        if close_neo:
            neo.close()


def approve_all_pending(neo: Neo4jClient | None = None) -> int:
    """Approve all pending reviews (batch mode for trusted environments)."""
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        now = _now_iso()
        result = neo.run("""
        MATCH (r:ReviewItem)
        WHERE r.review_status = 'pending'
        SET r.review_status = 'approved',
            r.reviewer_note = 'Batch approved',
            r.reviewed_at   = $now
        WITH r
        MATCH (a)-[h:HYPOTHESIZES]->(b)
        WHERE h.review_id = r.review_id
        SET h.verified            = true,
            h.verified_at         = $now,
            h.review_status       = 'approved',
            h.verification_reason = 'Batch approved by human'
        RETURN count(r) AS approved
        """, now=now)
        count = result[0]["approved"] if result else 0
        log.info("  Batch approved %d pending reviews", count)
        return count
    except Exception as exc:
        log.error("Batch approve failed: %s", exc)
        return 0
    finally:
        if close_neo:
            neo.close()


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _compute_blast_radius(neo: Neo4jClient, hyp: dict) -> int:
    """
    Count how many downstream nodes are connected to the hypothesis endpoints.
    High blast radius = many things would be affected if this hypothesis is wrong.
    """
    try:
        cypher = """
        MATCH (n {id: $node_id})-[*1..2]-(downstream)
        WHERE downstream.id IS NOT NULL AND downstream.id <> $node_id
        RETURN count(DISTINCT downstream) AS radius
        """
        # Check both endpoints
        r1 = neo.run(cypher, node_id=hyp.get("from_id", ""))
        r2 = neo.run(cypher, node_id=hyp.get("to_id", ""))
        radius = max(
            r1[0]["radius"] if r1 else 0,
            r2[0]["radius"] if r2 else 0,
        )
        return radius
    except Exception:
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def run_cli(args: argparse.Namespace) -> None:
    """Handle review CLI commands."""
    if args.list:
        report = list_pending_reviews()
        if report.pending_count == 0:
            print("No pending reviews.")
        else:
            print(f"\n{'='*70}")
            print(f" PENDING REVIEWS ({report.pending_count})")
            print(f"{'='*70}\n")
            for item in report.items:
                print(f"  ID: {item.get('review_id', '?')}")
                print(f"  Hypothesis: {item.get('from_name', '?')} → {item.get('hypothesis_type', '?')} → {item.get('to_name', '?')}")
                print(f"  Confidence: {item.get('confidence', 0):.2f}")
                print(f"  Trigger: {item.get('trigger_reason', '?')}")
                print(f"  Rationale: {item.get('rationale', '')[:100]}")
                print(f"  Created: {item.get('created_at', '?')}")
                print()
        print(json.dumps(report.to_dict(), indent=2))

    elif args.approve:
        success = approve_review(args.approve, reviewer_note=args.reason or "")
        print("APPROVED" if success else "FAILED")

    elif args.reject:
        if not args.reason:
            print("ERROR: --reason is required for rejection")
            return
        success = reject_review(args.reject, reason=args.reason)
        print("REJECTED" if success else "FAILED")

    elif args.approve_all:
        count = approve_all_pending()
        print(f"Batch approved {count} pending reviews")

    else:
        print("Use --list, --approve <id>, --reject <id> --reason '...', or --approve-all")
