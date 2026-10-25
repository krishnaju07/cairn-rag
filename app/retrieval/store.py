import sqlite3
from typing import Optional

import numpy as np

from .. import db


def insert_row(
    conn: sqlite3.Connection,
    collection_id: str,
    document_id: str,
    row: dict[str, object],
    vector: Optional[np.ndarray],
) -> int:
    cursor = conn.execute(
        """INSERT INTO chunks (document_id, collection_id, kind, parent_id, idx, text, tokens,
                               start_char, end_char, page_start, page_end, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            document_id,
            collection_id,
            row.get("kind", "chunk"),
            row.get("parent_id"),
            row["idx"],
            row["text"],
            row["tokens"],
            row["start_char"],
            row["end_char"],
            row["page_start"],
            row["page_end"],
            None if vector is None else vector.astype(np.float32).tobytes(),
        ),
    )
    chunk_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO chunks_fts (rowid, text, collection_id) VALUES (?, ?, ?)",
        (chunk_id, row["text"], collection_id),
    )
    return chunk_id


def save_pages(
    conn: sqlite3.Connection,
    document_id: str,
    pages: list[str],
    starts: list[int],
    layouts: Optional[list[dict]] = None,
    kind: str = "",
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO pages (document_id, page, start_char, text) VALUES (?, ?, ?, ?)",
        [(document_id, number, start, text) for number, (start, text) in enumerate(zip(starts, pages), start=1)],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO page_layout (document_id, page, kind, data) VALUES (?, ?, ?, ?)",
        [(document_id, number, kind, db.dumps(layout)) for number, layout in enumerate(layouts or [], start=1)],
    )


def clear_document(conn: sqlite3.Connection, document_id: str) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)", (document_id,))
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
    conn.execute("DELETE FROM page_layout WHERE document_id = ?", (document_id,))


def delete_document(document_id: str) -> None:
    with db.transaction() as conn:
        clear_document(conn, document_id)
        conn.execute("DELETE FROM doc_steps WHERE document_id = ?", (document_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def delete_collection_rows(conn: sqlite3.Connection, collection_id: str) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE collection_id = ?", (collection_id,))
    conn.execute("DELETE FROM chunks WHERE collection_id = ?", (collection_id,))
