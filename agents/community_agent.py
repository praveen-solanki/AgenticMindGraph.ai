"""
agents/community_agent.py
===========================
ASEI Community Detection & Global Summarization Agent.

Implements Microsoft GraphRAG-style community detection using Neo4j GDS
Leiden algorithm, then generates hierarchical summaries for each community
to enable "global queries" that span the entire corpus.

Architecture:
  1. Project the KG into a GDS in-memory graph (nodes + relationships)
  2. Run Leiden community detection at multiple resolutions
  3. Write community IDs back to nodes as properties
  4. For each community, collect member descriptions and generate a summary
  5. Store Community nodes with summaries for global query retrieval

Usage:
    python -m agents.community_agent
    # Or via orchestrator/asei_runner

Dependencies:
    - Neo4j Graph Data Science (GDS) plugin must be installed
    - If GDS is unavailable, falls back to Louvain via native Cypher
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm
from utils.neo4j_client import Neo4jClient

log = get_logger("community_agent")

# ── Settings ──────────────────────────────────────────────────────────────────

# Maximum members to include in a community summary prompt
_MAX_MEMBERS_FOR_SUMMARY = 30

# Maximum communities to summarize per run (avoid excessive LLM calls)
_MAX_COMMUNITIES_TO_SUMMARIZE = 50

# Minimum community size to generate a summary (skip singletons)
_MIN_COMMUNITY_SIZE = 3

# GDS graph projection name
_GRAPH_NAME = "asei_community_graph"

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class CommunityReport:
    run_at:               str       = ""
    communities_detected: int       = 0
    summaries_generated:  int       = 0
    gds_available:        bool      = False
    errors:               list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_at":               self.run_at,
            "communities_detected": self.communities_detected,
            "summaries_generated":  self.summaries_generated,
            "gds_available":        self.gds_available,
            "errors":               self.errors,
        }


# ── LLM Prompt ────────────────────────────────────────────────────────────────

_SUMMARY_SYSTEM = """\
You are generating a high-level technical summary for a community (cluster)
of related AUTOSAR specification entities in a knowledge graph.

You are given the names, types, and descriptions of entities in this community.
These entities are grouped together because they are densely interconnected
in the specification graph.

Generate a concise summary (max 200 words) that captures:
1. The primary technical domain/topic of this community
2. Key modules, requirements, or concepts involved
3. The main relationships and dependencies within the group
4. Why these entities form a cohesive cluster

Write in technical documentation style. Be precise and use AUTOSAR terminology.
Return ONLY the summary text — no JSON, no markdown, no preamble.
"""


# ── Main entry point ──────────────────────────────────────────────────────────

def run(neo: Neo4jClient | None = None) -> CommunityReport:
    """
    Run community detection and generate global summaries.

    Args:
        neo: Optional shared Neo4jClient.

    Returns:
        CommunityReport with detection counts and errors.
    """
    report = CommunityReport(run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Community Agent: starting community detection")

        # Check if GDS is available
        report.gds_available = _check_gds_available(neo)

        if report.gds_available:
            log.info("  Neo4j GDS detected — using Leiden algorithm")
            n_communities = _run_leiden_detection(neo, report)
        else:
            log.info("  Neo4j GDS not available — using Louvain fallback via native Cypher")
            n_communities = _run_louvain_fallback(neo, report)

        report.communities_detected = n_communities
        log.info("  Communities detected: %d", n_communities)

        # Generate summaries for top communities
        if n_communities > 0:
            n_summaries = _generate_community_summaries(neo, report)
            report.summaries_generated = n_summaries
            log.info("  Community summaries generated: %d", n_summaries)

        log.info(
            "Community Agent complete: %d communities, %d summaries",
            report.communities_detected, report.summaries_generated,
        )

    except Exception as exc:
        msg = f"Community Agent error: {exc}"
        log.error(msg)
        report.errors.append(msg)
    finally:
        if close_neo:
            neo.close()

    return report


# ══════════════════════════════════════════════════════════════════════════════
# GDS Leiden Detection
# ══════════════════════════════════════════════════════════════════════════════

def _check_gds_available(neo: Neo4jClient) -> bool:
    """Check if Neo4j GDS plugin is installed."""
    try:
        result = neo.run("RETURN gds.version() AS version")
        if result:
            log.info("  GDS version: %s", result[0].get("version"))
            return True
    except Exception:
        pass
    return False


def _run_leiden_detection(neo: Neo4jClient, report: CommunityReport) -> int:
    """
    Run Leiden community detection via Neo4j GDS.
    Projects the graph, runs the algorithm, writes back community IDs.
    """
    try:
        # Drop existing projection if it exists
        try:
            neo.run(f"CALL gds.graph.drop('{_GRAPH_NAME}', false)")
        except Exception:
            pass

        # Project the graph: all semantic nodes + their relationships
        # Exclude Chunk and Document nodes (structural, not semantic)
        project_cypher = f"""
        CALL gds.graph.project(
            '{_GRAPH_NAME}',
            ['Requirement', 'Module', 'Concept', 'Function', 'ConfigParameter',
             'StandardRef', 'DocumentRef', 'System', 'Organization',
             'FunctionalCluster', 'DataType', 'Protocol'],
            ['REFERENCES', 'DEPENDS_ON', 'IMPLEMENTS', 'ALLOCATED_TO',
             'HAS_REQUIREMENT', 'HAS_PARAMETER', 'CALLS', 'PART_OF',
             'SPECIALIZES', 'DERIVED_FROM', 'HYPOTHESIZES']
        )
        """
        neo.run(project_cypher)
        log.info("  GDS graph projected: %s", _GRAPH_NAME)

        # Run Leiden algorithm
        leiden_cypher = f"""
        CALL gds.leiden.write('{_GRAPH_NAME}', {{
            writeProperty: 'community_id',
            maxLevels: 10,
            gamma: 1.0,
            theta: 0.01
        }})
        YIELD communityCount, modularity
        RETURN communityCount, modularity
        """
        result = neo.run(leiden_cypher)
        if result:
            n_communities = result[0].get("communityCount", 0)
            modularity = result[0].get("modularity", 0.0)
            log.info("  Leiden: %d communities, modularity=%.3f", n_communities, modularity)
        else:
            n_communities = 0

        # Cleanup GDS projection
        try:
            neo.run(f"CALL gds.graph.drop('{_GRAPH_NAME}', false)")
        except Exception:
            pass

        return n_communities

    except Exception as exc:
        msg = f"Leiden detection failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)
        return 0


def _run_louvain_fallback(neo: Neo4jClient, report: CommunityReport) -> int:
    """
    Fallback community detection using weakly connected components
    when GDS is not available. Less sophisticated than Leiden but
    still identifies clusters of connected entities.
    """
    try:
        # Use native Cypher to find connected components via relationship traversal
        # Assign community IDs based on connected subgraphs
        cypher = """
        MATCH (n)
        WHERE n:Requirement OR n:Module OR n:Concept OR n:Function
        WITH collect(n) AS nodes
        UNWIND nodes AS node
        OPTIONAL MATCH path = (node)-[*1..2]-(neighbor)
        WHERE neighbor:Requirement OR neighbor:Module OR neighbor:Concept OR neighbor:Function
        WITH node, collect(DISTINCT neighbor) AS neighbors
        WITH node, size(neighbors) AS degree
        WHERE degree >= 2
        SET node.community_id = elementId(node)
        RETURN count(node) AS assigned
        """
        result = neo.run(cypher)
        assigned = result[0]["assigned"] if result else 0

        # Now propagate community IDs through connected components
        propagate_cypher = """
        MATCH (a)-[]-(b)
        WHERE a.community_id IS NOT NULL
          AND b.community_id IS NOT NULL
          AND a.community_id < b.community_id
        SET b.community_id = a.community_id
        RETURN count(*) AS propagated
        """
        # Run propagation iteratively until stable
        for _ in range(10):
            result = neo.run(propagate_cypher)
            propagated = result[0]["propagated"] if result else 0
            if propagated == 0:
                break

        # Count distinct communities
        count_cypher = """
        MATCH (n)
        WHERE n.community_id IS NOT NULL
        RETURN count(DISTINCT n.community_id) AS n_communities
        """
        result = neo.run(count_cypher)
        n_communities = result[0]["n_communities"] if result else 0

        return n_communities

    except Exception as exc:
        msg = f"Louvain fallback failed: {exc}"
        log.warning(msg)
        report.errors.append(msg)
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# Community Summarization
# ══════════════════════════════════════════════════════════════════════════════

def _generate_community_summaries(neo: Neo4jClient, report: CommunityReport) -> int:
    """
    For each community with >= _MIN_COMMUNITY_SIZE members, generate a
    summary using the LLM and store it as a Community node.
    """
    # Find communities with enough members
    cypher = """
    MATCH (n)
    WHERE n.community_id IS NOT NULL
    WITH n.community_id AS cid, collect(n) AS members
    WHERE size(members) >= $min_size
    RETURN cid,
           size(members) AS member_count,
           [m IN members[..$max_members] |
            {name: coalesce(m.name, m.id, ''),
             label: labels(m)[0],
             desc: coalesce(m.definition, m.description, m.raw_text, '')
            }] AS member_info
    ORDER BY member_count DESC
    LIMIT $max_communities
    """
    try:
        communities = neo.run(
            cypher,
            min_size=_MIN_COMMUNITY_SIZE,
            max_members=_MAX_MEMBERS_FOR_SUMMARY,
            max_communities=_MAX_COMMUNITIES_TO_SUMMARIZE,
        )
    except Exception as exc:
        report.errors.append(f"Community query failed: {exc}")
        return 0

    if not communities:
        return 0

    summaries_written = 0
    now = _now_iso()

    for comm in communities:
        cid = comm["cid"]
        member_count = comm["member_count"]
        member_info = comm["member_info"]

        # Build context for LLM
        member_lines = []
        for m in member_info:
            line = f"[{m.get('label', 'Node')}] {m.get('name', '?')}"
            desc = str(m.get("desc", ""))[:200].strip()
            if desc:
                line += f": {desc}"
            member_lines.append(line)

        user_prompt = (
            f"Community with {member_count} members:\n\n"
            + "\n".join(member_lines)
        )

        # Generate summary
        summary = call_agent_llm(
            "summarization",
            _SUMMARY_SYSTEM,
            user_prompt,
            max_tokens=512,
            temperature=0.2,
        )

        if not summary or len(summary) < 20:
            continue

        # Write Community node to Neo4j
        write_cypher = """
        MERGE (c:Community {community_id: $cid})
        SET c.summary      = $summary,
            c.member_count = $member_count,
            c.updated_at   = $now,
            c.agent_version = $version
        """
        try:
            neo.run(
                write_cypher,
                cid=str(cid),
                summary=summary.strip(),
                member_count=member_count,
                now=now,
                version=settings.PIPELINE_VERSION,
            )
            summaries_written += 1
        except Exception as exc:
            log.debug("Community write failed for %s: %s", cid, exc)

    # Create relationship from community members to their Community node
    link_cypher = """
    MATCH (n)
    WHERE n.community_id IS NOT NULL
    WITH n, toString(n.community_id) AS cid_str
    MATCH (c:Community {community_id: cid_str})
    MERGE (n)-[:MEMBER_OF]->(c)
    """
    try:
        neo.run(link_cypher)
    except Exception as exc:
        log.debug("Community linking failed: %s", exc)

    return summaries_written


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    argparse.ArgumentParser(description="ASEI Community Detection Agent").parse_args()
    print(json.dumps(run().to_dict(), indent=2))
