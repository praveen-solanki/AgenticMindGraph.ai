"""
pipeline/stage0_corpus_analysis.py
====================================
Stage 0: Corpus-level analysis before any PDF is processed.

LLM reads the list of document filenames + any first-page metadata
and returns:
  - Detected AUTOSAR corpus type (CP / AP / mixed)
  - Document type map: filename → {doc_type, module_abbrev, module_full, release}
  - Dynamic schema recommendations (additional node/relationship types)
  - Duplicate/version-conflict warnings

Output stored in corpus_meta dict, passed to all subsequent stages.
The doc_modules map from here replaces the broken filename-regex inference
in Stage 3.
"""

from __future__ import annotations

import json
from pathlib import Path

from utils.logger import get_logger
from utils.llm_client import call_llm_json
from config import settings

log = get_logger("stage0")

_SYSTEM = """You are an expert AUTOSAR architect with deep knowledge of both
Classic Platform (CP) and Adaptive Platform (AP) document conventions.

Your task: analyse a list of AUTOSAR document filenames (and optionally their
first-page text) and return a structured JSON response.

Return ONLY valid JSON, no markdown fences, no preamble.

JSON schema:
{
  "corpus_type": "CP" | "AP" | "mixed",
  "documents": {
    "<filename>": {
      "doc_type": "SWS" | "SRS" | "RS" | "EXP" | "TPS" | "TR" | "MOD" | "PRS" | "ATS" | "MMOD" | "OTHER",
      "module_abbrev": "<short abbreviation e.g. ComM, NvM, CSM, OSI>",
      "module_full": "<full module name e.g. Communication Manager>",
      "release": "<release string e.g. R22-11, 4.4.0 or null>",
      "duplicate_of": "<other filename if this appears to be a duplicate/older version, else null>"
    }
  },
  "extra_node_types": ["<type>", ...],
  "extra_relationship_types": ["<type>", ...],
  "notes": "<any important observations about this corpus>"
}

Rules:
- module_abbrev must use the official AUTOSAR abbreviation exactly (ComM not comm).
- If the filename is plain English (e.g. "Requirements on Operating System Interface.pdf"),
  infer the module from the title semantics.
- extra_node_types / extra_relationship_types: suggest ONLY types not already in the
  provided existing schema that would genuinely improve coverage for this corpus.
- Keep extra lists short (max 5 each). Do not suggest generic types."""


def run(pdf_dir: Path, pages: list[dict]) -> dict:
    """
    Analyse the corpus and return corpus_meta dict.

    Args:
        pdf_dir: directory containing PDFs
        pages:   raw pages from Stage 1 (used to read first-page text)
    """
    log.info("Stage 0: corpus analysis")

    pdfs = sorted(pdf_dir.glob("**/*.pdf"))
    if not pdfs:
        log.warning("No PDFs found for corpus analysis")
        return _empty_meta(pdf_dir)

    # Build first-page text map: filename → first page markdown
    first_page_map: dict[str, str] = {}
    for p in pages:
        fname = p["filename"]
        if fname not in first_page_map:
            first_page_map[fname] = p["markdown"][:800]  # first 800 chars is enough

    # Build the user prompt
    doc_list_lines = []
    for pdf in pdfs:
        first_text = first_page_map.get(pdf.name, "")
        doc_list_lines.append(
            f"Filename: {pdf.name}\n"
            f"First-page excerpt:\n{first_text[:400]}\n"
            f"---"
        )

    existing_nodes = ", ".join(settings.ALLOWED_NODES[:10])
    existing_rels  = ", ".join(settings.ALLOWED_RELATIONSHIPS[:10])

    user = (
        f"Existing node types in schema: {existing_nodes} ...\n"
        f"Existing relationship types: {existing_rels} ...\n\n"
        f"Documents to analyse ({len(pdfs)} total):\n\n"
        + "\n".join(doc_list_lines)
    )

    result = call_llm_json(_SYSTEM, user)

    if not result or not isinstance(result, dict):
        log.warning("Stage 0: LLM analysis failed — using empty defaults")
        return _empty_meta(pdf_dir)

    # Build the doc_modules map that Stage 3 needs
    doc_modules: dict[str, str] = {}
    documents = result.get("documents", {})
    for fname, info in documents.items():
        doc_modules[fname] = info.get("module_abbrev") or "Unknown"

    corpus_meta = {
        "corpus_type":              result.get("corpus_type", "mixed"),
        "documents":                documents,
        "doc_modules":              doc_modules,
        "extra_node_types":         result.get("extra_node_types", []),
        "extra_relationship_types": result.get("extra_relationship_types", []),
        "notes":                    result.get("notes", ""),
    }

    log.info("  Corpus type: %s", corpus_meta["corpus_type"])
    log.info("  Documents analysed: %d", len(documents))
    for fname, info in documents.items():
        log.info(
            "    %-50s → %-6s  %-12s  %s",
            fname[:50],
            info.get("doc_type", "?"),
            info.get("module_abbrev", "?"),
            info.get("release") or "",
        )
    if corpus_meta["extra_node_types"]:
        log.info("  Suggested extra node types: %s", corpus_meta["extra_node_types"])
    if corpus_meta["notes"]:
        log.info("  Notes: %s", corpus_meta["notes"])

    log.info("Stage 0 complete")
    return corpus_meta


def _empty_meta(pdf_dir: Path) -> dict:
    pdfs = sorted(pdf_dir.glob("**/*.pdf"))
    return {
        "corpus_type":              "mixed",
        "documents":                {p.name: {
            "doc_type": "OTHER",
            "module_abbrev": "Unknown",
            "module_full": p.stem,
            "release": None,
            "duplicate_of": None,
        } for p in pdfs},
        "doc_modules":              {p.name: "Unknown" for p in pdfs},
        "extra_node_types":         [],
        "extra_relationship_types": [],
        "notes":                    "",
    }