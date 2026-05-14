# ASEI Agent Layer (`agents/`)

This directory contains the autonomous agents that power the self-evolving intelligence of the ASEI system. These agents interact with the Neo4j Knowledge Graph to maintain, expand, and query the technical knowledge base.

## Core Agents

### 1. Evolution Agent (`evolution_agent.py`)
*   **Purpose:** Monitors the Knowledge Graph for "staleness" or "drift".
*   **Logic:** Identifies nodes that haven't been updated recently or were created with an older version of the ingestion pipeline.
*   **Output:** Flags nodes for re-processing or verification.

### 2. Conflict Agent (`conflict_agent.py`)
*   **Purpose:** Detects structural and semantic contradictions within the AUTOSAR specifications.
*   **Logic:** Uses LLMs to compare related requirements and identify logical inconsistencies or conflicting parameters.
*   **Output:** Creates `CONTRADICTS` relationships between nodes in the graph.

### 3. Synthesis Agent (`synthesis_agent.py`)
*   **Purpose:** Proposes new hypotheses, "bridge" requirements, or consolidated concepts.
*   **Logic:** Analyzes disconnected components or conflicting areas to suggest logical resolutions or missing links.
*   **Output:** Generates candidate hypothesis nodes.

### 4. Verification Agent (`verification_agent.py`)
*   **Purpose:** Acts as a gatekeeper for the Synthesis Agent.
*   **Logic:** Critically evaluates proposed hypotheses for technical accuracy and alignment with existing specifications.
*   **Output:** Approves or rejects hypotheses, which are then committed to the graph.

### 5. Reasoning Agent (`reasoning_agent.py`)
*   **Purpose:** Provides high-fidelity answers to user questions.
*   **Logic:** Employs a multi-agent debate architecture:
    *   **Prosecutor:** Argues for a specific interpretation.
    *   **Defender:** Provides a counter-argument or alternative context.
    *   **Skeptic:** Critiques both and looks for logical fallacies.
*   **Output:** A consolidated answer with a confidence score and evidence paths.

### 6. Gap Detection Agent (`gap_detection_agent.py`)
*   **Purpose:** Identifies "holes" in the specification where information is expected but missing.
*   **Logic:** Analyzes the graph topology for missing relationships (e.g., a requirement with no implementation) or orphaned concepts.
*   **Output:** Reports potential gaps for further synthesis or manual review.

### 7. Impact Agent (`impact_agent.py`)
*   **Purpose:** Analyzes the ripple effect of a change.
*   **Logic:** Performs multi-hop traversal in the graph to identify all requirements, modules, and functions that would be affected by a modification to a specific node.
*   **Output:** A list of impacted entities and the reasoning for their inclusion.

### 8. Summarization Agent (`summarization_agent.py`)
*   **Purpose:** Generates high-level technical summaries.
*   **Logic:** Aggregates content from document clusters or module sub-graphs to provide "at-a-glance" overviews.
*   **Output:** Updates summary properties on `Module` or `Document` nodes.

### 9. Watchdog Agent (`watchdog_agent.py`)
*   **Purpose:** Monitors the performance and quality of the autonomous system.
*   **Logic:** Tracks error rates, agent completion times, and the ratio of approved/rejected hypotheses.
*   **Output:** Health reports and alerts for system administrators.

### 10. Query Memory Agent (`query_memory_agent.py`)
*   **Purpose:** Optimizes future reasoning tasks.
*   **Logic:** Stores successful reasoning paths and query patterns to accelerate repeat or similar queries.
*   **Output:** Populates the `QueryPattern` nodes in the graph.

---

## Orchestration and Routing

### Orchestrator (`orchestrator.py`)
The Orchestrator is the "brain" of the agent cycle. It:
*   Maintains the `OrchestratorState`.
*   Sequentially executes agents (Evolution → Conflict → Synthesis → Verification → etc.).
*   Handles checkpointing for crash recovery.
*   Supports both "single-cycle" and "continuous service" modes.

### Router (`router.py`)
The Router manages the task-to-LLM-provider mapping. It ensures that:
*   Heavy reasoning tasks are sent to powerful models (e.g., GPT-4o, Llama-3-70B).
*   Fast classification or extraction tasks are sent to smaller, faster models.
*   Routing chains are respected for fallback scenarios.

---

## How to Add a New Agent

1.  **Create the script:** Define a `run(neo: Neo4jClient, ...)` function that returns a dataclass or dict.
2.  **Register in Orchestrator:** Add the agent to the `run_cycle` loop in `orchestrator.py`.
3.  **Update State:** Add a corresponding report field to the `OrchestratorState` dataclass.
4.  **Register in CLI:** Add a subcommand to `asei_runner.py`.
5.  **Configure:** Add any new thresholds or model assignments to `config/settings.py`.

---
*ASEI Agents - Driving Autonomous Technical Intelligence.*
