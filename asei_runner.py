"""
asei_runner.py
==============
Top-level CLI entry point for the ASEI (Autonomous Self-Evolving Research
Intelligence) system. Mirrors the `pipeline/main.py` pattern used by ingestion.

Subcommands
-----------
  cycle          Run one full ASEI cycle:
                 Evolution -> Conflict -> Synthesis -> Verification ->
                 Summarization -> Gap Detection -> Impact -> Reasoning(optional)
                 -> Watchdog -> Query Memory(optional).
  serve          Run ASEI continuously.
  ask            Ask the Reasoning Agent a question over the live KG.
  evolution      Run only the Evolution Agent.
  conflict       Run only the Conflict Agent.
  synthesis      Run only the Synthesis Agent.
  verification   Run only the Verification Agent.
  summarization  Run only the Summarization Agent.
  gap-detection  Run only the Gap Detection Agent.
  impact         Run only the Impact Agent.
  watchdog       Run only the Watchdog Agent.
  query-memory   Run only the Query Memory Agent.
  routes         Print task-to-provider routing chains.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from config import settings
from utils.logger import get_logger, set_debug

log = get_logger("asei_runner")


# ── Output helpers ────────────────────────────────────────────────────────────

def _to_dict(value: Any) -> dict:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    raise TypeError(f"Object is not serializable by asei_runner: {type(value).__name__}")


def _print_json(value: Any) -> dict:
    payload = _to_dict(value)
    print(json.dumps(payload, indent=2))
    return payload


def _exit_for_errors(payload: dict) -> None:
    sys.exit(1 if payload.get("errors") else 0)


# ── Sub-command handlers ──────────────────────────────────────────────────────

def _cmd_cycle(args: argparse.Namespace) -> None:
    """Run a single full ASEI cycle."""
    from agents.orchestrator import run_cycle

    state = run_cycle(
        question=args.question or None,
        state_dir=Path(args.state_dir) if args.state_dir else None,
    )
    payload = _print_json(state)
    sys.exit(0 if payload.get("status") == "complete" else 1)


def _cmd_serve(args: argparse.Namespace) -> None:
    """Run ASEI as a continuous service."""
    from agents.orchestrator import run_service

    run_service(
        cycle_interval_s=args.interval or None,
        state_dir=Path(args.state_dir) if args.state_dir else None,
    )


def _cmd_ask(args: argparse.Namespace) -> None:
    """Answer a question using the Reasoning Agent."""
    from agents.reasoning_agent import run as run_reasoning

    payload = _print_json(run_reasoning(question=args.question))
    _exit_for_errors(payload)


def _cmd_evolution(args: argparse.Namespace) -> None:
    from agents.evolution_agent import run as run_evolution

    payload = _print_json(run_evolution())
    _exit_for_errors(payload)


def _cmd_conflict(args: argparse.Namespace) -> None:
    from agents.conflict_agent import run as run_conflict

    payload = _print_json(run_conflict())
    _exit_for_errors(payload)


def _cmd_synthesis(args: argparse.Namespace) -> None:
    from agents.synthesis_agent import run as run_synthesis

    if args.min_confidence is not None:
        settings.ASEI_SYNTHESIS_MIN_CONFIDENCE = args.min_confidence

    payload = _print_json(run_synthesis())
    _exit_for_errors(payload)


def _cmd_verification(args: argparse.Namespace) -> None:
    from agents.verification_agent import run as run_verification

    payload = _print_json(run_verification())
    _exit_for_errors(payload)


def _cmd_summarization(args: argparse.Namespace) -> None:
    from agents.summarization_agent import run as run_summarization

    payload = _print_json(run_summarization())
    _exit_for_errors(payload)


def _cmd_gap_detection(args: argparse.Namespace) -> None:
    from agents.gap_detection_agent import run as run_gap_detection

    payload = _print_json(run_gap_detection())
    _exit_for_errors(payload)


def _cmd_impact(args: argparse.Namespace) -> None:
    from agents.impact_agent import run as run_impact

    payload = _print_json(run_impact(node_ids=args.node_id or None))
    _exit_for_errors(payload)


def _cmd_watchdog(args: argparse.Namespace) -> None:
    from agents.watchdog_agent import run as run_watchdog

    payload = _print_json(run_watchdog())
    _exit_for_errors(payload)


def _cmd_query_memory(args: argparse.Namespace) -> None:
    from agents.query_memory_agent import run as run_query_memory

    reasoning_result = None
    if args.question:
        reasoning_result = {
            "question": args.question,
            "answer": args.answer or "",
            "confidence": args.confidence,
            "evidence": [],
            "path_steps": [],
            "errors": [],
        }

    payload = _print_json(run_query_memory(reasoning_result=reasoning_result))
    _exit_for_errors(payload)


def _cmd_routes(args: argparse.Namespace) -> None:
    from agents.router import describe_chains

    print(json.dumps(describe_chains(), indent=2))
    sys.exit(0)


# ── Argument parsing ──────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asei_runner",
        description="ASEI - Autonomous Self-Evolving Research Intelligence CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--debug", action="store_true", help="Enable DEBUG logging")

    sub = p.add_subparsers(dest="command", required=True)

    p_cycle = sub.add_parser("cycle", help="Run one full ASEI agent cycle")
    p_cycle.add_argument("--question", default=None, help="Also run Reasoning Agent with this question")
    p_cycle.add_argument(
        "--state-dir",
        default=settings.ASEI_STATE_DIR,
        help="Directory for cycle state checkpoints",
    )

    p_serve = sub.add_parser("serve", help="Run ASEI as a continuous service")
    p_serve.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Cycle interval in seconds (overrides ASEI_CYCLE_INTERVAL_S)",
    )
    p_serve.add_argument(
        "--state-dir",
        default=settings.ASEI_STATE_DIR,
        help="Directory for cycle state checkpoints",
    )

    p_ask = sub.add_parser("ask", help="Answer a question via the Reasoning Agent")
    p_ask.add_argument("question", help="Question to answer")

    sub.add_parser("evolution", help="Run only the Evolution Agent")
    sub.add_parser("conflict", help="Run only the Conflict Agent")

    p_syn = sub.add_parser("synthesis", help="Run only the Synthesis Agent")
    p_syn.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help="Minimum hypothesis confidence to write",
    )

    sub.add_parser("verification", help="Run only the Verification Agent")
    sub.add_parser("summarization", help="Run only the Summarization Agent")
    sub.add_parser("gap-detection", help="Run only the Gap Detection Agent")

    p_impact = sub.add_parser("impact", help="Run only the Impact Agent")
    p_impact.add_argument(
        "--node-id",
        action="append",
        default=[],
        help="Changed node id to trace; repeat for multiple nodes",
    )

    sub.add_parser("watchdog", help="Run only the Watchdog Agent")

    p_qm = sub.add_parser("query-memory", help="Run only the Query Memory Agent")
    p_qm.add_argument("--question", default=None, help="Optional question to store as a query pattern")
    p_qm.add_argument("--answer", default="", help="Optional answer text for the stored query pattern")
    p_qm.add_argument("--confidence", type=float, default=0.0, help="Confidence for the stored query pattern")

    sub.add_parser("routes", help="Print task-to-provider routing chains")

    return p


_COMMAND_MAP = {
    "cycle": _cmd_cycle,
    "serve": _cmd_serve,
    "ask": _cmd_ask,
    "evolution": _cmd_evolution,
    "conflict": _cmd_conflict,
    "synthesis": _cmd_synthesis,
    "verification": _cmd_verification,
    "summarization": _cmd_summarization,
    "gap-detection": _cmd_gap_detection,
    "impact": _cmd_impact,
    "watchdog": _cmd_watchdog,
    "query-memory": _cmd_query_memory,
    "routes": _cmd_routes,
}


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.debug:
        set_debug(True)

    handler = _COMMAND_MAP.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    log.info("ASEI runner: %s", args.command)
    handler(args)


if __name__ == "__main__":
    main()
