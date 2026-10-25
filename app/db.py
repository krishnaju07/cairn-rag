import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import DB_PATH

_local = threading.local()
_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init() -> None:
    connect().executescript(_SCHEMA)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def one(sql: str, params: tuple = ()) -> Optional[dict]:
    row = connect().execute(sql, params).fetchone()
    return dict(row) if row else None


def many(sql: str, params: tuple = ()) -> list:
    return [dict(row) for row in connect().execute(sql, params).fetchall()]


def run(sql: str, params: tuple = ()) -> None:
    connect().execute(sql, params)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: Optional[str], default: Any = None) -> Any:
    return json.loads(value) if value else default
