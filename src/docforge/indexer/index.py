"""Orchestrates chunk → embed → Chroma. Single entry point: build_index()."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chromadb
from chromadb.config import Settings

from .chunk import Chunk, chunk_file
from .embed import embed_query, embed_texts


@dataclass
class IndexStats:
    chunks: int
    files_chunked: int
    files_skipped: int


class CodeIndex:
    """Thin wrapper over a Chroma collection. One collection per repo."""

    def __init__(self, persist_dir: Path, collection_name: str):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        self._col = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def count(self) -> int:
        return self._col.count()

    def add_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = embed_texts(_embedding_text(c) for c in chunks)
        self._col.upsert(
            ids=[c.id for c in chunks],
            embeddings=vectors,
            documents=[c.content for c in chunks],
            metadatas=[
                {
                    "file": c.file,
                    "line_start": c.line_start,
                    "line_end": c.line_end,
                    "lang": c.lang,
                    "kind": c.kind,
                    "name": c.name or "",
                }
                for c in chunks
            ],
        )

    def query(self, text: str, k: int = 8, where: dict | None = None) -> list[dict]:
        """Return [{file, line_start, line_end, lang, kind, name, content, score}, ...]"""
        vec = embed_query(text)
        results = self._col.query(
            query_embeddings=[vec],
            n_results=k,
            where=where,
        )
        out: list[dict] = []
        for doc, meta, dist in zip(
            results["documents"][0], results["metadatas"][0], results["distances"][0]
        ):
            out.append({**meta, "content": doc, "score": 1 - dist})
        return out

    def reset(self) -> None:
        # Drop and recreate the collection — cheaper than wiping the whole client.
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._col = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )


def _embedding_text(c: Chunk) -> str:
    """What we feed the embedder. Including file+symbol gives retrieval a hand."""
    header = f"{c.file}"
    if c.name:
        header += f" :: {c.name}"
    header += f" ({c.kind}, lines {c.line_start}-{c.line_end})"
    # Keep the chunk modest — bge-small caps around 512 tokens.
    body = c.content if len(c.content) < 6000 else c.content[:6000]
    return f"{header}\n{body}"


def build_index(
    repo_root: Path,
    files: Iterable[Path],
    repo_name: str,
    persist_dir: Path,
    batch_size: int = 64,
    reset: bool = True,
) -> tuple[CodeIndex, IndexStats]:
    """Chunk + embed + upsert all files. Returns the index plus simple stats."""
    index = CodeIndex(persist_dir=persist_dir, collection_name=repo_name)
    if reset:
        index.reset()

    buffer: list[Chunk] = []
    files_chunked = 0
    files_skipped = 0
    total_chunks = 0

    for path in files:
        chunks = chunk_file(path, repo_root, repo_name)
        if not chunks:
            files_skipped += 1
            continue
        files_chunked += 1
        total_chunks += len(chunks)
        buffer.extend(chunks)
        if len(buffer) >= batch_size:
            index.add_chunks(buffer)
            buffer.clear()

    if buffer:
        index.add_chunks(buffer)

    return index, IndexStats(
        chunks=total_chunks, files_chunked=files_chunked, files_skipped=files_skipped
    )
