# ASEI: Autonomous Self-Evolving Research Intelligence for AUTOSAR

ASEI is a cutting-edge, agent-driven platform designed to ingest, analyze, and evolve knowledge from complex technical corpuses, specifically tailored for the **AUTOSAR (AUTomotive Open System ARchitecture)** ecosystem. The system transforms static PDF specifications into a dynamic, self-evolving Knowledge Graph (KG) powered by Neo4j and Large Language Models (LLMs).

## Project Overview

The ASEI system is divided into two primary subsystems:

1.  **The Ingestion Pipeline (`pipeline/`):** A multi-stage ETL process that extracts text from AUTOSAR PDFs, cleans noise, harvests requirement IDs, chunks content, extracts entities and relationships, resolves duplicates, and stores everything in a Neo4j Knowledge Graph.
2.  **The Agentic System (`agents/`):** A suite of autonomous agents that interact with the Knowledge Graph to detect drift (Evolution), identify contradictions (Conflict), propose new hypotheses (Synthesis), answer complex queries (Reasoning), and monitor system health (Watchdog).

### Core Features

*   **Automated KG Construction:** Converts raw PDFs into a structured graph with 1.1.0 schema versioning.
*   **Self-Evolution:** Detects stale information and updates the graph as the corpus evolves.
*   **Conflict Resolution:** Identifies and analyzes semantic contradictions between different parts of the specification.
*   **Agentic Reasoning:** Uses a multi-agent debate and reasoning approach to provide high-confidence answers to technical questions.
*   **Evaluation Framework:** A robust benchmarking tool to measure LLM performance against ground truth (GT) data.

---

## Technology Stack

*   **Languages:** Python 3.10+
*   **Graph Database:** Neo4j
*   **LLM Integration:** vLLM (local inference), OpenAI-compatible APIs (Groq, Sambanova, NVIDIA NIM, etc.)
*   **Embeddings:** BAAI/bge-m3 (1024-dim)
*   **Key Libraries:** `openai`, `neo4j`, `langgraph` (style), `rouge-score`, `bert-score`, `pypdfum2`, `sentence-transformers`.

---

## Repository Structure

```text
.
├── asei_runner.py        # Main CLI for the Agentic system
├── run.py                # Batch query runner for evaluation
├── eval.py               # Evaluation script for LLM outputs
├── agents/               # Autonomous agent implementations
├── pipeline/             # 8-stage data ingestion pipeline
├── config/               # Centralized configuration (settings.py)
├── utils/                # Supporting utilities (LLM clients, Neo4j, logging)
├── output/               # Checkpoints and state files
└── results/              # Logs and JSON output from runs
```

---

## Setup Instructions

### 1. Prerequisites
*   **Neo4j:** Install and start a Neo4j instance. Default credentials expected: `neo4j/autosar123` at `bolt://localhost:7687`.
*   **vLLM (Optional):** If running local inference, start a vLLM server on port 8011.
*   **Python Environment:**
    ```bash
    pip install -r requirements.txt
    ```

### 2. Environment Variables
Create a `.env` file in the root directory (see `.env.example` if available) with the following:
```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=.............
VLLM_URL=http://localhost:8011/v1
GROQ_API_KEY=your_key
SAMBANOVA_API_KEY=your_key
NVIDIA_API_KEY=your_key
# ... other provider keys
```

---

## Usage Guide

### 1. Data Ingestion
Run the pipeline to populate the Knowledge Graph from a folder of PDFs:
```bash
python pipeline/main.py --pdf-dir ./pdfs --output-dir ./output
```
*   `--fresh`: Discard all checkpoints and restart.
*   `--from-stage N`: Resume from a specific stage (1-8).

### 2. Running Agents
Use `asei_runner.py` to interact with the agents:
```bash
# Run a full autonomous cycle
python asei_runner.py cycle

# Ask a specific question using the Reasoning Agent
python asei_runner.py ask "What are the requirements for NvM_WriteBlock?"

# Run a specific agent
python asei_runner.py evolution
python asei_runner.py conflict
```

### 3. Evaluation
To run a batch of questions from a JSON file and evaluate the results:
```bash
# Run queries
python run.py

# Evaluate outputs
python eval.py --gt path/to/gold.json --input results.json --output eval_results.json
```

---

## Configuration

All tunable parameters are located in `config/settings.py`. This includes:
*   **LLM Models:** Selection of models for different agent roles.
*   **Thresholds:** Confidence scores, similarity thresholds, and staleness limits.
*   **Schema:** `ALLOWED_NODES` and `ALLOWED_RELATIONSHIPS` for the Knowledge Graph.

---

## Troubleshooting

*   **Neo4j Connection Failed:** Verify the URI and credentials in `.env` and ensure Neo4j is running.
*   **LLM Timeout:** Increase `LLM_TIMEOUT` in `config/settings.py` if using slow models or high concurrency.
*   **JSON Parse Errors:** The runner includes robust extraction logic, but extremely noisy LLM output might still cause issues. Check `results/runner_output.log`.

---

## Contribution

When contributing, ensure that:
1.  All new agents are registered in `asei_runner.py`.
2.  Any schema changes are reflected in `config/settings.py` and the `PIPELINE_VERSION` is bumped.
3.  New utilities follow the singleton/client pattern used in `utils/`.

---
