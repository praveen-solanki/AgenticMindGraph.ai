# ASEI Configuration (`config/`)

This directory contains the central configuration for the ASEI system. The primary file, `settings.py`, serves as the single source of truth for all tunable parameters across the ingestion pipeline and the agentic layer.

## Settings Overview (`settings.py`)

The configuration is organized into logical sections:

### 1. Global & Versioning
*   **`PIPELINE_VERSION`**: Tracks the schema and logic version. Changing this will trigger the Evolution Agent to flag nodes as "stale" or "drifted".

### 2. Infrastructure (Neo4j & vLLM)
*   **Database**: Connection URI, username, and password for Neo4j.
*   **Inference**: Base URL and API keys for the vLLM server or OpenAI-compatible endpoints.

### 3. Ingestion Pipeline Thresholds
*   **Stage 1 & 2**: PDF margins, noise removal ratios (TOC, revision, blank page thresholds).
*   **Stage 3**: Regex patterns for identifying AUTOSAR requirements (SWS, SRS, PRS, etc.).
*   **Stage 4**: Chunking limits (max tokens, overlap) and header-based splitting.
*   **Stage 6**: Entity resolution thresholds (certainty vs. uncertainty zones).

### 4. Graph Schema
*   **`ALLOWED_NODES`**: List of valid labels in the graph (e.g., `Requirement`, `Module`, `Function`).
*   **`ALLOWED_RELATIONSHIPS`**: List of valid relationship types (e.g., `REFERENCES`, `IMPLEMENTS`).
*   **`CANONICAL_NAME_OVERRIDES`**: Dictionary for normalizing entity names and abbreviations.

### 5. Agent Layer Settings
Each agent has specific parameters that control its behavior:
*   **Evolution Agent**: Staleness days and confidence thresholds.
*   **Conflict Agent**: Structural and semantic comparison limits.
*   **Synthesis Agent**: Hypothesis candidate limits and minimum confidence.
*   **Reasoning Agent**: Top-K retrieval, max hops for graph traversal, and debate weights (Prosecutor/Defender/Skeptic).
*   **Impact Agent**: Max hops for ripple-effect analysis.

### 6. Multi-LLM Provider Configuration
*   **API Keys**: Environment variable mappings for Groq, Sambanova, NVIDIA, etc.
*   **Model Assignments**: Which model to use for which role (e.g., `GROQ_MODEL_HEAVY` for reasoning, `OPENROUTER_MODEL_TINY` for classification).
*   **Rate Limits**: RPM (Requests Per Minute) and TPM (Tokens Per Minute) caps per provider.

---

## How to Modify Settings

It is recommended to use environment variables for sensitive information (API keys, passwords) and modify `settings.py` for logical parameters.

### Using a `.env` file
Create a `.env` file in the project root. The `settings.py` file uses `os.environ.get()` to load these values:

```env
NEO4J_PASSWORD=my_secure_password
GROQ_API_KEY=gsk_...
LLM_MAX_TOKENS=8192
```

### Direct Modification
For non-sensitive defaults (e.g., `CHUNK_MAX_TOKENS`), you can edit `settings.py` directly.

---

## Best Practices

*   **Bump Versioning:** If you change the `ALLOWED_NODES` or extraction logic, bump the `PIPELINE_VERSION`.
*   **Tune Thresholds:** Start with defaults. If you see too many duplicate entities, lower the `ENTITY_RESOLUTION_THRESHOLD`. If reasoning is too shallow, increase `ASEI_REASONING_MAX_HOPS`.
*   **Provider Diversity:** Configure at least two LLM providers to ensure system availability during outages or rate-limit hits.

---
*ASEI Configuration - Tailoring Intelligence to your Corpus.*
