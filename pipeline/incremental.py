"""
pipeline/incremental.py
========================
Incremental PDF ingestion — processes only new documents and merges
them into the existing Knowledge Graph without reprocessing the corpus.

Usage:
    python -m pipeline.incremental --add-pdf ./new_document.pdf --output-dir ./output
    python -m pipeline.incremental --add-pdf-dir ./new_pdfs/ --output-dir ./output

Architecture:
    1. Detects which PDFs are new (not already in the graph via Document nodes)
    2. Runs Stages 1-7 on ONLY the new PDFs
    3. Stage 8 uses MERGE semantics so new nodes/edges integrate seamlessly
    4. Entity resolution runs on new entities against the EXISTING graph
    5. Vector indexes are updated incrementally

Key Design Decisions:
    - Does NOT re-run corpus analysis (Stage 0) — uses existing schema
    - Does NOT invalidate any checkpoints for the full pipeline
    - Uses separate checkpoint namespace ("incr_") to track incremental runs
    - MERGE-based writes ensure idempotency — safe to re-run
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from utils.logger import get_logger, set_debug
from utils.neo4j_client import Neo4jClient
from config import settings

log = get_logger("incremental")


def _get_existing_documents(neo: Neo4jClient) -> set[str]:
    """
    Query Neo4j for all Document.filename values already in the graph.
    Returns a set of filenames (e.g. {"AUTOSAR_SWS_ComM.pdf", ...}).
    """
    try:
        rows = neo.run("MATCH (d:Document) RETURN d.filename AS filename")
        return {r["filename"] for r in rows if r.get("filename")}
    except Exception as exc:
        log.warning("Could not query existing documents: %s", exc)
        return set()


def _filter_new_pdfs(pdf_paths: list[Path], existing: set[str]) -> list[Path]:
    """Filter out PDFs that are already in the graph."""
    new_pdfs = [p for p in pdf_paths if p.name not in existing]
    skipped = len(pdf_paths) - len(new_pdfs)
    if skipped:
        log.info("  Skipping %d PDF(s) already in graph", skipped)
    return new_pdfs


def run_incremental(
    pdf_paths: list[Path],
    output_dir: Path,
    debug: bool = False,
) -> dict:
    """
    Run the incremental ingestion pipeline on new PDFs only.

    Args:
        pdf_paths:  List of PDF file paths to ingest
        output_dir: Root output directory (for temp checkpoints)
        debug:      Enable debug logging

    Returns:
        Summary dict with counts of pages, chunks, nodes, relationships added.
    """
    if debug:
        set_debug(True)

    summary = {
        "new_pdfs": 0,
        "pages_extracted": 0,
        "pages_cleaned": 0,
        "ids_harvested": 0,
        "chunks_created": 0,
        "nodes_added": 0,
        "relationships_added": 0,
        "errors": [],
    }

    # Validate inputs
    valid_pdfs = [p for p in pdf_paths if p.exists() and p.suffix.lower() == ".pdf"]
    if not valid_pdfs:
        log.error("No valid PDF files found in input")
        summary["errors"].append("No valid PDF files found")
        return summary

    log.info("=" * 70)
    log.info(" ASEI Incremental Ingestion")
    log.info(" Input: %d PDF(s)", len(valid_pdfs))
    log.info("=" * 70)

    t_total = time.time()

    # ── Check which PDFs are already in the graph ─────────────────────────────
    with Neo4jClient() as neo:
        existing = _get_existing_documents(neo)
    log.info("  Existing documents in graph: %d", len(existing))

    new_pdfs = _filter_new_pdfs(valid_pdfs, existing)
    if not new_pdfs:
        log.info("  All PDFs already in graph — nothing to do")
        return summary

    summary["new_pdfs"] = len(new_pdfs)
    log.info("  New PDFs to ingest: %d", len(new_pdfs))

    # Create a temporary directory for incremental processing
    incr_dir = output_dir / "incremental_temp"
    incr_dir.mkdir(parents=True, exist_ok=True)

    # Create a temporary PDF directory with symlinks to new PDFs
    temp_pdf_dir = incr_dir / "pdfs"
    temp_pdf_dir.mkdir(parents=True, exist_ok=True)
    for pdf in new_pdfs:
        target = temp_pdf_dir / pdf.name
        if not target.exists():
            target.symlink_to(pdf.resolve())

    try:
        # ══════════════════════════════════════════════════════════════════════
        # STAGE 1 — PDF Extraction (new PDFs only)
        # ══════════════════════════════════════════════════════════════════════
        log.info("[1/7] PDF Extraction (incremental)")
        from pipeline import stage1_extract
        t0 = time.time()
        pages = stage1_extract.run(temp_pdf_dir)
        summary["pages_extracted"] = len(pages)
        log.info("  Done in %.0fs — %d pages", time.time() - t0, len(pages))

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 2 — Noise Removal
        # ══════════════════════════════════════════════════════════════════════
        log.info("[2/7] Noise Removal (incremental)")
        from pipeline import stage2_clean
        t0 = time.time()
        clean_pages = stage2_clean.run(pages)
        summary["pages_cleaned"] = len(clean_pages)
        log.info("  Done in %.0fs — %d pages kept", time.time() - t0, len(clean_pages))
        del pages

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 3 — Requirement ID Harvesting
        # ══════════════════════════════════════════════════════════════════════
        log.info("[3/7] Requirement ID Harvesting (incremental)")
        from pipeline import stage3_harvest
        t0 = time.time()
        # Use existing corpus_meta if available, else None (will use filename regex fallback)
        from utils.checkpoint import CheckpointManager
        ckpt = CheckpointManager(output_dir)
        corpus_meta = None
        if ckpt.is_done(0, "corpus"):
            corpus_meta = ckpt.load(0, "corpus")

        harvest = stage3_harvest.run(clean_pages, corpus_meta=corpus_meta)
        summary["ids_harvested"] = len(harvest["id_inventory"])
        log.info(
            "  Done in %.0fs — %d IDs, %d cross-refs",
            time.time() - t0,
            len(harvest["id_inventory"]),
            len(harvest["cross_refs"]),
        )

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 4 — Chunking
        # ══════════════════════════════════════════════════════════════════════
        log.info("[4/7] Chunking (incremental)")
        from pipeline import stage4_chunk
        t0 = time.time()
        chunks, config_params = stage4_chunk.run(clean_pages)
        summary["chunks_created"] = len(chunks)
        log.info(
            "  Done in %.0fs — %d chunks, %d config params",
            time.time() - t0, len(chunks), len(config_params),
        )

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 5 — Entity & Relation Extraction
        # ══════════════════════════════════════════════════════════════════════
        log.info("[5/7] Entity & Relation Extraction (incremental)")
        from pipeline import stage5_extract_entities
        t0 = time.time()
        entity_data = stage5_extract_entities.run(chunks, harvest, config_params)
        log.info(
            "  Done in %.0fs — %d nodes, %d relationships",
            time.time() - t0,
            len(entity_data["nodes"]),
            len(entity_data["relationships"]),
        )

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 6 — Entity Resolution (against existing graph)
        # ══════════════════════════════════════════════════════════════════════
        log.info("[6/7] Entity Resolution (incremental — merges with existing graph)")
        from pipeline import stage6_resolve
        t0 = time.time()
        resolved_data = stage6_resolve.run(entity_data)
        summary["nodes_added"] = len(resolved_data["nodes"])
        summary["relationships_added"] = len(resolved_data["relationships"])
        log.info(
            "  Done in %.0fs — %d nodes after dedup",
            time.time() - t0, len(resolved_data["nodes"]),
        )
        del entity_data

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 7 — Embedding
        # ══════════════════════════════════════════════════════════════════════
        log.info("[7/7] Embedding (incremental)")
        from pipeline import stage7_embed
        t0 = time.time()
        embedded_chunks = stage7_embed.run(chunks)
        log.info(
            "  Done in %.0fs — %d embeddings",
            time.time() - t0, len(embedded_chunks),
        )

        # ══════════════════════════════════════════════════════════════════════
        # STAGE 8 — Neo4j Storage (MERGE — safe for incremental)
        # ══════════════════════════════════════════════════════════════════════
        log.info("[+] Graph Storage → Neo4j (MERGE mode)")
        from pipeline import stage8_store
        t0 = time.time()
        stage8_store.run(
            chunks=embedded_chunks,
            entity_data=resolved_data,
            config_params=config_params,
            pages=clean_pages,
        )
        log.info("  Done in %.0fs", time.time() - t0)

    except Exception as exc:
        msg = f"Incremental ingestion failed: {exc}"
        log.error(msg)
        summary["errors"].append(msg)
    finally:
        # Clean up temp symlinks
        if temp_pdf_dir.exists():
            for link in temp_pdf_dir.iterdir():
                if link.is_symlink():
                    link.unlink()

    total_elapsed = time.time() - t_total
    log.info("")
    log.info("=" * 70)
    log.info(" Incremental ingestion complete in %.0fm %.0fs",
             total_elapsed // 60, total_elapsed % 60)
    log.info(" Added: %d pages, %d chunks, %d nodes, %d relationships",
             summary["pages_cleaned"], summary["chunks_created"],
             summary["nodes_added"], summary["relationships_added"])
    log.info("=" * 70)

    return summary


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ASEI Incremental PDF Ingestion — add new documents without reprocessing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--add-pdf", action="append", default=[],
        help="Path to a single PDF to ingest (repeatable)",
    )
    p.add_argument(
        "--add-pdf-dir", default=None,
        help="Directory containing new PDFs to ingest",
    )
    p.add_argument(
        "--output-dir", required=True,
        help="Root output directory (same as full pipeline)",
    )
    p.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)

    # Collect PDF paths
    pdf_paths: list[Path] = []

    for pdf_str in args.add_pdf:
        p = Path(pdf_str)
        if p.exists():
            pdf_paths.append(p)
        else:
            log.warning("PDF not found: %s", pdf_str)

    if args.add_pdf_dir:
        pdf_dir = Path(args.add_pdf_dir)
        if pdf_dir.is_dir():
            pdf_paths.extend(sorted(pdf_dir.glob("**/*.pdf")))
        else:
            log.error("Directory not found: %s", args.add_pdf_dir)
            sys.exit(1)

    if not pdf_paths:
        log.error("No PDFs specified. Use --add-pdf or --add-pdf-dir")
        sys.exit(1)

    import json
    summary = run_incremental(pdf_paths, output_dir, debug=args.debug)
    print(json.dumps(summary, indent=2))
    sys.exit(1 if summary["errors"] else 0)


if __name__ == "__main__":
    main()
