"""
utils/neo4j_client.py
=====================
Neo4j connection management and helper methods.
Uses MERGE everywhere (not CREATE) so re-runs are idempotent.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

from neo4j import GraphDatabase, Driver, Session
from neo4j.exceptions import ServiceUnavailable

from config import settings
from utils.logger import get_logger

log = get_logger("neo4j")

# Suppress verbose schema-notification INFO spam (expected on re-run)
logging.getLogger("neo4j.notifications").setLevel(logging.WARNING)


class Neo4jClient:

    def __init__(
        self,
        uri:      str = settings.NEO4J_URI,
        user:     str = settings.NEO4J_USER,
        password: str = settings.NEO4J_PASSWORD,
    ) -> None:
        self._driver: Driver = GraphDatabase.driver(
            uri, auth=(user, password)
        )
        self._verify()

    def close(self) -> None:
        self._driver.close()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        with self._driver.session() as s:
            yield s

    # ------------------------------------------------------------------
    # Schema setup — call once before ingestion
    # ------------------------------------------------------------------

    def create_constraints_and_indexes(self) -> None:
        """
        Create uniqueness constraints and indexes.
        Constraints implicitly create indexes on the constrained property.
        """
        constraints = [
            # Node uniqueness
            ("Document",        "id"),
            ("Chunk",           "id"),
            ("Requirement",     "id"),
            ("ConfigParameter", "id"),
            ("Module",          "name"),
            ("Entity",          "id"),
            ("DocumentRef",     "id"),
            ("Concept",         "id"),      # Concept nodes now have unique IDs
        ]
        with self.session() as s:
            for label, prop in constraints:
                try:
                    s.run(
                        f"CREATE CONSTRAINT {label.lower()}_{prop}_unique "
                        f"IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
                    )
                    log.debug("Constraint: %s.%s", label, prop)
                except Exception as exc:
                    log.warning("Constraint %s.%s: %s", label, prop, exc)

        # ── Provenance indexes (support ASEI Evolution Agent queries) ─────────
        # Index on ingested_at allows efficient time-range queries over all nodes.
        # Index on confidence_score allows filtering low-confidence nodes for review.
        # Index on source_chunk_id allows reverse-lookup: chunk → which nodes it produced.
        provenance_indexes = [
            ("Requirement",     "ingested_at"),
            ("Requirement",     "confidence_score"),
            ("Requirement",     "source_chunk_id"),
            ("Entity",          "ingested_at"),
            ("Entity",          "confidence_score"),
            ("Concept",         "ingested_at"),
        ]
        with self.session() as s:
            for label, prop in provenance_indexes:
                idx_name = f"{label.lower()}_{prop}_idx"
                try:
                    s.run(
                        f"CREATE INDEX {idx_name} IF NOT EXISTS "
                        f"FOR (n:{label}) ON (n.{prop})"
                    )
                    log.debug("Index: %s.%s", label, prop)
                except Exception as exc:
                    log.warning("Index %s.%s: %s", label, prop, exc)

        log.info("Neo4j constraints and indexes ready")

    def create_vector_index(self) -> None:
        """Create vector index on Chunk.embedding (call after all chunks written)."""
        cypher = """
        CREATE VECTOR INDEX chunk_embedding_index IF NOT EXISTS
        FOR (c:Chunk) ON (c.embedding)
        OPTIONS {indexConfig: {
          `vector.dimensions`: $dim,
          `vector.similarity_function`: 'cosine'
        }}
        """
        with self.session() as s:
            s.run(cypher, dim=settings.EMBED_DIM)
        log.info("Vector index on Chunk.embedding created (dim=%d)", settings.EMBED_DIM)

    # ------------------------------------------------------------------
    # Batch write helpers
    # ------------------------------------------------------------------

    def run_batch(self, cypher: str, rows: list[dict]) -> int:
        """
        Execute a parameterized Cypher statement for each row in rows.
        Splits into batches of settings.NEO4J_BATCH_SIZE.
        Returns total rows written.
        """
        total = 0
        batch_size = settings.NEO4J_BATCH_SIZE
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            with self.session() as s:
                s.run(cypher, rows=batch)
            total += len(batch)
        return total

    def run(self, cypher: str, **params) -> list[dict]:
        with self.session() as s:
            result = s.run(cypher, **params)
            return [dict(r) for r in result]

    # ------------------------------------------------------------------
    # Node counts (for progress reporting)
    # ------------------------------------------------------------------

    def node_count(self, label: str) -> int:
        result = self.run(f"MATCH (n:{label}) RETURN count(n) AS n")
        return result[0]["n"] if result else 0

    def relationship_count(self, rel_type: str | None = None) -> int:
        if rel_type:
            result = self.run(
                f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS n"
            )
        else:
            result = self.run("MATCH ()-[r]->() RETURN count(r) AS n")
        return result[0]["n"] if result else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _verify(self) -> None:
        try:
            self._driver.verify_connectivity()
            log.info("Neo4j connected: %s", settings.NEO4J_URI)
        except ServiceUnavailable as exc:
            raise RuntimeError(
                f"Cannot connect to Neo4j at {settings.NEO4J_URI}. "
                "Is the Docker container running?"
            ) from exc