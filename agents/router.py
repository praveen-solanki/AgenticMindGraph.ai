"""
agents/router.py
================
ASEI Router — classifies agent tasks and returns the correct task_type key
for multi_llm_client.  Agents call router.route(description) instead of
hardcoding a task type, so model assignments stay in one place.

For simple cases agents can also call route() directly with a known key.
The router validates the key and falls back to "mid_reasoning" on unknown input.
"""

from __future__ import annotations

from utils.logger import get_logger
from utils.multi_llm_client import TASK_PROVIDER_CHAINS, call_agent_llm

log = get_logger("router")

# ── Known task types and their descriptions ───────────────────────────────────
_TASK_MAP: dict[str, str] = {
    "heavy_reasoning":  "conflict detection, verification, multi-hop reasoning with debate",
    "synthesis":        "hypothesis generation, inferring new graph relationships",
    "mid_reasoning":    "structured JSON extraction, debate leg, general reasoning",
    "fast_classify":    "binary classification, pattern matching, quick yes/no decisions",
    "summarization":    "document summarization, incremental module summaries",
    "gap_detection":    "formal spec analysis, finding missing requirements",
    "impact":           "tracing downstream effects of changes through the graph",
    "local_reasoning":  "local vLLM skeptic debate leg",
}

_CLASSIFY_SYSTEM = """\
You are a task router for an AI agent system.

Given a description of a task, classify it into exactly one of these task types:
heavy_reasoning, synthesis, mid_reasoning, fast_classify, summarization, gap_detection, impact, local_reasoning

Return ONLY the task type string, nothing else.
"""


def route(task_description_or_key: str) -> str:
    """
    Return the task_type key to use for multi_llm_client.

    If the input is already a known key, return it directly.
    If it's a natural-language description, classify it with a fast LLM call.
    Falls back to "mid_reasoning" on any failure.

    Args:
        task_description_or_key: Known task key OR natural-language description.

    Returns:
        A valid task_type key from TASK_PROVIDER_CHAINS.
    """
    # Already a known key — return directly
    if task_description_or_key in TASK_PROVIDER_CHAINS:
        return task_description_or_key

    # Classify with fast model (Cerebras Llama 3.1 8B)
    try:
        result = call_agent_llm(
            "fast_classify",
            _CLASSIFY_SYSTEM,
            f"Task: {task_description_or_key}",
            max_tokens=20,
            temperature=0.0,
        ).strip().lower()
        if result in TASK_PROVIDER_CHAINS:
            log.debug("Router: '%s' → '%s'", task_description_or_key[:60], result)
            return result
    except Exception as exc:
        log.debug("Router LLM classification failed: %s", exc)

    log.debug("Router: falling back to 'mid_reasoning' for: %s", task_description_or_key[:60])
    return "mid_reasoning"


def describe_chains() -> dict[str, str]:
    """Return human-readable description of each task chain (for logging/debug)."""
    from utils.multi_llm_client import TASK_PROVIDER_CHAINS
    return {
        key: " → ".join(p["model"] for p in chain)
        for key, chain in TASK_PROVIDER_CHAINS.items()
    }
