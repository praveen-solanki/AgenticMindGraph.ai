# ASEI Utilities (`utils/`)

This directory contains the foundational infrastructure and helper classes used by both the Ingestion Pipeline and the Agentic Layer. These utilities ensure consistent logging, reliable LLM interactions, and robust database management.

## Core Utilities

### 1. Multi-LLM Client (`multi_llm_client.py`)
*   **Purpose:** The primary LLM interface for the ASEI agent layer. Routes inference to NVIDIA NIM models with automatic fallback to local vLLM.
*   **Features:**
    *   **Provider Chains:** Each task type (heavy_reasoning, synthesis, mid_reasoning, fast_classify, summarization, gap_detection, impact, local_reasoning) has an ordered chain of models to try.
    *   **Rate Limiting:** Per-model token-bucket rate limiter respects NVIDIA free-tier RPM limits.
    *   **Cooldown System:** Providers returning 429 are automatically cooled down per-model (not per-host), so one model's rate limit doesn't block others.
    *   **Retries & Fallbacks:** Exponential backoff on transient errors; automatic fallback to next provider in chain.
*   **Usage:**
    ```python
    from utils.multi_llm_client import call_agent_llm, call_agent_llm_json

    text   = call_agent_llm("heavy_reasoning", system_prompt, user_prompt)
    result = call_agent_llm_json("synthesis", system_prompt, user_prompt)
    ```

### 2. Neo4j Client (`neo4j_client.py`)
*   **Purpose:** A wrapper around the official Neo4j Python driver for simplified graph operations.
*   **Features:**
    *   **Connection Management:** Handles driver instantiation, verification, and teardown via context manager.
    *   **Batching:** Splits large writes into `NEO4J_BATCH_SIZE` chunks with automatic retry (up to 3 attempts) on transient errors (`ServiceUnavailable`, `SessionExpired`, `TransientError`).
    *   **Schema Management:** Creates uniqueness constraints, provenance indexes, and vector indexes.
    *   **Hard Reset:** Supports full database wipe for `--fresh` pipeline runs.
*   **Usage:**
    ```python
    from utils.neo4j_client import Neo4jClient
    with Neo4jClient() as neo:
        neo.run("MATCH (n:Requirement) RETURN count(n) AS n")
    ```

### 3. Checkpoint Manager (`checkpoint.py`)
*   **Purpose:** Provides persistence for the multi-stage ingestion pipeline.
*   **Features:**
    *   **JSON Serialization:** Saves intermediate stage data to disk.
    *   **Status Tracking:** Creates `.done` files to mark successful stage completion.
    *   **Invalidation:** Supports clearing specific checkpoints to allow re-running from a specific point.
*   **Usage:**
    ```python
    from utils.checkpoint import CheckpointManager
    ckpt = CheckpointManager(output_dir)
    if not ckpt.is_done(stage_num, "name"):
        # run stage...
        ckpt.save(stage_num, "name", data)
    ```

### 4. LLM Client (`llm_client.py`)
*   **Purpose:** The async/sync LLM client used by pipeline stages (Stage 1–5). Connects to local vLLM via LangChain's `ChatOpenAI`.
*   **Features:**
    *   **Async Support:** `acall_llm_json()` and `acall_llm_text()` for concurrent pipeline processing with semaphore-bounded concurrency.
    *   **Retry with Backoff:** Both sync and async functions retry with exponential backoff (`1.5^attempt` seconds) to handle transient vLLM failures gracefully.
    *   **JSON Parsing:** Robust extraction of JSON from LLM output (strips markdown fences, handles partial responses).
    *   **Singleton Pattern:** Single `ChatOpenAI` instance reused across all calls.

### 5. Logger (`logger.py`)
*   **Purpose:** Centralized logging configuration.
*   **Features:**
    *   **Consistent Formatting:** Ensures all logs across agents and pipeline stages follow the same `HH:MM:SS LEVEL [module] message` format.
    *   **Debug Mode:** `set_debug(True)` propagates DEBUG level to all named loggers (not just root), ensuring visibility across the entire system.
    *   **Non-propagating:** Each logger has `propagate=False` to prevent duplicate log lines.
*   **Usage:**
    ```python
    from utils.logger import get_logger, set_debug
    log = get_logger("my_module")
    set_debug(True)  # enables DEBUG for ALL loggers
    log.info("Starting task...")
    ```

---

## Best Practices for Developers

*   **Use the correct client:** Pipeline stages (1–5) use `llm_client.py` (async, local vLLM). Agents use `multi_llm_client.py` (sync, NVIDIA NIM + fallbacks). Never mix them.
*   **Close Connections:** Always use `Neo4jClient` as a context manager or explicitly call `.close()` to prevent connection leaks.
*   **Log Everything:** Use the centralized logger to ensure that events are captured for troubleshooting.
*   **Schema Governance:** When extracting entities, always validate labels against `settings.ALLOWED_NODES`. Unknown labels should map to `"Concept"` (enforced in Stage 5).

---
*ASEI Utilities - Reliability and Scalability.*
