# ASEI Results and Logs (`results/`)

This directory stores the artifacts generated during execution, including detailed execution logs and the final output of batch query runs.

## Important Files

### 1. Runner Output Log (`runner_output.log`)
*   **Purpose:** The central log file for the `run.py` script and the `asei_runner.py` tool.
*   **Content:**
    *   **Raw LLM Payloads:** Captures the full STDOUT and STDERR from subprocess calls.
    *   **JSON Extraction Logs:** Details the balanced-bracket scanning process used to extract JSON from noisy LLM output.
    *   **Errors:** Provides full tracebacks for subprocess failures, JSON decode errors, and API timeouts.
*   **Utility:** This is the first place to look if an agent fails or if a query returns "None" or "Error".

### 2. Results JSON (`results.json`)
*   **Purpose:** Stores the consolidated output of a batch query run (triggered by `run.py`).
*   **Format:** A list of JSON objects, each containing:
    *   `question`: The input query.
    *   `answer`: The generated response.
    *   `reasoning`: The chain-of-thought or debate summary.
    *   `evidence`: Citations and graph paths used.
    *   `confidence`: The numerical confidence score assigned by the agent.
    *   `errors`: Any specific errors encountered for this query.

### 3. Pipeline Logs
While the pipeline logs primarily to the console, any redirected output or specific stage-level summaries may also be found here depending on the execution environment.

---

## Data Lifecycle

*   **`runner_output.log`**: This file is typically cleared and restarted at the beginning of each `run.py` execution to prevent it from becoming excessively large.
*   **`results.json`**: This file is overwritten by `run.py`. If you want to save results from multiple experiments, rename the file or copy it to a different location.
*   **`output.json`**: An intermediate or alternative result file often used during development or debugging of the extraction logic.

---

## Troubleshooting via Logs

If you see inconsistent results in `results.json`, search `runner_output.log` for the specific question text. Look for:
1.  **"JSON PARSE ERROR"**: Indicates the LLM provided an invalid response format.
2.  **"SUBPROCESS ERROR"**: Indicates a crash in `asei_runner.py` (check for Neo4j connection issues).
3.  **"Balanced-scan slice"**: A preview of the text the system tried to parse as JSON, useful for identifying why parsing failed.

---
*ASEI Results - Transparent and Auditable.*
