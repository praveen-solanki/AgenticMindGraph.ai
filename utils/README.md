# ASEI Utilities (`utils/`)

This directory contains the foundational infrastructure and helper classes used by both the Ingestion Pipeline and the Agentic Layer. These utilities ensure consistent logging, reliable LLM interactions, and robust database management.

## Core Utilities

### 1. Multi-LLM Client (`multi_llm_client.py`)
*   **Purpose:** A unified interface for interacting with multiple LLM providers.
*   **Features:**
    *   **Provider Support:** Groq, Sambanova, Cerebras, OpenRouter, NVIDIA NIM, and Bosch AI.
    *   **Rate Limiting:** Built-in RPM (Requests Per Minute) management to prevent API throttling.
    *   **Retries & Fallbacks:** Automatically retries failed requests and falls back to secondary models or providers if necessary.
    *   **Token Management:** Tracks token usage and applies per-model output caps.
*   **Usage:**
    ```python
    from utils.multi_llm_client import MultiLLMClient
    client = MultiLLMClient()
    response = client.chat(task="extraction", prompt="...", provider="groq")
    ```

### 2. Neo4j Client (`neo4j_client.py`)
*   **Purpose:** A wrapper around the official Neo4j Python driver for simplified graph operations.
*   **Features:**
    *   **Connection Management:** Handles driver instantiation and teardown.
    *   **Batching:** Optimized for high-volume writes during Stage 8 of the pipeline.
    *   **Cypher Helpers:** Methods for common tasks like finding similar chunks, checking node staleness, and tracing impact.
*   **Usage:**
    ```python
    from utils.neo4j_client import Neo4jClient
    with Neo4jClient() as neo:
        neo.run_query("MATCH (n:Requirement) RETURN count(n)")
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
*   **Purpose:** A simpler, base client often used for local vLLM instances or direct OpenAI-compatible endpoint calls.
*   **Features:** Minimal overhead, used primarily in the `eval.py` script and some early pipeline stages.

### 5. Logger (`logger.py`)
*   **Purpose:** Centralized logging configuration.
*   **Features:**
    *   **Consistent Formatting:** Ensures all logs across agents and pipeline stages follow the same format.
    *   **Debug Mode:** Global toggle for verbose logging.
*   **Usage:**
    ```python
    from utils.logger import get_logger
    log = get_logger("my_module")
    log.info("Starting task...")
    ```

---

## Best Practices for Developers

*   **Use the `MultiLLMClient`:** Never call LLM APIs directly in agents. Always use the `MultiLLMClient` to benefit from rate limiting and fallbacks.
*   **Close Connections:** Always use `Neo4jClient` as a context manager or explicitly call `.close()` to prevent connection leaks.
*   **Log Everything:** Use the centralized logger to ensure that events are captured in `results/runner_output.log` for troubleshooting.

---
*ASEI Utilities - Reliability and Scalability.*
