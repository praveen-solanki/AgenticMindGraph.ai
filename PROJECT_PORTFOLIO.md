# AgenticMindGraph.ai — Complete Project Portfolio

## Project Title
**AgenticMindGraph.ai** — Autonomous Self-Evolving Research Intelligence (ASEI) Platform

## One-Line Summary
A production-grade autonomous AI platform that converts 1000+ page AUTOSAR specification PDFs into a self-evolving Neo4j Knowledge Graph using multi-agent LLM orchestration, GraphRAG, and adversarial verification pipelines.

---

## Table of Contents
1. [Technical Overview](#technical-overview)
2. [Architecture](#architecture)
3. [Technology Stack](#technology-stack)
4. [Key Engineering Achievements](#key-engineering-achievements)
5. [Pipeline Architecture (8-Stage ETL)](#pipeline-architecture)
6. [Agent Layer (12 Autonomous Agents)](#agent-layer)
7. [GraphRAG Implementation](#graphrag-implementation)
8. [Multi-Agent Debate Reasoning](#multi-agent-debate-reasoning)
9. [Entity Resolution System](#entity-resolution-system)
10. [Production Hardening](#production-hardening)
11. [Scalability Design](#scalability-design)
12. [Metrics & Performance](#metrics--performance)
13. [Research Innovations](#research-innovations)
14. [Skills Demonstrated](#skills-demonstrated)

---

## Technical Overview

### Problem Statement
AUTOSAR (AUTomotive Open System ARchitecture) specifications span 50+ PDF documents totaling 10,000+ pages of dense technical requirements, API definitions, protocol specifications, and traceability matrices. Engineers spend weeks manually cross-referencing requirements, tracing dependencies, and identifying specification gaps.

### Solution
An end-to-end autonomous intelligence platform that:
- **Ingests** AUTOSAR PDFs through an 8-stage pipeline (extraction → cleaning → harvesting → chunking → entity extraction → resolution → embedding → graph storage)
- **Reasons** over the Knowledge Graph using multi-hop GraphRAG with a 3-model adversarial debate architecture
- **Self-evolves** through 12 autonomous agents that detect staleness, resolve conflicts, synthesize hypotheses, verify claims, and maintain graph integrity
- **Scales** with Neo4j native vector indexes, incremental ingestion, and community detection for global queries

### Impact
- Reduces specification analysis time from **weeks to minutes**
- Detects cross-module contradictions that human reviewers miss
- Traces impact of requirement changes across 50+ interconnected modules
- Provides explainable, evidence-backed answers with confidence scores

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ASEI Platform Architecture                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────┐    ┌──────────────┐    ┌─────────────────────┐     │
│  │  PDF Corpus  │───▶│  8-Stage ETL │───▶│  Neo4j Knowledge    │     │
│  │  (AUTOSAR)   │    │   Pipeline   │    │  Graph (50K+ nodes) │     │
│  └─────────────┘    └──────────────┘    └─────────┬───────────┘     │
│                                                     │                 │
│                           ┌─────────────────────────┼──────┐         │
│                           │     Agent Layer          │      │         │
│                           │                          ▼      │         │
│                           │  ┌────────────────────────────┐ │         │
│                           │  │    Supervisor Agent         │ │         │
│                           │  │  (Dynamic Orchestration)   │ │         │
│                           │  └────────────┬───────────────┘ │         │
│                           │               │                  │         │
│                           │  ┌────────────┼───────────────┐ │         │
│                           │  │ Evolution │ Conflict │ Synth│ │         │
│                           │  │ Verify   │ Summary  │ Gap  │ │         │
│                           │  │ Impact   │ Community│ Watch│ │         │
│                           │  │ Reasoning│ Query Mem│ HITL │ │         │
│                           │  └───────────────────────────-┘ │         │
│                           └─────────────────────────────────┘         │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  LLM Infrastructure                                          │     │
│  │  • NVIDIA NIM (14 models) + Local vLLM (Qwen2.5-72B-AWQ)   │     │
│  │  • Per-model rate limiting + cooldown + fallback chains      │     │
│  │  • Multi-provider: Groq, Sambanova, OpenRouter, NVIDIA       │     │
│  └─────────────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technologies |
|-------|-------------|
| **Language** | Python 3.11+ (type-annotated, async/await) |
| **Graph Database** | Neo4j 5.x (Cypher, Vector Indexes, GDS) |
| **LLM Inference** | vLLM (Qwen2.5-72B-AWQ), NVIDIA NIM (14 models) |
| **Embeddings** | BAAI/bge-m3 (1024-dim, multilingual) |
| **LLM Framework** | LangChain, LangChain-OpenAI, LLMGraphTransformer |
| **PDF Processing** | PyMuPDF4LLM (structure-preserving extraction) |
| **Vector Search** | Neo4j native vector indexes (cosine similarity) |
| **Orchestration** | Custom state machine + Supervisor pattern |
| **Networking** | httpx (async HTTP), threading (rate limiters) |
| **Serialization** | JSON checkpoints with atomic writes + fsync |

---

## Key Engineering Achievements

### 1. Multi-Agent Debate Architecture (Research-Grade Innovation)
- 3-model weighted debate system for knowledge graph reasoning
- Heavy reasoning (qwen3.5-397b) vs Mid reasoning (qwen3.5-122b) vs Local skeptic (Qwen2.5-72B)
- Weighted confidence voting: 0.45 / 0.35 / 0.20
- Produces higher-quality answers than single-model approaches

### 2. Ontology-Governed Entity Extraction
- Strict structural label protection (Document, Chunk, Corpus → never LLM-writable)
- Schema boundary guard: hallucinated labels → "Concept" coercion
- Dual-track extraction: Rule-based (deterministic) + LLM-based (semantic)
- Post-extraction adversarial validation of LLM relationships

### 3. Graph-Native Vector Entity Resolution
- Neo4j vector index for O(N log N) nearest-neighbor entity matching
- Replaces naive O(N²) dense matrix approach
- Antonym-aware clustering (encryption ≠ decryption)
- 3-tier resolution: Manual overrides → Vector clustering → LLM uncertain-zone

### 4. Incremental Knowledge Graph Construction
- `--add-pdf` flag processes only new documents
- Queries existing graph to skip already-ingested files
- MERGE-based writes ensure idempotent integration
- No checkpoint invalidation for existing pipeline data

### 5. Community Detection & Global Summarization
- Neo4j GDS Leiden algorithm for community detection
- LLM-generated hierarchical community summaries
- Enables Microsoft GraphRAG-style "global queries"
- MEMBER_OF edges link entities to community nodes

### 6. Circuit Breaker & Fault Tolerance
- Infrastructure failure detection (Neo4j down, vLLM crash)
- Automatic cycle abort prevents cascading agent failures
- Per-model cooldown isolation (one model's 429 ≠ all models blocked)
- Exponential backoff on all retry paths (sync and async)

### 7. Human-in-the-Loop Safety Gate
- High-impact hypotheses (CONTRADICTS, uncertain zone, high blast radius) flagged for review
- ReviewItem nodes stored in Neo4j with full provenance
- CLI interface: list/approve/reject/batch-approve
- Integrates seamlessly into Verification Agent without code changes

### 8. Dynamic Supervisor Orchestration
- Graph state probes determine which agents have work
- Skips idle agents (typically invokes 3-4 of 12 per cycle)
- 40-70% reduction in cycle time and LLM API costs
- Condition-based triggering: stale nodes → Evolution, unverified hypotheses → Verification

---

## Pipeline Architecture

### 8-Stage ETL Pipeline with Crash Recovery

| Stage | Name | Technology | Output |
|-------|------|-----------|--------|
| 0 | Corpus Analysis | LLM (document classification) | Document type map, schema recommendations |
| 1 | PDF Extraction | PyMuPDF4LLM + async LLM classification | Per-page Markdown + page type labels |
| 2 | Noise Removal | LLM cleaning + BGE-M3 boilerplate dedup | Clean pages (headers/footers/TOC removed) |
| 3 | Requirement Harvesting | Regex + LLM cross-reference validation | ID inventory + validated REFERENCES edges |
| 4 | Chunking | MarkdownHeaderTextSplitter + LLM enrichment | Typed chunks with summaries + embeddings |
| 5 | Entity Extraction | Rule-based (Track A) + LLMGraphTransformer (Track B) | Nodes + relationships with provenance |
| 6 | Entity Resolution | Neo4j vector index + LLM uncertain-zone | Deduplicated canonical nodes |
| 7 | Embedding | sentence-transformers BGE-M3 | 1024-dim vectors (primary + summary) |
| 8 | Graph Storage | Neo4j MERGE (idempotent) | Full KG with vector indexes + kNN edges |

**Key Design Decisions:**
- Atomic JSON checkpoints after every stage (crash → resume from last completed)
- `--fresh` flag wipes all checkpoints + Neo4j for clean re-ingestion
- `--from-stage N` allows selective re-processing
- Dynamic schema expansion from Stage 0 corpus analysis

---

## Agent Layer

### 12 Autonomous Agents

| Agent | Purpose | LLM Calls | Graph Writes |
|-------|---------|-----------|-------------|
| **Evolution** | Detect stale/drifted nodes | 0 (pure Cypher) | `stale=true` flags |
| **Conflict** | Find contradictions (structural + semantic) | Yes (semantic pairs) | CONTRADICTS edges |
| **Synthesis** | Propose hypothesis relationships | Yes (bridge mining) | HYPOTHESIZES edges |
| **Verification** | Adversarial hypothesis testing | Yes (disproof) | verified=true/false |
| **Summarization** | Incremental module summaries | Yes (update summaries) | Module.summary |
| **Gap Detection** | Find missing requirements | Yes (cross-module) | SPEC_GAP edges |
| **Impact** | Trace downstream change effects | Yes (severity) | IMPACT_OF edges |
| **Reasoning** | Answer questions (multi-hop GraphRAG) | Yes (3-model debate) | None (read-only) |
| **Watchdog** | Monitor agent health metrics | 0 (pure Cypher) | AgentMetrics nodes |
| **Query Memory** | Store/analyze query patterns | Yes (classify) | QueryPattern nodes |
| **Community** | Detect graph communities | 0 (GDS/Cypher) | Community nodes + MEMBER_OF |
| **Supervisor** | Dynamic agent invocation | 0 (graph probes) | None (orchestration) |

### Orchestration Modes
1. **Full Cycle** (`asei_runner.py cycle`) — runs all agents sequentially
2. **Supervised** (`asei_runner.py supervise`) — dynamic invocation based on graph state
3. **Continuous Service** (`asei_runner.py serve`) — cycles every N hours
4. **Individual** (`asei_runner.py <agent>`) — run any single agent

---

## GraphRAG Implementation

### Multi-Hop Graph Expansion
```
User Question → BGE-M3 Embedding → Neo4j Vector Index → Top-K Seed Chunks
    → Multi-hop Cypher Traversal (up to 5 hops)
    → Collect entities, requirements, concepts along paths
    → Token-capped context assembly (8000 tokens × 4 chars)
    → 3-Model Adversarial Debate
    → Weighted confidence answer with evidence paths
```

### Key Differentiators from Standard RAG:
- **Graph traversal** expands context beyond vector-similar chunks
- **Multi-hop reasoning** follows DEPENDS_ON, REFERENCES, IMPLEMENTS paths
- **Explainability** — every answer includes the exact graph traversal path
- **Community summaries** enable global queries spanning entire corpus
- **Impact-aware** — injects IMPACT_OF context from Impact Agent

---

## Multi-Agent Debate Reasoning

### Architecture
```
                    ┌─────────────────────┐
                    │   User Question     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                 │
    ┌─────────▼──────┐ ┌──────▼───────┐ ┌──────▼──────┐
    │ Heavy Reasoning │ │Mid Reasoning │ │   Local     │
    │ (qwen3.5-397b) │ │(qwen3.5-122b)│ │ (Qwen72B)  │
    │  weight: 0.45   │ │ weight: 0.35 │ │weight: 0.20│
    └─────────┬──────┘ └──────┬───────┘ └──────┬──────┘
              │                │                 │
              └────────────────┼────────────────┘
                               │
                    ┌──────────▼──────────┐
                    │  Weighted Vote      │
                    │  Primary: max(weight)│
                    │  Conf: Σ(w·c)/Σ(w)  │
                    └─────────────────────┘
```

### Why This Works Better Than Single-Model:
- **Diversity**: Different model architectures catch different failure modes
- **Adversarial**: The skeptic challenges both prosecutor and defender
- **Calibrated**: Weighted voting produces better-calibrated confidence scores
- **Fallback**: If one provider is down, remaining legs still produce answers

---

## Entity Resolution System

### 3-Tier Approach

**Tier 1 — Manual Overrides** (500+ canonical name mappings)
- `"communication manager"` → `"ComM"`
- `"iso26262"` → `"ISO 26262"`
- Covers all 80+ AUTOSAR modules and common variants

**Tier 2 — Graph-Native Vector Clustering** (O(N log N))
1. Embed entity names with BGE-M3
2. Upload to Neo4j as temporary EntityCandidate nodes
3. Create vector index on name_embedding
4. Query k-nearest neighbors per entity
5. Apply same-label guard + antonym guard
6. Union-Find clustering on high-similarity pairs
7. LLM picks canonical name per cluster
8. Cleanup temporary nodes

**Tier 3 — LLM Uncertain-Zone Resolution** (0.75 ≤ sim < 0.92)
- Pairs in the "uncertain zone" sent to LLM for merge/no-merge decision
- Prefix stripping validation (Concept_Job → Job)
- Batch processing (10 pairs per LLM call)

---

## Production Hardening

### Reliability Features
| Feature | Implementation |
|---------|--------------|
| Circuit Breaker | Aborts cycle if 2+ infra failures in last 3 errors |
| Retry with Backoff | All async LLM calls: `await asyncio.sleep(1.5^attempt)` |
| Neo4j Transient Retry | `run_batch()` retries 3x on ServiceUnavailable/SessionExpired |
| Checkpoint/Resume | Atomic JSON saves with fsync; crash → resume from last stage |
| Schema Boundary Guard | Unknown LLM labels → "Concept" (prevents graph pollution) |
| Per-Model Cooldown | 429 on model A doesn't block model B on same host |
| Graceful Degradation | If vector index unavailable → fallback to in-memory clustering |

### Human-in-the-Loop Safety
| Trigger | Action |
|---------|--------|
| hypothesis_type == "CONTRADICTS" | → Human review (safety-critical) |
| Confidence in [0.40, 0.60] | → Human review (model uncertain) |
| Blast radius ≥ 5 downstream nodes | → Human review (high impact) |

---

## Scalability Design

| Dimension | Current | Designed For | Approach |
|-----------|---------|--------------|----------|
| PDF Count | 50 | 500+ | Incremental ingestion (--add-pdf) |
| Entity Count | 15,000 | 100,000+ | Neo4j vector index (O(N log N)) |
| Chunk Count | 10,000 | 200,000+ | Batched writes (500/transaction) |
| Concurrent LLM | 16 | 64 | Semaphore-bounded async |
| Cycle Time | 15 min | 5 min | Supervisor (skip idle agents) |
| Global Queries | Module-level | Corpus-wide | Community detection + summaries |

---

## Metrics & Performance

### Pipeline Performance (50 PDFs, ~5000 pages)
- Stage 1 (Extraction): ~2 minutes
- Stage 2 (Cleaning): ~8 minutes (LLM per page)
- Stage 3 (Harvesting): ~5 minutes (LLM validation)
- Stage 4 (Chunking): ~10 minutes (LLM enrichment)
- Stage 5 (Entities): ~20 minutes (LLMGraphTransformer)
- Stage 6 (Resolution): ~3 minutes (vector clustering)
- Stage 7 (Embedding): ~2 minutes (GPU) / ~15 minutes (CPU)
- Stage 8 (Storage): ~1 minute (MERGE writes)
- **Total: ~50 minutes** (GPU) / ~65 minutes (CPU-only)

### Knowledge Graph Stats (typical corpus)
- Nodes: 15,000–50,000
- Relationships: 30,000–100,000
- Node Labels: 25+ (Requirement, Module, Concept, Function, etc.)
- Relationship Types: 20+ (REFERENCES, DEPENDS_ON, IMPLEMENTS, etc.)
- Vector Indexes: 2 (chunk_embedding, summary_embedding)

---

## Research Innovations

### Ahead of Industry Standard:
1. **Multi-agent debate for KG reasoning** — Most production systems use single-model RAG
2. **Autonomous knowledge graph evolution** — Very few systems implement automated staleness detection
3. **Ontology-governed extraction with coercion** — Prevents LLM hallucination from polluting graph schema
4. **Per-model cooldown isolation** — Novel rate-limiting granularity for multi-model free-tier usage
5. **Adversarial hypothesis verification** — LLM argues AGAINST hypotheses before committing them

### Industry-Standard Implementations:
- Microsoft GraphRAG-style community detection + global summarization
- Neo4j native vector search for entity resolution (replacing O(N²) approaches)
- Incremental graph construction (process only new documents)
- Supervisor pattern orchestration (dynamic agent invocation)
- Human-in-the-loop safety gates (blast radius + uncertainty triggers)

---

## Skills Demonstrated

### AI/ML Engineering
- Large Language Model orchestration (multi-provider, multi-model)
- Retrieval-Augmented Generation (RAG) with graph traversal
- Embedding pipelines (sentence-transformers, cosine similarity)
- Prompt engineering (system prompts for extraction, validation, reasoning)
- LLM hallucination prevention (ontology guards, adversarial verification)

### Graph Database Engineering
- Neo4j Cypher query optimization (MERGE, vector indexes, GDS)
- Knowledge graph schema design (ontology governance, coercion maps)
- Entity resolution at scale (vector clustering, union-find)
- Graph Data Science (Leiden community detection, modularity)
- Multi-hop graph traversal for reasoning

### Systems Engineering
- Async/concurrent Python (asyncio, threading, semaphores)
- Fault-tolerant distributed systems (circuit breakers, retry logic)
- Rate limiting (token bucket, per-model cooldown)
- Atomic checkpoint/resume (fsync, crash recovery)
- Multi-provider LLM failover chains

### Software Architecture
- Multi-agent autonomous systems (12 agents, supervisor pattern)
- Pipeline architecture (8-stage ETL with crash recovery)
- Event-driven orchestration (graph state probes → dynamic invocation)
- Human-in-the-loop integration (ReviewItem nodes, CLI interface)
- Incremental data processing (skip existing, merge new)

### Domain Expertise
- AUTOSAR Classic Platform & Adaptive Platform architecture
- Requirements engineering (traceability, allocation, derivation)
- Specification analysis (cross-module dependency detection)
- Automotive software standards (ISO 26262, MISRA, AUTOSAR BSW)

---

## Repository Statistics

| Metric | Value |
|--------|-------|
| Total Python LOC | ~14,000 |
| Number of Agents | 12 |
| Pipeline Stages | 8 |
| Neo4j Cypher Queries | 100+ |
| LLM System Prompts | 15+ |
| Configuration Parameters | 80+ |
| CLI Subcommands | 16 |
| Supported LLM Providers | 6 (NVIDIA, Groq, Sambanova, Cerebras, OpenRouter, local vLLM) |

---

## How to Run

```bash
# Full pipeline (initial corpus ingestion)
python -m pipeline.main --pdf-dir ./pdfs --output-dir ./output

# Incremental (add new PDFs without reprocessing)
python -m pipeline.main --add-pdf ./new_doc.pdf --output-dir ./output

# Ask a question
python asei_runner.py ask "What are the NvM requirements for flash sector handling?"

# Run supervised agent cycle
python asei_runner.py supervise --question "What changed in R22-11?"

# Review pending hypotheses
python asei_runner.py review --list
python asei_runner.py review --approve review_123 --reason "Valid relationship confirmed"

# Run community detection
python asei_runner.py community
```

---

## Contact & Links

- **Repository:** https://github.com/praveen-solanki/AgenticMindGraph.ai
- **Architecture:** Multi-agent GraphRAG + Autonomous KG Evolution
- **Domain:** AUTOSAR Automotive Software Specifications
- **Status:** Production-ready research system (v1.1.0)

---

*Built with passion for autonomous intelligence systems and knowledge graph engineering.*
