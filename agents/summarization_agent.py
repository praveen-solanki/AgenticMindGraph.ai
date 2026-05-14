"""
agents/summarization_agent.py
==============================
ASEI Summarization Agent — maintains living summaries on Module and Document nodes.

Incremental approach: reads existing summary + new chunks since last_summarized_at,
asks the LLM to update (not regenerate) the summary. Writes back to the node.
These summaries are the Reasoning Agent's first-stop context before deep traversal.

Run standalone:
    python -m agents.summarization_agent
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm
from utils.neo4j_client import Neo4jClient

log = get_logger("summarization_agent")

_SUMMARY_SYSTEM = """\
You are a technical document summarizer for AUTOSAR specifications.

You are given:
1. An existing summary of a module (may be empty for first run)
2. New text chunks from that module not yet included in the summary

Produce an updated, concise summary (max 300 words) that incorporates both
the existing summary and the new information. Keep it factual and precise.
Focus on: module purpose, key requirements, interfaces, and dependencies.

Return ONLY the updated summary text — no JSON, no markdown, no preamble.
"""


@dataclass
class SummarizationReport:
    run_at:           str      = ""
    modules_updated:  int      = 0
    modules_skipped:  int      = 0
    errors:           list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":          self.run_at,
            "modules_updated": self.modules_updated,
            "modules_skipped": self.modules_skipped,
            "errors":          self.errors,
        }


def run(neo: Neo4jClient | None = None) -> SummarizationReport:
    report    = SummarizationReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Summarization Agent: starting")
        modules = _fetch_modules_needing_update(neo, report)
        log.info("  %d modules need summary update", len(modules))

        for mod in modules:
            _update_module_summary(neo, mod, report)

        log.info(
            "Summarization Agent complete: updated=%d skipped=%d",
            report.modules_updated, report.modules_skipped,
        )
    except Exception as exc:
        msg = f"Summarization Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


def _fetch_modules_needing_update(neo: Neo4jClient, report: SummarizationReport) -> list[dict]:
    """
    Find Module nodes where new chunks exist since last_summarized_at.
    Also includes modules with no summary at all.
    """
    q = """
    MATCH (m:Module)<-[:SOURCED_FROM]-(c:Chunk)
    WHERE m.last_summarized_at IS NULL
       OR c.ingested_at > m.last_summarized_at
    WITH m, collect(c)[..{limit}] AS new_chunks
    WHERE size(new_chunks) > 0
    RETURN m.name AS module_name,
           coalesce(m.summary, '') AS existing_summary,
           [c IN new_chunks | coalesce(c.cleaned_text, c.text, '')]
             AS chunk_texts
    """.format(limit=settings.ASEI_SUMMARY_MAX_CHUNKS_PER_MODULE)
    try:
        rows = neo.run(q)
        return [dict(r) for r in rows]
    except Exception as exc:
        report.errors.append(f"Module fetch failed: {exc}")
        return []


def _update_module_summary(neo: Neo4jClient, mod: dict, report: SummarizationReport) -> None:
    """Generate incremental summary update and write back to Module node."""
    module_name = mod["module_name"]
    existing    = mod.get("existing_summary", "")
    chunks      = mod.get("chunk_texts", [])

    if not chunks:
        report.modules_skipped += 1
        return

    # Assemble new chunk text (capped)
    new_text = "\n\n".join(str(c)[:800] for c in chunks)
    if len(new_text) > settings.ASEI_SUMMARY_CONTEXT_CHARS:
        new_text = new_text[:settings.ASEI_SUMMARY_CONTEXT_CHARS] + "\n[truncated]"

    user = (
        f"Module: {module_name}\n\n"
        f"EXISTING SUMMARY:\n{existing or '(none)'}\n\n"
        f"NEW CHUNKS TO INCORPORATE:\n{new_text}"
    )

    updated_summary = call_agent_llm(
        "summarization", _SUMMARY_SYSTEM, user, max_tokens=400, temperature=0.1
    )
    if not updated_summary:
        report.modules_skipped += 1
        log.warning("  Summarization: no output for module '%s'", module_name)
        return

    # Write summary back to Module node
    now = _now_iso()
    q = """
    MATCH (m:Module {name: $name})
    SET m.summary            = $summary,
        m.last_summarized_at = $now
    """
    try:
        neo.run(q, name=module_name, summary=updated_summary.strip(), now=now)
        report.modules_updated += 1
        log.info("  Summarization: updated summary for '%s'", module_name)
    except Exception as exc:
        msg = f"Summary write failed for {module_name}: {exc}"
        log.warning(msg)
        report.errors.append(msg)
        report.modules_skipped += 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Summarization Agent").parse_args()
    print(json.dumps(run().to_dict(), indent=2))
