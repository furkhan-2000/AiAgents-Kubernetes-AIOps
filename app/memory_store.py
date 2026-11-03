"""SQLite + sqlite-vec vector store for RAG memory. One file on disk, no server.
Kept separate from memory.py so the logic is testable without the native
extension. All methods may raise; RagMemory wraps them fail-open."""

import sqlite3
import struct


def _serialize(vec) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class SqliteVecStore:
    """Cosine KNN over float[dim] vectors. Stores (embedding, question, tools)."""

    def __init__(self, path: str, dim: int = 768):
        import sqlite_vec  # imported here so the app still starts if RAG is off
        self._dim = dim
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.enable_load_extension(True)
        sqlite_vec.load(self._db)
        self._db.enable_load_extension(False)
        self._db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS memory USING vec0("
            f"  embedding float[{dim}] distance_metric=cosine,"
            f"  +question text,"
            f"  +tools text)"
        )
        self._db.commit()

    def add(self, vec, question: str, tools: str) -> None:
        self._db.execute(
            "INSERT INTO memory(embedding, question, tools) VALUES (?, ?, ?)",
            (_serialize(vec), question, tools),
        )
        self._db.commit()

    def search(self, vec, k: int):
        # sqlite-vec returns cosine DISTANCE (0 = identical). similarity = 1 - distance.
        rows = self._db.execute(
            "SELECT tools, distance FROM memory "
            "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
            (_serialize(vec), k),
        ).fetchall()
        return [(tools, 1.0 - float(dist)) for tools, dist in rows]

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM memory").fetchone()[0]
