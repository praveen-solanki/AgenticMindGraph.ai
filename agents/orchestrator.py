"""
agents/orchestrator.py
======================
ASEI Orchestrator — LangGraph-style state machine coordinating all agents.

Responsibilities
----------------
1. Cycle management:
   - Run the four ASEI agents in the correct order each cycle:
       (a) Evolution  — detect stale/drifted nodes
       (b) Conflict   — detect contradictions
       (c) Synthesis  — propose new hypotheses
       (d) Reasoning  — answer pending questions (optional / API mode)

2. State tracking:
   - Maintains an OrchestratorState dataclass that is threaded through all
     agent runs and written to checkpoint after each agent completes.
   - Supports resume from last checkpoint on crash/restart.

3. Continuous service mode:
   - When run as a service (--serve), cycles repeat every ASEI_CYCLE_INTERVAL_S
     seconds. Each cycle reuses a single Neo4jClient connection.

4. CLI mode:
   - Single-cycle run for batch pipelines or cron jobs.
   - Supports --question flag to run reasoning on-demand.

Run as a service:
    python -m agents.orchestrator --serve

Run a single cycle:
    python -m agents.orchestrator

Ask a question:
    python -m agents.orchestrator --question "What are NvM requirements?"
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import settings
from utils.logger import get_logger
from utils.neo4j_client import Neo4jClient

# Agent imports (lazy inside functions to avoid circular at module load)
log = get_logger("orchestrator")

# ── State dataclass ───────────────────────────────────────────────────────────

@dataclass
class OrchestratorState:
    """Full state threaded through one ASEI cycle."""
    cycle_id:               str        = ""
    started_at:             str        = ""
    completed_at:           str        = ""
    current_agent:          str        = ""
    evolution_report:       dict       = field(default_factory=dict)
    conflict_report:        dict       = field(default_factory=dict)
    synthesis_report:       dict       = field(default_factory=dict)
    verification_report:    dict       = field(default_factory=dict)
    summarization_report:   dict       = field(default_factory=dict)
    gap_report:             dict       = field(default_factory=dict)
    impact_report:          dict       = field(default_factory=dict)
    watchdog_report:        dict       = field(default_factory=dict)
    reasoning_results:      list[dict] = field(default_factory=list)
    total_flags:            int        = 0
    total_conflicts:        int        = 0
    total_hypotheses:       int        = 0
    total_gaps:             int        = 0
    total_impacted:         int        = 0
    errors:                 list[str]  = field(default_factory=list)
    status:                 str        = "pending"

    def to_dict(self) -> dict:
        return asdict(self)


# ── Main orchestration logic ──────────────────────────────────────────────────

def run_cycle(
    neo:       Neo4jClient | None = None,
    question:  str | None = None,
    state_dir: Path | None = None,
) -> OrchestratorState:
    """
    Execute one full ASEI cycle: Evolution → Conflict → Synthesis → (Reasoning).

    Args:
        neo:       Shared Neo4jClient. Created internally if not provided.
        question:  Optional question to answer via the Reasoning agent.
        state_dir: Directory to write state JSON for checkpointing.

    Returns:
        Completed OrchestratorState.
    """
    state = OrchestratorState(
        cycle_id   = _cycle_id(),
        started_at = _now_iso(),
        status     = "running",
    )
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Orchestrator: cycle %s started", state.cycle_id)

        # ── 1. Evolution Agent ────────────────────────────────────────────────
        state.current_agent = "evolution"
        _checkpoint(state, state_dir)
        from agents.evolution_agent import run as run_evolution
        evo = run_evolution(neo=neo)
        state.evolution_report = evo.to_dict()
        state.total_flags      = evo.total_flagged
        state.errors.extend(evo.errors)
        log.info("  [1/8] Evolution: %d flagged", evo.total_flagged)

        # ── 2. Conflict Agent ─────────────────────────────────────────────────
        state.current_agent = "conflict"
        _checkpoint(state, state_dir)
        from agents.conflict_agent import run as run_conflict
        con = run_conflict(neo=neo)
        state.conflict_report  = con.to_dict()
        state.total_conflicts  = con.total_conflicts
        state.errors.extend(con.errors)
        log.info("  [2/8] Conflict: %d conflicts", con.total_conflicts)

        # ── 3. Synthesis Agent ────────────────────────────────────────────────
        state.current_agent = "synthesis"
        _checkpoint(state, state_dir)
        from agents.synthesis_agent import run as run_synthesis
        syn = run_synthesis(neo=neo)
        state.synthesis_report = syn.to_dict()
        state.total_hypotheses = syn.hypotheses_written
        state.errors.extend(syn.errors)
        log.info("  [3/8] Synthesis: %d hypotheses written", syn.hypotheses_written)

        # ── 4. Verification Agent (gates Synthesis output) ────────────────────
        state.current_agent = "verification"
        _checkpoint(state, state_dir)
        from agents.verification_agent import run as run_verification
        ver = run_verification(neo=neo)
        state.verification_report = ver.to_dict()
        state.errors.extend(ver.errors)
        log.info("  [4/8] Verification: accepted=%d rejected=%d", ver.accepted, ver.rejected)

        # ── 5. Summarization Agent ────────────────────────────────────────────
        state.current_agent = "summarization"
        _checkpoint(state, state_dir)
        from agents.summarization_agent import run as run_summarization
        summ = run_summarization(neo=neo)
        state.summarization_report = summ.to_dict()
        state.errors.extend(summ.errors)
        log.info("  [5/8] Summarization: %d modules updated", summ.modules_updated)

        # ── 6. Gap Detection Agent ────────────────────────────────────────────
        state.current_agent = "gap_detection"
        _checkpoint(state, state_dir)
        from agents.gap_detection_agent import run as run_gap
        gap = run_gap(neo=neo)
        state.gap_report   = gap.to_dict()
        state.total_gaps   = gap.gaps_found
        state.errors.extend(gap.errors)
        log.info("  [6/8] Gap Detection: %d gaps found", gap.gaps_found)

        # ── 7. Impact Agent ───────────────────────────────────────────────────
        state.current_agent = "impact"
        _checkpoint(state, state_dir)
        from agents.impact_agent import run as run_impact
        imp = run_impact(neo=neo)
        state.impact_report   = imp.to_dict()
        state.total_impacted  = imp.impacted_nodes
        state.errors.extend(imp.errors)
        log.info("  [7/8] Impact: %d nodes traced", imp.impacted_nodes)

        # ── 8. Reasoning Agent (only if question provided) ────────────────────
        reasoning_result = None
        if question:
            state.current_agent = "reasoning"
            _checkpoint(state, state_dir)
            from agents.reasoning_agent import run as run_reasoning
            reasoning_result = run_reasoning(question=question, neo=neo)
            state.reasoning_results.append(reasoning_result.to_dict())
            state.errors.extend(reasoning_result.errors)
            log.info(
                "  [8/8] Reasoning: confidence=%.2f for: %.60s",
                reasoning_result.confidence, question,
            )
        else:
            log.info("  [8/8] Reasoning: skipped (no question)")

        # ── Watchdog (always last — monitors the full cycle) ──────────────────
        state.current_agent = "watchdog"
        from agents.watchdog_agent import run as run_watchdog
        from agents.query_memory_agent import run as run_query_memory
        wd = run_watchdog(cycle_state=state.to_dict(), neo=neo)
        state.watchdog_report = wd.to_dict()
        state.errors.extend(wd.errors)
        if reasoning_result:
            qm = run_query_memory(reasoning_result=reasoning_result.to_dict(), neo=neo)
            state.errors.extend(qm.errors)

        # ── Finalise ──────────────────────────────────────────────────────────
        state.completed_at  = _now_iso()
        state.current_agent = ""
        state.status        = "failed" if state.errors else "complete"
        _checkpoint(state, state_dir)

        log.info(
            "Orchestrator: cycle %s %s — flags=%d conflicts=%d hypotheses=%d gaps=%d impacted=%d errors=%d",
            state.cycle_id, state.status,
            state.total_flags, state.total_conflicts,
            state.total_hypotheses, state.total_gaps,
            state.total_impacted, len(state.errors),
        )

    except Exception as exc:
        msg = f"Orchestrator cycle error: {exc}"
        log.error(msg)
        state.errors.append(msg)
        state.status = "failed"
        _checkpoint(state, state_dir)
    finally:
        if close_neo:
            neo.close()

    return state


def run_service(
    cycle_interval_s: int | None = None,
    state_dir: Path | None = None,
) -> None:
    """
    Continuous service loop: run a full ASEI cycle every `cycle_interval_s`
    seconds using a shared Neo4j connection.
    """
    interval = cycle_interval_s or settings.ASEI_CYCLE_INTERVAL_S
    log.info(
        "Orchestrator service starting — cycle interval: %ds", interval
    )
    neo = Neo4jClient()
    cycle_num = 0
    try:
        while True:
            cycle_num += 1
            log.info("=== ASEI Cycle #%d ===", cycle_num)
            state = run_cycle(neo=neo, state_dir=state_dir)
            log.info(
                "Cycle #%d complete (%s). Next in %ds.",
                cycle_num, state.status, interval,
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("Orchestrator service stopped by user.")
    finally:
        neo.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _checkpoint(state: OrchestratorState, state_dir: Path | None) -> None:
    """Write current state to disk for crash recovery."""
    if state_dir is None:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"orchestrator_{state.cycle_id}.json"
    try:
        path.write_text(json.dumps(state.to_dict(), indent=2))
    except Exception as exc:
        log.warning("Checkpoint write failed: %s", exc)


def _cycle_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ASEI Orchestrator — coordinates all ASEI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--serve", action="store_true",
        help="Run as a continuous service (cycles every ASEI_CYCLE_INTERVAL_S seconds).",
    )
    p.add_argument(
        "--question", default=None,
        help="Question to answer via the Reasoning Agent in this cycle.",
    )
    p.add_argument(
        "--state-dir", default="./output/asei_state",
        help="Directory for cycle state checkpoints (default: ./output/asei_state).",
    )
    return p.parse_args()


if __name__ == "__main__":
    args      = _parse_args()
    state_dir = Path(args.state_dir)

    if args.serve:
        run_service(state_dir=state_dir)
    else:
        state = run_cycle(question=args.question, state_dir=state_dir)
        print(json.dumps(state.to_dict(), indent=2))
