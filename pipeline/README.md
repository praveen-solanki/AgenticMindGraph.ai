# ASEI Ingestion Pipeline (`pipeline/`)

The Ingestion Pipeline is a robust, multi-stage ETL system designed to convert unstructured AUTOSAR PDF specifications into a high-fidelity Neo4j Knowledge Graph.

## Pipeline Architecture

The pipeline is orchestrated by `main.py` and consists of 9 sequential stages (0-8). It uses a checkpointing mechanism to allow resuming from any stage in case of failure.

### Stage 0: Corpus Analysis (`stage0_corpus_analysis.py`)
*   **Purpose:** Analyzes the directory of PDFs to understand the document types (SWS, SRS, TPS, etc.).
*   **Action:** Detects specific AUTOSAR domains and suggests dynamic additions to the graph schema (`ALLOWED_NODES`, `ALLOWED_RELATIONSHIPS`).
*   **Run after Stage 1:** It requires the raw extracted text to perform analysis.

### Stage 1: PDF Extraction (`stage1_extract.py`)
*   **Purpose:** Raw text extraction from PDFs.
*   **Action:** Uses `pypdfum2` to extract text from every page. It applies header/footer margins (defined in `settings.py`) to ignore page numbers and standard headers.

### Stage 2: Noise Removal (`stage2_clean.py`)
*   **Purpose:** Filters out non-content text.
*   **Action:** Removes repeated lines (running headers/footers), Table of Contents (TOC) pages, revision history tables, and near-blank pages. It also handles orphaned captions.

### Stage 3: Requirement ID Harvesting (`stage3_harvest.py`)
*   **Purpose:** Identifies formal AUTOSAR requirement IDs.
*   **Action:** Uses regex patterns (e.g., `[SWS_NvM_00001]`) to build an inventory of requirements and their cross-references within the text.

### Stage 4: Chunking (`stage4_chunk.py`)
*   **Purpose:** Segments the cleaned text for LLM processing.
*   **Action:** Splits text into semantic chunks based on headers (H1-H5) and token limits. It ensures overlap between chunks to maintain context and handles large tables separately.

### Stage 5: Entity & Relation Extraction (`stage5_extract_entities.py`)
*   **Purpose:** The "intelligence" stage of the pipeline.
*   **Action:** Sends chunks to an LLM (typically a heavy model like Qwen-72B) to extract nodes (Requirements, Modules, Functions) and relationships (REFERENCES, IMPLEMENTS, CONFIGURES) based on the project schema.

### Stage 6: Entity Resolution (`stage6_resolve.py`)
*   **Purpose:** Deduplication and normalization.
*   **Action:** Uses vector embeddings to find similar entities and an LLM to decide if they should be merged. It also applies canonical name overrides for common AUTOSAR abbreviations (e.g., "Comm Manager" -> "ComM").

### Stage 7: Embedding (`stage7_embed.py`)
*   **Purpose:** Vectorization of content.
*   **Action:** Generates high-dimensional embeddings for every chunk using the `BAAI/bge-m3` model. These are used for semantic search and retrieval during reasoning.

### Stage 8: Graph Storage (`stage8_store.py`)
*   **Purpose:** Final ingestion.
*   **Action:** Commits all extracted nodes, relationships, chunks, and metadata to Neo4j. It creates `SIMILAR_TO` edges between chunks based on vector similarity (k-NN).

---

## Running the Pipeline

### Basic Run
```bash
python pipeline/main.py --pdf-dir ./pdfs --output-dir ./output
```

### Resume from Stage
If the pipeline fails at Stage 5:
```bash
python pipeline/main.py --pdf-dir ./pdfs --output-dir ./output --from-stage 5
```

### Fresh Restart
```bash
python pipeline/main.py --pdf-dir ./pdfs --output-dir ./output --fresh
```

---

## Configuration

The pipeline's behavior is highly tunable via `config/settings.py`:
*   **Margins:** `PDF_HEADER_MARGIN`, `PDF_FOOTER_MARGIN`.
*   **Thresholds:** `REPEATED_LINE_THRESHOLD`, `TOC_LINE_RATIO_THRESHOLD`.
*   **ID Patterns:** `REQUIREMENT_ID_PATTERNS`.
*   **Chunking:** `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS`.
*   **Schema:** `ALLOWED_NODES`, `ALLOWED_RELATIONSHIPS`.

---

## Common Errors & Troubleshooting

1.  **Stage 1 Failure:** Usually due to corrupted PDFs or restricted file permissions.
2.  **Stage 5 JSON Errors:** If the LLM returns malformed JSON, the pipeline will log the error but may skip the chunk. Check `results/runner_output.log`.
3.  **Stage 8 Neo4j Error:** Ensure Neo4j is reachable and the password in `settings.py` matches. Use `MATCH (n) DETACH DELETE n` in Neo4j to clear the graph if needed before a fresh run.

---
*ASEI Ingestion - Building the Foundation of Autonomous Intelligence.*
