import hashlib
import secrets
from typing import Any, Optional

from . import db, library

SECRET_PREFIX = "ck_"
PREFIX_LENGTH = len(SECRET_PREFIX) + 6


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create(collection_id: str, name: str) -> dict[str, Any]:
    library.get_collection(collection_id)
    secret = SECRET_PREFIX + secrets.token_urlsafe(32)
    key_id = db.new_id("key")
    db.run(
        "INSERT INTO api_keys (id, collection_id, name, prefix, key_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (key_id, collection_id, name.strip() or "Untitled key", secret[:PREFIX_LENGTH], _hash(secret), db.now()),
    )
    return {**db.one("SELECT * FROM api_keys WHERE id = ?", (key_id,)), "secret": secret}


def list_keys(collection_id: Optional[str] = None) -> list[dict[str, Any]]:
    sql = "SELECT k.*, c.name AS collection_name FROM api_keys k JOIN collections c ON c.id = k.collection_id"
    params: tuple = ()
    if collection_id:
        sql, params = sql + " WHERE k.collection_id = ?", (collection_id,)
    return db.many(sql + " ORDER BY k.created_at DESC", params)


def revoke(key_id: str) -> None:
    db.run("UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (db.now(), key_id))


def authenticate(secret: str) -> Optional[dict[str, Any]]:
    if not secret.startswith(SECRET_PREFIX):
        return None
    row = db.one("SELECT * FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL", (_hash(secret),))
    if row:
        db.run("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (db.now(), row["id"]))
    return row
