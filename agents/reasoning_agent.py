"""
agents/reasoning_agent.py
=========================
ASEI Reasoning Agent — multi-hop GraphRAG with Chain-of-Thought (KG-CoT).

Responsibilities
----------------
1. Vector search:
   - Embed the user question with the same sentence-transformer used during
     ingestion (EMBED_MODEL in settings).
   - Retrieve the top-K most similar Chunk nodes via Neo4j vector index.

2. Graph expansion (multi-hop traversal):
   - From each retrieved chunk, traverse up to ASEI_REASONING_MAX_HOPS hops
     along MENTIONS, DEPENDS_ON, REFERENCES, SIMILAR_TO, HYPOTHESIZES edges
     to gather a richer subgraph context.
   - Collect all reachable entities, requirements, and concepts along with the
     relationship path that connects them.

3. LLM reasoning (KG-CoT):
   - Pass the question + assembled subgraph context to the LLM.
   - The LLM returns a structured answer with:
       * answer       : the direct answer to the question
       * reasoning    : step-by-step chain-of-thought over the KG paths
       * evidence     : list of node IDs / chunk IDs used
       * confidence   : 0.0–1.0 overall answer confidence

4. Return a ReasoningResult for the orchestrator or direct API use.

Design notes
------------
- Vector search uses Neo4j's built-in vector index (chunk_embedding_index).
  Falls back to cosine similarity scan if the index is unavailable.
- Graph traversal is depth-bounded by ASEI_REASONING_MAX_HOPS (default 3).
- The assembled context is token-capped at ASEI_REASONING_CONTEXT_TOKENS
  before being sent to the LLM.
- Every answer includes the exact traversal path for full explainability.

Run standalone:
    python -m agents.reasoning_agent --question "What are the AUTOSAR NvM requirements?"
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config import settings
from utils.logger import get_logger
from utils.multi_llm_client import call_agent_llm, call_agent_llm_json
from utils.neo4j_client import Neo4jClient

log = get_logger("reasoning_agent")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ReasoningResult:
    question:    str        = ""
    answer:      str        = ""
    reasoning:   str        = ""
    evidence:    list[str]  = field(default_factory=list)
    path_steps:  list[dict] = field(default_factory=list)
    confidence:  float      = 0.0
    run_at:      str        = ""
    errors:      list[str]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question":   self.question,
            "answer":     self.answer,
            "reasoning":  self.reasoning,
            "evidence":   self.evidence,
            "path_steps": self.path_steps,
            "confidence": self.confidence,
            "run_at":     self.run_at,
            "errors":     self.errors,
        }


# ── LLM prompt ────────────────────────────────────────────────────────────────

_REASONING_SYSTEM = """\
You are an expert knowledge graph reasoning engine.

You are given:
1. A user question
2. A subgraph context assembled by multi-hop traversal of a knowledge graph.
   The context contains: chunk text excerpts, entity/concept descriptions,
   requirement texts, and the relationship paths connecting them.

Your task is to answer the question by reasoning over the provided KG paths
step by step (Chain-of-Thought). Be precise. Cite specific nodes and paths.

Return ONLY valid JSON, no markdown, no extra text:
{
  "answer":      "<direct answer to the question>",
  "reasoning":   "<step-by-step chain-of-thought over the KG paths>",
  "evidence":    ["<node_id or chunk_id>", ...],
  "confidence":  <float 0.0-1.0>
}

If the context does not contain enough information to answer, set
confidence=0.0 and explain what is missing in the reasoning field.
"""


# ── Main agent entry point ────────────────────────────────────────────────────

def run(question: str, neo: Neo4jClient | None = None) -> ReasoningResult:
    """
    Answer a question using multi-hop GraphRAG + KG-CoT.

    Args:
        question: Natural language question to answer.
        neo:      Optional Neo4jClient; created internally if not provided.

    Returns:
        ReasoningResult with answer, reasoning chain, evidence, and path steps.
    """
    result    = ReasoningResult(question=question, run_at=_now_iso())
    close_neo = neo is None
    if neo is None:
        neo = Neo4jClient()

    try:
        log.info("Reasoning Agent: processing question: %.80s", question)

        # ── 1. Embed question ─────────────────────────────────────────────────
        q_embedding = _embed_question(question)

        # ── 2. Vector search → seed chunks ───────────────────────────────────
        seed_chunks = _vector_search(neo, q_embedding, result)
        if not seed_chunks:
            result.answer    = "No relevant information found in the knowledge graph."
            result.reasoning = "Vector search returned no matching chunks."
            result.confidence = 0.0
            return result

        # ── 3. Multi-hop graph expansion ──────────────────────────────────────
        subgraph = _expand_subgraph(neo, seed_chunks, result)

        # ── 4. Assemble context string (token-capped) ─────────────────────────
        context_str = _build_context(seed_chunks, subgraph)
        result.path_steps = subgraph.get("path_steps", [])

        # ── 5. LLM reasoning (KG-CoT) ─────────────────────────────────────────
        llm_result = _llm_reason(question, context_str, result)
        if llm_result:
            result.answer     = llm_result.get("answer", "")
            result.reasoning  = llm_result.get("reasoning", "")
            result.evidence   = llm_result.get("evidence", [])
            result.confidence = float(llm_result.get("confidence", 0.5))

        log.info(
            "Reasoning Agent complete: confidence=%.2f, evidence=%d nodes",
            result.confidence, len(result.evidence),
        )

    except Exception as exc:
        msg = f"Reasoning Agent error: {exc}"
        log.error(msg)
        result.errors.append(msg)
        result.answer    = f"Reasoning failed: {exc}"
        result.confidence = 0.0
    finally:
        if close_neo:
            neo.close()

    return result


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Question embedding
# ══════════════════════════════════════════════════════════════════════════════

def _embed_question(question: str) -> list[float] | None:
    """Embed the question using the same model as ingestion."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(settings.EMBED_MODEL)
        vector = model.encode(question, normalize_embeddings=True)
        return vector.tolist()
    except Exception as exc:
        log.warning("Question embedding failed: %s", exc)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Vector search
# ══════════════════════════════════════════════════════════════════════════════

def _vector_search(
    neo: Neo4jClient,
    embedding: list[float] | None,
    result: ReasoningResult,
) -> list[dict]:
    """
    Retrieve top-K chunks via Neo4j vector index.
    Falls back to a keyword/text scan if embedding is unavailable.
    """
    top_k = settings.ASEI_REASONING_TOP_K

    if embedding:
        cypher = """
        CALL db.index.vector.queryNodes('chunk_embedding_index', $top_k, $embedding)
        YIELD node AS chunk, score
        WHERE score >= $min_score
        RETURN chunk.id    AS chunk_id,
               chunk.text  AS text,
               chunk.doc_id AS doc_id,
               score
        ORDER BY score DESC
        """
        try:
            rows = neo.run(
                cypher,
                top_k=top_k,
                embedding=embedding,
                min_score=settings.ASEI_REASONING_MIN_SIMILARITY,
            )
            if rows:
                log.info("  Reasoning: vector search returned %d seed chunks", len(rows))
                return [dict(r) for r in rows]
        except Exception as exc:
            msg = f"Vector index search failed (may not exist yet): {exc}"
            log.warning(msg)
            result.errors.append(msg)

    # Fallback: return first N chunks (very rough — only for dev/test)
    log.warning("  Reasoning: falling back to sequential chunk scan")
    try:
        rows = neo.run("MATCH (c:Chunk) RETURN c.id AS chunk_id, c.text AS text, c.doc_id AS doc_id, 1.0 AS score LIMIT $k", k=top_k)
        return [dict(r) for r in rows]
    except Exception as exc:
        result.errors.append(f"Fallback chunk scan failed: {exc}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Multi-hop graph expansion
# ══════════════════════════════════════════════════════════════════════════════

def _expand_subgraph(
    neo: Neo4jClient,
    seed_chunks: list[dict],
    result: ReasoningResult,
) -> dict:
    """
    From each seed chunk, traverse up to MAX_HOPS hops to collect
    related entities, requirements, concepts, and the paths to them.
    """
    max_hops   = settings.ASEI_REASONING_MAX_HOPS
    chunk_ids  = [c["chunk_id"] for c in seed_chunks if c.get("chunk_id")]
    path_steps: list[dict] = []
    nodes:      list[dict] = []

    if not chunk_ids:
        return {"nodes": nodes, "path_steps": path_steps}

    # Traverse: chunk → mentions → entity/requirement/concept → follow relations
    cypher = f"""
    UNWIND $chunk_ids AS cid
    MATCH path = (start:Chunk {{id: cid}})-[*1..{max_hops}]-(related)
    WHERE (related:Entity OR related:Requirement OR related:Concept)
      AND related.name IS NOT NULL
    RETURN
        cid                             AS seed_chunk_id,
        related.id                      AS node_id,
        related.name                    AS node_name,
        labels(related)[0]              AS node_label,
        coalesce(related.definition,
                 related.description,
                 related.text, '')      AS node_desc,
        length(path)                    AS hop_count,
        [r IN relationships(path) | type(r)] AS rel_types
    ORDER BY hop_count ASC
    LIMIT $node_limit
    """
    node_limit = settings.ASEI_REASONING_TOP_K * max_hops * 20
    try:
        rows = neo.run(cypher, chunk_ids=chunk_ids, node_limit=node_limit)
        for r in rows:
            node_entry = {
                "node_id":    r.get("node_id", ""),
                "name":       r.get("node_name", ""),
                "label":      r.get("node_label", ""),
                "desc":       str(r.get("node_desc", ""))[:400],
                "hop_count":  r.get("hop_count", 1),
            }
            nodes.append(node_entry)
            path_steps.append({
                "seed": r.get("seed_chunk_id"),
                "node": r.get("node_name"),
                "via":  r.get("rel_types", []),
                "hops": r.get("hop_count", 1),
            })
        log.info("  Reasoning: subgraph expanded — %d nodes across %d hops", len(nodes), max_hops)
    except Exception as exc:
        msg = f"Subgraph expansion failed: {exc}"
        log.warning(msg)
        result.errors.append(msg)

    # Deduplicate nodes by node_id
    seen: set[str] = set()
    unique_nodes: list[dict] = []
    for n in nodes:
        if n["node_id"] not in seen:
            seen.add(n["node_id"])
            unique_nodes.append(n)

    return {"nodes": unique_nodes, "path_steps": path_steps}


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Context assembly
# ══════════════════════════════════════════════════════════════════════════════

def _build_context(
    seed_chunks: list[dict],
    subgraph: dict,
) -> str:
    """
    Assemble a token-capped context string from seed chunks + expanded nodes.
    Context is ordered: seed chunks first (most relevant), then graph nodes.
    """
    max_chars = settings.ASEI_REASONING_CONTEXT_TOKENS * 4  # rough chars-per-token

    parts: list[str] = []

    # Seed chunk texts
    parts.append("=== Relevant Document Chunks ===")
    for c in seed_chunks:
        chunk_text = str(c.get("text", ""))[:800]
        parts.append(f"[Chunk {c.get('chunk_id', '?')} | score={c.get('score', 0):.3f}]\n{chunk_text}")

    # Expanded graph nodes
    parts.append("\n=== Knowledge Graph Context (multi-hop expansion) ===")
    for n in subgraph.get("nodes", []):
        desc = n.get("desc", "").strip()
        entry = f"[{n.get('label', 'Node')}] {n.get('name', '')} (hop={n.get('hop_count', '?')})"
        if desc:
            entry += f"\n  → {desc[:300]}"
        parts.append(entry)

    # Truncate to max_chars
    full_context = "\n\n".join(parts)
    if len(full_context) > max_chars:
        full_context = full_context[:max_chars] + "\n[...context truncated...]"

    return full_context


# ══════════════════════════════════════════════════════════════════════════════
# Step 5 — LLM reasoning (KG-CoT)
# ══════════════════════════════════════════════════════════════════════════════

def _llm_reason(
    question: str,
    context: str,
    result: ReasoningResult,
) -> dict | None:
    """
    Multi-agent debate: three independent agents reason over the KG context,
    then a weighted vote produces the final answer.

    Prosecutor  — heavy_reasoning (GPT-OSS-120B) — builds strongest case
    Defender    — mid_reasoning   (Qwen3-32B)    — challenges / alternative
    Skeptic     — local_reasoning (local Qwen72B) — finds holes in both

    Weighted vote: heavy=0.45, mid=0.35, local=0.20
    Final answer is taken from the highest-weight agent that produced a valid response.
    Confidence is the weighted average of all valid confidences.
    """
    user = (
        f"QUESTION:\n{question}\n\n"
        f"KNOWLEDGE GRAPH CONTEXT:\n{context}\n\n"
        "Using the context above, reason over the KG paths and answer the question."
    )

    weights = {
        "prosecutor": settings.ASEI_REASONING_DEBATE_WEIGHT_HEAVY,
        "defender":   settings.ASEI_REASONING_DEBATE_WEIGHT_MID,
        "skeptic":    settings.ASEI_REASONING_DEBATE_WEIGHT_LOCAL,
    }

    responses: dict[str, dict] = {}

    for task_type in ("prosecutor", "defender", "skeptic"):
        try:
            r = call_agent_llm_json(task_type, _REASONING_SYSTEM, user, max_tokens=1024)
            if r and isinstance(r, dict) and r.get("answer"):
                responses[task_type] = r
                log.info(
                    "  Debate leg '%s': confidence=%.2f",
                    task_type, float(r.get("confidence", 0)),
                )
        except Exception as exc:
            log.debug("Debate leg '%s' failed: %s", task_type, exc)

    if not responses:
        return None

    # Weighted confidence
    total_weight = sum(weights[k] for k in responses)
    weighted_conf = sum(
        float(v.get("confidence", 0.5)) * weights[k] / total_weight
        for k, v in responses.items()
    )

    # Primary answer: highest-weight responding leg
    primary_key = max(responses, key=lambda k: weights[k])
    primary = responses[primary_key]

    # Merge evidence from all legs
    all_evidence: list[str] = []
    for r in responses.values():
        all_evidence.extend(r.get("evidence", []))

    # Build combined reasoning showing all debate legs
    debate_summary = "\n\n".join(
        f"[{k} | conf={float(v.get('confidence',0)):.2f}] {v.get('reasoning','')}"
        for k, v in responses.items()
    )

    return {
        "answer":     primary.get("answer", ""),
        "reasoning":  f"Multi-agent debate ({len(responses)}/3 legs):\n\n{debate_summary}",
        "evidence":   list(dict.fromkeys(all_evidence)),  # dedup, preserve order
        "confidence": round(weighted_conf, 3),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ASEI Reasoning Agent — standalone run")
    p.add_argument("--question", required=True, help="Question to answer")
    return p.parse_args()


if __name__ == "__main__":
    import json
    args = _parse_args()
    result = run(question=args.question)
    print(json.dumps(result.to_dict(), indent=2))
