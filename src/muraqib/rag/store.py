"""Vector stores.

``memory`` is the default: fast, hermetic, needs nothing installed.
``chroma`` persists to disk for larger corpora.
``pgvector`` targets Postgres for a shared/team deployment.

All three implement the same tiny interface, so the retriever is unaware of
which one is in play - that is what lets the same assessment run on a laptop
and in a sovereign-cloud Postgres without code changes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from .embeddings import cosine

log = logging.getLogger("muraqib.rag")


@dataclass(slots=True)
class StoredDoc:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    vector: list[float] = field(default_factory=list)


class VectorStore(Protocol):
    backend: str

    def upsert(self, docs: list[StoredDoc]) -> None: ...
    def query(self, vector: list[float], top_k: int) -> list[tuple[StoredDoc, float]]: ...
    def count(self) -> int: ...


class InMemoryVectorStore:
    backend = "memory"

    def __init__(self) -> None:
        self._docs: dict[str, StoredDoc] = {}

    def upsert(self, docs: list[StoredDoc]) -> None:
        for d in docs:
            self._docs[d.id] = d

    def query(self, vector: list[float], top_k: int) -> list[tuple[StoredDoc, float]]:
        scored = [(d, cosine(vector, d.vector)) for d in self._docs.values() if d.vector]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return len(self._docs)

    def all(self) -> list[StoredDoc]:
        return list(self._docs.values())


class ChromaVectorStore:
    backend = "chroma"

    def __init__(self, path: str, collection: str = "muraqib_controls"):
        import chromadb  # noqa: PLC0415

        self._client = chromadb.PersistentClient(path=path)
        self._c = self._client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}
        )
        self._cache: dict[str, StoredDoc] = {}

    def upsert(self, docs: list[StoredDoc]) -> None:
        if not docs:
            return
        self._c.upsert(
            ids=[d.id for d in docs],
            documents=[d.text for d in docs],
            embeddings=[d.vector for d in docs],
            metadatas=[{k: str(v) for k, v in d.metadata.items()} for d in docs],
        )
        for d in docs:
            self._cache[d.id] = d

    def query(self, vector: list[float], top_k: int) -> list[tuple[StoredDoc, float]]:
        res = self._c.query(query_embeddings=[vector], n_results=top_k)
        out: list[tuple[StoredDoc, float]] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids):
            out.append(
                (
                    StoredDoc(id=doc_id, text=docs[i], metadata=dict(metas[i] or {})),
                    1.0 - float(dists[i]),  # cosine distance -> similarity
                )
            )
        return out

    def count(self) -> int:
        return int(self._c.count())


class PgVectorStore:
    """Postgres + pgvector. Team/shared deployment target.

    Table names cannot be parameterised in SQL, so they are handled two ways:
    validated against a strict identifier pattern at construction, and composed
    with ``psycopg.sql.Identifier`` so the driver quotes them. Values are always
    passed as bound parameters, never interpolated.
    """

    backend = "pgvector"

    _IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

    def __init__(self, dsn: str, dimension: int, table: str = "muraqib_controls"):
        import psycopg  # noqa: PLC0415
        from psycopg import sql  # noqa: PLC0415

        if not self._IDENT.match(table):
            raise ValueError(f"invalid table name {table!r}: must match {self._IDENT.pattern}")
        if not isinstance(dimension, int) or not 1 <= dimension <= 16000:
            raise ValueError(f"invalid embedding dimension: {dimension!r}")

        self._psycopg = psycopg
        self._sql = sql
        self._dsn = dsn
        self._table = sql.Identifier(table)
        self._table_name = table
        self._dim = dimension

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {table} ("
                    "id text PRIMARY KEY, "
                    "text text NOT NULL, "
                    "metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb, "
                    "embedding vector({dim}))"
                ).format(table=self._table, dim=sql.Literal(dimension))
            )
            cur.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS {idx} ON {table} "
                    "USING hnsw (embedding vector_cosine_ops)"
                ).format(idx=sql.Identifier(f"{table}_embedding_hnsw"), table=self._table)
            )
            conn.commit()

    def upsert(self, docs: list[StoredDoc]) -> None:
        import json  # noqa: PLC0415

        sql = self._sql
        statement = sql.SQL(
            "INSERT INTO {table} (id, text, metadata, embedding) "
            "VALUES (%s, %s, %s::jsonb, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "text = EXCLUDED.text, metadata = EXCLUDED.metadata, embedding = EXCLUDED.embedding"
        ).format(table=self._table)
        with self._psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.executemany(
                statement,
                [(d.id, d.text, json.dumps(d.metadata), str(d.vector)) for d in docs],
            )
            conn.commit()

    def query(self, vector: list[float], top_k: int) -> list[tuple[StoredDoc, float]]:
        sql = self._sql
        statement = sql.SQL(
            "SELECT id, text, metadata, 1 - (embedding <=> %s::vector) AS score "
            "FROM {table} ORDER BY embedding <=> %s::vector LIMIT %s"
        ).format(table=self._table)
        with self._psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(statement, (str(vector), str(vector), int(top_k)))
            return [
                (StoredDoc(id=r[0], text=r[1], metadata=r[2] or {}), float(r[3]))
                for r in cur.fetchall()
            ]

    def count(self) -> int:
        statement = self._sql.SQL("SELECT count(*) FROM {table}").format(table=self._table)
        with self._psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
            cur.execute(statement)
            return int(cur.fetchone()[0])


def get_vector_store(
    backend: str, *, path: str = "", dsn: str = "", dimension: int = 512
) -> VectorStore:
    if backend == "memory":
        return InMemoryVectorStore()
    if backend == "chroma":
        try:
            return ChromaVectorStore(path=path or ".chroma")
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "chroma unavailable (%s); falling back to in-memory store", type(exc).__name__
            )
            return InMemoryVectorStore()
    if backend == "pgvector":
        return PgVectorStore(dsn=dsn, dimension=dimension)
    raise ValueError(f"unknown vector backend: {backend}")
