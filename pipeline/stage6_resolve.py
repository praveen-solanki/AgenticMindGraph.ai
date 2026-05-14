"""
pipeline/stage6_resolve.py
==========================
Stage 6: Entity resolution.

Three-tier approach:
  Tier 1 — Manual overrides       (settings.CANONICAL_NAME_OVERRIDES)
  Tier 2 — BGE-M3 cosine clustering  (for Concept/Entity nodes only)
  Tier 3 — Requirement IDs          (exact match, no resolution needed)

Requirement IDs ([SWS_X_NNNNN]) are treated as primary keys — never
merged, never renamed.

Module names are resolved using the manual overrides dictionary, which
covers all ~80 AUTOSAR modules and their common variant spellings.

Output:
  Returns the same {"nodes": [...], "relationships": [...]} dict with:
  - Duplicate nodes merged into canonical nodes
  - All relationships updated to point to canonical node IDs
  - Each node gets an "aliases" property listing resolved variants
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict

from utils.logger import get_logger
from utils.llm_client import call_llm_json
from config import settings

log = get_logger("stage6")


def run(entity_data: dict) -> dict:
    """
    Resolve entity nodes and update all relationship references.

    Args:
        entity_data: {"nodes": [...], "relationships": [...]}

    Returns:
        Resolved {"nodes": [...], "relationships": [...]}
    """
    nodes         = entity_data["nodes"]
    relationships = entity_data["relationships"]

    log.info("Stage 6: entity resolution on %d nodes", len(nodes))

    # Separate nodes by label — different resolution strategies per label
    req_nodes:    list[dict] = []
    module_nodes: list[dict] = []
    param_nodes:  list[dict] = []
    docref_nodes: list[dict] = []
    other_nodes:  list[dict] = []

    # BUG FIX: normalise label casing before routing so "Functionalcluster",
    # "Changerecord", etc. land in the correct bucket (other_nodes) with their
    # canonical label, not as broken variants.
    # We reuse the same _LABEL_ALIASES dict pattern from stage8; import inline
    # to avoid a circular import.
    _S8_ALIASES: dict[str, str] = {
        "documentref": "DocumentRef", "document_ref": "DocumentRef",
        "configparameter": "ConfigParameter", "config_parameter": "ConfigParameter",
        "standardref": "StandardRef", "standard_ref": "StandardRef",
        "functionalcluster": "FunctionalCluster", "functional_cluster": "FunctionalCluster",
        "changerecord": "ChangeRecord", "change_record": "ChangeRecord",
        "specificationitem": "SpecificationItem", "specification_item": "SpecificationItem",
        "securityfeature": "SecurityFeature", "security_feature": "SecurityFeature",
        "datatype": "DataType", "data_type": "DataType",
        "testspecification": "TestSpecification", "testcase": "TestCase",
        "organisation": "Organization",
    }

    for node in nodes:
        raw_label = node.get("label", "Entity")
        label = _S8_ALIASES.get(raw_label.lower(), raw_label)
        node = dict(node, label=label)   # write canonical label back

        if label == "Requirement":
            req_nodes.append(node)
        elif label == "Module":
            module_nodes.append(node)
        elif label == "ConfigParameter":
            param_nodes.append(node)
        elif label == "DocumentRef":
            docref_nodes.append(node)
        else:
            other_nodes.append(node)

    # ── Tier 1: Manual overrides for Module + other nodes ─────────────────────
    module_nodes, module_remap = _apply_manual_overrides(module_nodes)
    log.info("  Tier 1: %d module remaps from manual overrides", len(module_remap))

    # ── Tier 1 also on other_nodes (Concept, Entity, StandardRef) ────────────
    other_nodes, other_remap_t1 = _apply_manual_overrides(other_nodes)

    # ── Tier 2: BGE-M3 clustering for Concept/Entity/StandardRef nodes ───────
    cluster_remap: dict[str, str] = {}
    other_nodes, cluster_remap = _cluster_entities(other_nodes)
    log.info("  Tier 2: %d entity remaps from BGE-M3 clustering", len(cluster_remap))

    # ── Tier 2b: LLM resolution for uncertain-zone clusters (0.75–0.92) ──────
    other_nodes, uncertain_remap = _llm_resolve_uncertain_clusters(other_nodes)
    log.info("  Tier 2b: %d entity remaps from LLM uncertain-zone resolution", len(uncertain_remap))
    cluster_remap.update(uncertain_remap)

    # ── Tier 2c: Prefix stripping validation ──────────────────────────────────
    other_nodes, prefix_remap = _llm_resolve_prefixed_names(other_nodes)
    log.info("  Tier 2c: %d entity remaps from prefix stripping", len(prefix_remap))
    cluster_remap.update(prefix_remap)

    # ── Tier 3: Requirement IDs — no resolution ───────────────────────────────
    # (already unique by construction in Stage 5)

    # ── Build the complete old_id → new_id remap ──────────────────────────────
    full_remap: dict[str, str] = {}
    full_remap.update(module_remap)
    full_remap.update(other_remap_t1)
    full_remap.update(cluster_remap)

    # ── Update all relationship references ────────────────────────────────────
    updated_rels = _remap_relationships(relationships, full_remap)

    # ── Reassemble final node list ────────────────────────────────────────────
    final_nodes = req_nodes + module_nodes + param_nodes + docref_nodes + other_nodes

    # Deduplicate one final time
    seen: set[str] = set()
    deduped: list[dict] = []
    for node in final_nodes:
        nid = node["node_id"]
        if nid not in seen:
            seen.add(nid)
            deduped.append(node)

    log.info(
        "Stage 6 complete: %d nodes (was %d), %d relationships",
        len(deduped), len(nodes), len(updated_rels),
    )

    return {"nodes": deduped, "relationships": updated_rels}


# ══════════════════════════════════════════════════════════════════════════════
# TIER 1 — Manual overrides
# ══════════════════════════════════════════════════════════════════════════════

def _apply_manual_overrides(nodes: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """
    Apply settings.CANONICAL_NAME_OVERRIDES.
    Returns (merged_nodes, remap_dict).
    """
    overrides = settings.CANONICAL_NAME_OVERRIDES   # lowercased key → canonical

    # Group nodes: those that match an override → merge into canonical
    canonical_nodes: dict[str, dict] = {}   # canonical_name → node
    remap: dict[str, str] = {}              # old_node_id → canonical_node_id

    for node in nodes:
        raw_name  = str(node["properties"].get("name", "")).strip()
        lower     = raw_name.lower()
        canonical = overrides.get(lower, raw_name)   # keep original if no override

        label = node.get("label", "Entity")
        safe  = re.sub(r"\s+", "_", canonical.lower())
        safe  = re.sub(r"[^a-z0-9_]", "", safe)[:60]
        canonical_id = f"{label.lower()}_{safe}"

        old_id = node["node_id"]

        if canonical_id not in canonical_nodes:
            # First occurrence — create canonical node
            canonical_node = dict(node)
            canonical_node["node_id"] = canonical_id
            canonical_node["properties"] = dict(node["properties"])
            canonical_node["properties"]["name"]    = canonical
            canonical_node["properties"]["aliases"] = [raw_name]
            canonical_nodes[canonical_id] = canonical_node
        else:
            # Merge — add alias
            existing_aliases = canonical_nodes[canonical_id]["properties"].get("aliases", [])
            if raw_name not in existing_aliases:
                existing_aliases.append(raw_name)
            canonical_nodes[canonical_id]["properties"]["aliases"] = existing_aliases

        if old_id != canonical_id:
            remap[old_id] = canonical_id

    return list(canonical_nodes.values()), remap


# ══════════════════════════════════════════════════════════════════════════════
# TIER 2 — BGE-M3 cosine clustering
# ══════════════════════════════════════════════════════════════════════════════

# Antonym word pairs that should NEVER be merged even at high cosine similarity.
# Embedding models conflate these because the surrounding tokens are similar.
_ANTONYM_PAIRS: set[frozenset] = {
    frozenset({"encryption", "decryption"}),
    frozenset({"encrypt", "decrypt"}),
    frozenset({"symmetrical encryption", "symmetrical decryption"}),
    frozenset({"symmetric encryption", "symmetric decryption"}),
    frozenset({"asymmetric encryption", "asymmetric decryption"}),
    frozenset({"synchronous", "asynchronous"}),
    frozenset({"synchronous job processing", "asynchronous job processing"}),
    frozenset({"http", "https"}),
    frozenset({"sender", "receiver"}),
    frozenset({"client", "server"}),
    frozenset({"input", "output"}),
    frozenset({"read", "write"}),
    frozenset({"request", "response"}),
    frozenset({"start", "stop"}),
    frozenset({"activate", "deactivate"}),
    frozenset({"enable", "disable"}),
}


def _is_antonym_pair(name_a: str, name_b: str) -> bool:
    """Return True if the two names are a known antonym pair that must not be merged."""
    a = name_a.lower().strip()
    b = name_b.lower().strip()
    if a == b:
        return False
    pair = frozenset({a, b})
    if pair in _ANTONYM_PAIRS:
        return True
    # Also check if any antonym set is a subset of either name
    for ap in _ANTONYM_PAIRS:
        words = list(ap)
        if len(words) == 2:
            if words[0] in a and words[1] in b:
                return True
            if words[1] in a and words[0] in b:
                return True
    return False


def _cluster_entities(nodes: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """
    Embed all entity names with BGE-M3, cluster by cosine similarity,
    pick the most frequent name as canonical per cluster.

    BUG FIX (over-merge): old code clustered all "other" nodes together
    regardless of label. That allowed e.g. a Concept node and a Function node
    with similar names to be merged, and let antonym pairs like
    "Symmetrical Encryption" / "Symmetrical Decryption" be merged because
    their embeddings are very close.

    Fix 1: cluster within the same label only.
    Fix 2: skip union if either member is an antonym of the other.

    Returns (canonical_nodes, remap_dict).
    """
    if len(nodes) < 2:
        return nodes, {}

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        log.warning("sentence-transformers not installed — skipping Tier 2 clustering")
        return nodes, {}

    names = [str(n["properties"].get("name", n["node_id"])) for n in nodes]

    log.info("  Tier 2: embedding %d entity names ...", len(names))
    model       = SentenceTransformer(settings.EMBED_MODEL)
    embeddings  = model.encode(
        names,
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=False,
    )

    # Build clusters using Union-Find
    parent = list(range(len(nodes)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    threshold = settings.ENTITY_RESOLUTION_THRESHOLD
    sim_matrix = embeddings @ embeddings.T   # cosine similarity (normalized)

    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            if sim_matrix[i, j] < threshold:
                continue
            # BUG FIX 1: never merge nodes of different labels
            if nodes[i].get("label") != nodes[j].get("label"):
                continue
            # BUG FIX 2: never merge antonym pairs
            if _is_antonym_pair(names[i], names[j]):
                continue
            union(i, j)

    # Group by cluster root
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(nodes)):
        clusters[find(i)].append(i)

    remap: dict[str, str] = {}
    canonical_nodes: list[dict] = []

    for root, members in clusters.items():
        if len(members) == 1:
            canonical_nodes.append(nodes[members[0]])
            continue

        # Pick canonical = LLM-selected name (or most frequent if LLM fails)
        member_names = [names[i] for i in members]
        canonical_name = _llm_pick_canonical_name(member_names) or max(set(member_names), key=member_names.count)
        label = nodes[members[0]].get("label", "Entity")
        safe  = re.sub(r"\s+", "_", canonical_name.lower())
        safe  = re.sub(r"[^a-z0-9_]", "", safe)[:60]
        canonical_id = f"{label.lower()}_{safe}"

        # Merge properties from all members
        merged_props = {}
        all_aliases: list[str] = []
        for idx in members:
            merged_props.update(nodes[idx]["properties"])
            n = names[idx]
            if n not in all_aliases:
                all_aliases.append(n)

        merged_props["name"]    = canonical_name
        merged_props["aliases"] = all_aliases

        canonical_nodes.append({
            "node_id":    canonical_id,
            "label":      label,
            "properties": merged_props,
        })

        # Build remap for all non-canonical members
        for idx in members:
            old_id = nodes[idx]["node_id"]
            if old_id != canonical_id:
                remap[old_id] = canonical_id

    return canonical_nodes, remap


# ══════════════════════════════════════════════════════════════════════════════
# Relationship remapping
# ══════════════════════════════════════════════════════════════════════════════

def _remap_relationships(
    relationships: list[dict],
    remap: dict[str, str],
) -> list[dict]:
    """Update from_id / to_id in all relationships according to remap dict."""
    updated = []
    for rel in relationships:
        new_rel = dict(rel)
        new_rel["from_id"] = remap.get(rel["from_id"], rel["from_id"])
        new_rel["to_id"]   = remap.get(rel["to_id"],   rel["to_id"])

        # Drop self-loops created by merging
        if new_rel["from_id"] == new_rel["to_id"]:
            continue
        updated.append(new_rel)
    return updated


# ══════════════════════════════════════════════════════════════════════════════
# LLM helpers for resolution
# ══════════════════════════════════════════════════════════════════════════════

_UNCERTAIN_SYSTEM = """You are resolving AUTOSAR entity names.
Given a list of entity names, determine which ones refer to the same AUTOSAR entity.

Return ONLY a JSON object:
{
  "merge": true | false,
  "canonical": "<most precise and standard canonical name if merge=true, else null>"
}

Rules:
- Use official AUTOSAR abbreviations as canonical (ComM not 'Communication Manager', RTE not 'Runtime Environment')
- ISO 26262 is more canonical than ISO26262
- Only merge if they clearly refer to the same entity; when in doubt, return merge=false
- No markdown, no explanation"""

_PREFIX_SYSTEM = """You are resolving AUTOSAR entity names that may have redundant prefixes.
Given pairs of names like ('Concept_Job', 'Job') or ('Module_ComM', 'ComM'),
determine which pairs should be merged and what the canonical name should be.

Return ONLY a JSON array:
[{"name_a": "...", "name_b": "...", "merge": true|false, "canonical": "<name>"}, ...]

Rules:
- 'Concept_X' and 'X' for the same X → merge, canonical = 'X'
- 'Module_X' and 'X' for the same X → merge, canonical = 'X'
- 'Function_X' and 'X' for the same X → merge, canonical = 'X'
- 'System_X' and 'X' for the same X → merge, canonical = 'X'
- Only merge when the stripped name is clearly the same entity
- No markdown, no explanation"""

_CANONICAL_SYSTEM = """You are selecting the canonical name for an AUTOSAR entity.
Given a list of name variants that all refer to the same entity,
return the single most precise and standard canonical form used in AUTOSAR specifications.

Return ONLY a JSON string: "<canonical name>"
Examples: "ComM" not "communication manager"; "ISO 26262" not "ISO26262"; "RTE" not "Runtime Environment"
No markdown, no explanation."""


def _llm_pick_canonical_name(names: list[str]) -> str | None:
    """Ask LLM to pick the most canonical AUTOSAR name from a cluster."""
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    try:
        result = call_llm_json(
            system=_CANONICAL_SYSTEM,
            user=f"Name variants: {names}",
        )
        if isinstance(result, str) and result.strip():
            return result.strip()
    except Exception:
        pass
    return None


def _llm_resolve_uncertain_clusters(nodes: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """
    For entity name pairs with cosine similarity in the uncertain zone (0.75–0.92),
    ask the LLM whether they should be merged.
    """
    if len(nodes) < 2:
        return nodes, {}

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError:
        return nodes, {}

    low  = settings.ENTITY_RESOLUTION_UNCERTAIN_LOW
    high = settings.ENTITY_RESOLUTION_UNCERTAIN_HIGH

    names = [str(n["properties"].get("name", n["node_id"])) for n in nodes]
    model = SentenceTransformer(settings.EMBED_MODEL)
    embeddings = model.encode(names, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
    sim_matrix = embeddings @ embeddings.T

    # Find uncertain pairs — applying same guards as Tier 2 clustering
    uncertain_pairs: list[tuple[int, int]] = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            sim = float(sim_matrix[i, j])
            if low <= sim < high:
                # Never merge different labels or known antonyms
                if nodes[i].get("label") != nodes[j].get("label"):
                    continue
                if _is_antonym_pair(names[i], names[j]):
                    continue
                uncertain_pairs.append((i, j))

    if not uncertain_pairs:
        return nodes, {}

    log.info("  Tier 2b: resolving %d uncertain pairs with LLM ...", len(uncertain_pairs))

    # Process in batches of 10 pairs
    BATCH = 10
    merge_decisions: dict[tuple[int, int], bool] = {}
    canonical_decisions: dict[tuple[int, int], str] = {}

    for b in range(0, len(uncertain_pairs), BATCH):
        batch = uncertain_pairs[b:b + BATCH]
        pairs_data = [
            {"name_a": names[i], "name_b": names[j]}
            for i, j in batch
        ]
        try:
            result = call_llm_json(
                system=_UNCERTAIN_SYSTEM,
                user=f"Pairs to check:\n{pairs_data}",
            )
            if isinstance(result, list) and len(result) == len(batch):
                for (i, j), item in zip(batch, result):
                    if isinstance(item, dict):
                        merge_decisions[(i, j)] = bool(item.get("merge", False))
                        canonical_decisions[(i, j)] = item.get("canonical") or names[i]
            elif isinstance(result, dict) and len(batch) == 1:
                i, j = batch[0]
                merge_decisions[(i, j)] = bool(result.get("merge", False))
                canonical_decisions[(i, j)] = result.get("canonical") or names[i]
        except Exception:
            pass

    # Apply merges using union-find
    parent = list(range(len(nodes)))
    canonical_name_map: dict[int, str] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (i, j), should_merge in merge_decisions.items():
        if should_merge:
            root_i, root_j = find(i), find(j)
            if root_i != root_j:
                parent[root_i] = root_j
                canonical_name_map[root_j] = canonical_decisions.get((i, j), names[j])

    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(nodes)):
        clusters[find(i)].append(i)

    remap: dict[str, str] = {}
    canonical_nodes: list[dict] = []

    for root, members in clusters.items():
        if len(members) == 1:
            canonical_nodes.append(nodes[members[0]])
            continue

        canonical_name = canonical_name_map.get(root) or names[members[0]]
        label = nodes[members[0]].get("label", "Entity")
        safe  = re.sub(r"\s+", "_", canonical_name.lower())
        safe  = re.sub(r"[^a-z0-9_]", "", safe)[:60]
        canonical_id = f"{label.lower()}_{safe}"

        merged_props: dict = {}
        all_aliases: list[str] = []
        for idx in members:
            merged_props.update(nodes[idx]["properties"])
            n = names[idx]
            if n not in all_aliases:
                all_aliases.append(n)

        merged_props["name"]    = canonical_name
        merged_props["aliases"] = all_aliases
        canonical_nodes.append({"node_id": canonical_id, "label": label, "properties": merged_props})

        for idx in members:
            old_id = nodes[idx]["node_id"]
            if old_id != canonical_id:
                remap[old_id] = canonical_id

    return canonical_nodes, remap


_PREFIX_PATTERN = re.compile(
    r"^(Concept|Module|Function|System|Entity|Type|Service)_(.+)$",
    re.IGNORECASE,
)


def _llm_resolve_prefixed_names(nodes: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """
    Find nodes with names like Concept_Job and check if a plain 'Job' node exists.
    Ask LLM whether they should be merged.
    """
    prefixed: list[tuple[int, str, str]] = []   # (index, prefix, plain_name)
    name_to_indices: dict[str, list[int]] = defaultdict(list)

    for i, node in enumerate(nodes):
        name = str(node["properties"].get("name", ""))
        name_to_indices[name.lower()].append(i)
        m = _PREFIX_PATTERN.match(name)
        if m:
            prefixed.append((i, m.group(1), m.group(2)))

    if not prefixed:
        return nodes, {}

    # Find pairs where plain_name also exists
    pairs_to_check: list[tuple[int, int]] = []
    for idx, prefix, plain in prefixed:
        plain_lower = plain.lower()
        if plain_lower in name_to_indices:
            for plain_idx in name_to_indices[plain_lower]:
                if plain_idx != idx:
                    pairs_to_check.append((idx, plain_idx))

    if not pairs_to_check:
        return nodes, {}

    # Ask LLM in batches
    pairs_data = [
        {"name_a": nodes[i]["properties"].get("name", ""), "name_b": nodes[j]["properties"].get("name", "")}
        for i, j in pairs_to_check[:30]  # cap at 30
    ]

    remap: dict[str, str] = {}
    try:
        result = call_llm_json(system=_PREFIX_SYSTEM, user=str(pairs_data))
        if result and isinstance(result, list):
            for (i, j), item in zip(pairs_to_check, result):
                if isinstance(item, dict) and item.get("merge"):
                    canonical = item.get("canonical", nodes[j]["properties"].get("name", ""))
                    label = nodes[j].get("label", "Entity")
                    safe  = re.sub(r"\s+", "_", canonical.lower())
                    safe  = re.sub(r"[^a-z0-9_]", "", safe)[:60]
                    canonical_id = f"{label.lower()}_{safe}"
                    # Remap the prefixed node to the plain node
                    old_id = nodes[i]["node_id"]
                    if old_id != canonical_id:
                        remap[old_id] = canonical_id
                        # Update canonical node's name if needed
                        nodes[j]["properties"]["name"] = canonical
                        nodes[j]["node_id"] = canonical_id
    except Exception:
        pass

    # Remove nodes that were remapped away
    remapped_ids = set(remap.keys())
    remaining_nodes = [n for n in nodes if n["node_id"] not in remapped_ids]
    return remaining_nodes, remap
