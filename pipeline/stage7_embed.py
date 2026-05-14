"""
pipeline/stage7_embed.py
========================
Stage 7: Embed all chunk texts using sentence-transformers BGE-M3.

Two embeddings per chunk:
  1. embed_text  (cleaned_text from Stage 4 LLM enrichment) — for retrieval
  2. summary embedding — for high-level question matching

Both vectors are stored on each chunk dict.
The raw Markdown (text) is preserved in Neo4j for display; only the clean
version goes to the vector index.

Output: same list of chunk dicts, each with:
    "embedding"         — 1024-dim list (embed_text vector)
    "summary_embedding" — 1024-dim list (summary vector)
"""

from __future__ import annotations

import sys
from typing import Optional

from utils.logger import get_logger
from config import settings

log = get_logger("stage7")


def run(chunks: list[dict]) -> list[dict]:
    """
    Embed all chunks. Returns chunks with "embedding" and "summary_embedding" fields added.
    """
    try:
        from sentence_transformers import SentenceTransformer
        import torch
        import numpy as np
    except ImportError:
        sys.exit(
            "sentence-transformers or torch not installed.\n"
            "Run: pip install sentence-transformers torch"
        )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        log.warning("CUDA not available — embedding on CPU (will be slow for large corpora)")

    batch_size = (
        settings.EMBED_BATCH_SIZE_GPU if device == "cuda"
        else settings.EMBED_BATCH_SIZE_CPU
    )

    log.info(
        "Stage 7: embedding %d chunks with %s on %s (batch=%d)",
        len(chunks), settings.EMBED_MODEL, device, batch_size,
    )

    model = SentenceTransformer(settings.EMBED_MODEL, device=device)

    # ── Primary embeddings: cleaned_text (stripped Markdown, for retrieval) ──
    # Fall back to text if cleaned_text not populated by Stage 4 LLM enrichment
    embed_texts = [
        c.get("embed_text") or c.get("cleaned_text") or c["text"]
        for c in chunks
    ]

    log.info("  Encoding primary (embed_text) vectors ...")
    primary_embeddings = model.encode(
        embed_texts,
        normalize_embeddings=settings.EMBED_NORMALIZE,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    )

    actual_dim = primary_embeddings.shape[1]
    if actual_dim != settings.EMBED_DIM:
        log.warning(
            "Embedding dim mismatch: expected %d, got %d. "
            "Update EMBED_DIM in config/settings.py",
            settings.EMBED_DIM, actual_dim,
        )

    # ── Summary embeddings: one-sentence summaries from Stage 4 ──────────────
    summary_texts = []
    for c in chunks:
        summary = c.get("summary", "").strip()
        if summary:
            summary_texts.append(summary)
        else:
            # Fall back to first 100 chars of embed_text for chunks without summary
            summary_texts.append((c.get("embed_text") or c["text"])[:100])

    log.info("  Encoding summary vectors ...")
    summary_embeddings = model.encode(
        summary_texts,
        normalize_embeddings=settings.EMBED_NORMALIZE,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
    )

    # ── Attach both embeddings to each chunk ─────────────────────────────────
    for i, chunk in enumerate(chunks):
        chunk["embedding"]         = primary_embeddings[i].tolist()
        chunk["summary_embedding"] = summary_embeddings[i].tolist()

    log.info(
        "Stage 7 complete: %d primary + %d summary embeddings (dim=%d)",
        len(chunks), len(chunks), actual_dim,
    )
    return chunks
