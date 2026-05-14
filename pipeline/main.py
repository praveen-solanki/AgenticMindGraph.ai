"""
main.py
=======
Orchestrator for the AUTOSAR KG pipeline.

Runs all 8 stages in order. Checkpoints after every stage so a crash
can be resumed from where it stopped.

Usage:
    python main.py --pdf-dir ./pdfs --output-dir ./output
    python main.py --pdf-dir ./pdfs --output-dir ./output --from-stage 4
    python main.py --pdf-dir ./pdfs --output-dir ./output --fresh
    python main.py --pdf-dir ./pdfs --output-dir ./output --debug
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from utils.logger import get_logger, set_debug
from utils.checkpoint import CheckpointManager

log = get_logger("main")


# ── Stage descriptors ─────────────────────────────────────────────────────────
# (stage_number, internal_name, human_label)
STAGES = [
    (0, "corpus",   "Corpus Analysis"),
    (1, "extract",  "PDF Extraction"),
    (2, "clean",    "Noise Removal"),
    (3, "harvest",  "Requirement ID Harvesting"),
    (4, "chunk",    "Chunking"),
    (5, "entities", "Entity & Relation Extraction"),
    (6, "resolve",  "Entity Resolution"),
    (7, "embed",    "Embedding"),
    (8, "store",    "Graph Storage (Neo4j)"),
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="AUTOSAR PDF → Neo4j Knowledge Graph pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--pdf-dir",    required=True,  help="Directory containing AUTOSAR PDFs")
    p.add_argument("--output-dir", required=True,  help="Root output / checkpoint directory")
    p.add_argument(
        "--from-stage", type=int, default=None, metavar="N",
        help="Force re-run from stage N (1-8). Discards checkpoints for stages N and later.",
    )
    p.add_argument(
        "--fresh", action="store_true",
        help="Discard ALL checkpoints and restart from stage 1.",
    )
    p.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    return p.parse_args()


def _apply_dynamic_schema(corpus_meta: dict) -> None:
    """
    Apply extra node/relationship types recommended by Stage 0 corpus analysis
    to settings.ALLOWED_NODES and settings.ALLOWED_RELATIONSHIPS for this run.
    """
    from config import settings as s

    extra_nodes = corpus_meta.get("extra_node_types", [])
    extra_rels  = corpus_meta.get("extra_relationship_types", [])

    added_nodes = 0
    for node_type in extra_nodes:
        if node_type and node_type not in s.ALLOWED_NODES:
            s.ALLOWED_NODES.append(node_type)
            added_nodes += 1

    added_rels = 0
    for rel_type in extra_rels:
        if rel_type and rel_type not in s.ALLOWED_RELATIONSHIPS:
            s.ALLOWED_RELATIONSHIPS.append(rel_type)
            added_rels += 1

    if added_nodes or added_rels:
        log.info(
            "  Dynamic schema: added %d node type(s), %d relationship type(s) from Stage 0",
            added_nodes, added_rels,
        )


def main() -> None:
    args       = parse_args()
    pdf_dir    = Path(args.pdf_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.debug:
        set_debug(True)

    ckpt = CheckpointManager(output_dir)

    # ── Invalidate checkpoints if requested ───────────────────────────────────
    if args.fresh:
        log.info("--fresh: clearing all checkpoints")
        ckpt.clear_all()
    elif args.from_stage is not None:
        log.info("--from-stage %d: invalidating checkpoints from stage %d onwards",
                 args.from_stage, args.from_stage)
        ckpt.invalidate_from(args.from_stage)

    log.info("=" * 70)
    log.info(" AUTOSAR KG Pipeline")
    log.info(" PDFs:   %s", pdf_dir)
    log.info(" Output: %s", output_dir)
    log.info("=" * 70)

    t_total = time.time()

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 1 — PDF Extraction
    # ══════════════════════════════════════════════════════════════════════════
    pages: list[dict]
    if ckpt.is_done(1, "extract"):
        log.info("[1/8] PDF Extraction — SKIPPED (checkpoint found)")
        pages = ckpt.load(1, "extract")
    else:
        log.info("[1/8] PDF Extraction")
        from pipeline import stage1_extract
        t0    = time.time()
        pages = stage1_extract.run(pdf_dir)
        ckpt.save(1, "extract", pages)
        log.info("  Done in %.0fs — %d pages", time.time() - t0, len(pages))

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 0 — Corpus-Level Analysis (runs after Stage 1 extraction)
    # ══════════════════════════════════════════════════════════════════════════
    corpus_meta: dict
    if ckpt.is_done(0, "corpus"):
        log.info("[0/8] Corpus Analysis — SKIPPED (checkpoint found)")
        corpus_meta = ckpt.load(0, "corpus")
    else:
        log.info("[0/8] Corpus Analysis")
        from pipeline import stage0_corpus_analysis
        t0          = time.time()
        corpus_meta = stage0_corpus_analysis.run(pdf_dir, pages)
        ckpt.save(0, "corpus", corpus_meta)
        log.info("  Done in %.0fs — corpus_type=%s", time.time() - t0, corpus_meta.get("corpus_type"))

    # Apply dynamic schema from Stage 0 to settings
    _apply_dynamic_schema(corpus_meta)

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 2 — Noise Removal
    # ══════════════════════════════════════════════════════════════════════════
    clean_pages: list[dict]
    if ckpt.is_done(2, "clean"):
        log.info("[2/8] Noise Removal — SKIPPED (checkpoint found)")
        clean_pages = ckpt.load(2, "clean")
    else:
        log.info("[2/8] Noise Removal")
        from pipeline import stage2_clean
        t0          = time.time()
        clean_pages = stage2_clean.run(pages)
        ckpt.save(2, "clean", clean_pages)
        log.info("  Done in %.0fs — %d pages kept", time.time() - t0, len(clean_pages))

    # Free raw pages from memory
    del pages

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 3 — Requirement ID Harvesting
    # ══════════════════════════════════════════════════════════════════════════
    harvest: dict
    if ckpt.is_done(3, "harvest"):
        log.info("[3/8] Requirement ID Harvesting — SKIPPED (checkpoint found)")
        harvest = ckpt.load(3, "harvest")
    else:
        log.info("[3/8] Requirement ID Harvesting")
        from pipeline import stage3_harvest
        t0      = time.time()
        harvest = stage3_harvest.run(clean_pages, corpus_meta=corpus_meta)
        ckpt.save(3, "harvest", harvest)
        log.info(
            "  Done in %.0fs — %d IDs, %d cross-refs",
            time.time() - t0,
            len(harvest["id_inventory"]),
            len(harvest["cross_refs"]),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 4 — Chunking
    # ══════════════════════════════════════════════════════════════════════════
    chunks:        list[dict]
    config_params: list[dict]
    if ckpt.is_done(4, "chunk"):
        log.info("[4/8] Chunking — SKIPPED (checkpoint found)")
        stage4_data   = ckpt.load(4, "chunk")
        chunks        = stage4_data["chunks"]
        config_params = stage4_data["config_params"]
    else:
        log.info("[4/8] Chunking")
        from pipeline import stage4_chunk
        t0            = time.time()
        chunks, config_params = stage4_chunk.run(clean_pages)
        ckpt.save(4, "chunk", {"chunks": chunks, "config_params": config_params})
        log.info(
            "  Done in %.0fs — %d chunks, %d config params",
            time.time() - t0, len(chunks), len(config_params),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 5 — Entity & Relation Extraction
    # ══════════════════════════════════════════════════════════════════════════
    entity_data: dict
    if ckpt.is_done(5, "entities"):
        log.info("[5/8] Entity & Relation Extraction — SKIPPED (checkpoint found)")
        entity_data = ckpt.load(5, "entities")
    else:
        log.info("[5/8] Entity & Relation Extraction")
        from pipeline import stage5_extract_entities
        t0          = time.time()
        entity_data = stage5_extract_entities.run(chunks, harvest, config_params)
        ckpt.save(5, "entities", entity_data)
        log.info(
            "  Done in %.0fs — %d nodes, %d relationships",
            time.time() - t0,
            len(entity_data["nodes"]),
            len(entity_data["relationships"]),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 6 — Entity Resolution
    # ══════════════════════════════════════════════════════════════════════════
    resolved_data: dict
    if ckpt.is_done(6, "resolve"):
        log.info("[6/8] Entity Resolution — SKIPPED (checkpoint found)")
        resolved_data = ckpt.load(6, "resolve")
    else:
        log.info("[6/8] Entity Resolution")
        from pipeline import stage6_resolve
        t0            = time.time()
        resolved_data = stage6_resolve.run(entity_data)
        ckpt.save(6, "resolve", resolved_data)
        log.info(
            "  Done in %.0fs — %d nodes after dedup",
            time.time() - t0, len(resolved_data["nodes"]),
        )

    # Free entity_data from memory after resolution
    del entity_data

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 7 — Embedding
    # ══════════════════════════════════════════════════════════════════════════
    embedded_chunks: list[dict]
    if ckpt.is_done(7, "embed"):
        log.info("[7/8] Embedding — SKIPPED (checkpoint found)")
        embedded_chunks = ckpt.load(7, "embed")
    else:
        log.info("[7/8] Embedding")
        from pipeline import stage7_embed
        t0              = time.time()
        embedded_chunks = stage7_embed.run(chunks)
        ckpt.save(7, "embed", embedded_chunks)
        log.info(
            "  Done in %.0fs — %d embeddings",
            time.time() - t0, len(embedded_chunks),
        )

    # ══════════════════════════════════════════════════════════════════════════
    # STAGE 8 — Neo4j Storage
    # ══════════════════════════════════════════════════════════════════════════
    if ckpt.is_done(8, "store"):
        log.info("[8/8] Graph Storage — SKIPPED (checkpoint found)")
        log.info("  KG already stored in Neo4j. Use --from-stage 8 to re-store.")
    else:
        log.info("[8/8] Graph Storage → Neo4j")
        from pipeline import stage8_store
        t0 = time.time()
        stage8_store.run(
            chunks=embedded_chunks,
            entity_data=resolved_data,
            config_params=config_params,
            pages=clean_pages,
        )
        ckpt.save(8, "store", {"status": "complete", "ts": time.time()})
        log.info("  Done in %.0fs", time.time() - t0)

    # ══════════════════════════════════════════════════════════════════════════
    # Done
    # ══════════════════════════════════════════════════════════════════════════
    total_elapsed = time.time() - t_total
    log.info("")
    log.info("=" * 70)
    log.info(" Pipeline complete in %.0fm %.0fs",
             total_elapsed // 60, total_elapsed % 60)
    log.info(" Neo4j browser: http://localhost:7474")
    log.info(" Useful Cypher queries to verify your graph:")
    log.info("   MATCH (n) RETURN labels(n)[0] AS label, count(n) ORDER BY count(n) DESC")
    log.info("   MATCH (r:Requirement) RETURN r LIMIT 5")
    log.info("   MATCH (m:Module)-[:HAS_REQUIREMENT]->(r) RETURN m.name, count(r)")
    log.info("   MATCH p=(:Requirement)-[:REFERENCES]->(:Requirement) RETURN p LIMIT 10")
    log.info("=" * 70)


if __name__ == "__main__":
    main()
