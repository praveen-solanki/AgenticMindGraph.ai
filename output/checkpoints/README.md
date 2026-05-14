# Pipeline Checkpoints (`output/checkpoints/`)

This directory contains the persistence layer for the 8-stage ingestion pipeline.

## Files
*   **`.json` files**: Contain the actual data output from each stage (e.g., `stage_04_chunk.json` contains the list of text chunks).
*   **`.done` files**: Marker files used by `main.py` to identify completed stages and skip them during a resume.

## Recovery
To force the pipeline to re-run a specific stage, delete both the `.json` and the `.done` file for that stage and run `main.py`.
