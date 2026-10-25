import threading

import numpy as np

from ..config import DEFAULT_EMBED_MODEL, MODEL_DIR

EMBED_MODELS: list[dict[str, object]] = [
    {
        "id": DEFAULT_EMBED_MODEL,
        "label": "BGE small v1.5",
        "dims": 384,
        "max_tokens": 512,
        "hint": "Handles 500-token chunks. Recommended.",
    },
    {
        "id": "sentence-transformers/all-MiniLM-L6-v2",
        "label": "all-MiniLM-L6-v2",
        "dims": 384,
        "max_tokens": 256,
        "hint": "Faster, but tuned for short text. Quality drops above about 256 tokens.",
    },
    {
        "id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        "label": "Multilingual MiniLM",
        "dims": 384,
        "max_tokens": 128,
        "hint": "Reads about 50 languages and can match across them, but it is weaker than the default on English text. "
        "Use it only for mixed-language collections. It reads 128 tokens at a time, so keep detail passages on.",
    },
]

_QUERY_PREFIX = {DEFAULT_EMBED_MODEL: "Represent this sentence for searching relevant passages: "}
_models: dict[str, object] = {}
_lock = threading.Lock()


def _load(name: str):
    with _lock:
        if name not in _models:
            from fastembed import TextEmbedding

            _models[name] = TextEmbedding(model_name=name, cache_dir=str(MODEL_DIR))
        return _models[name]


def embed(name: str, texts: list[str], batch_size: int = 32) -> np.ndarray:
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = np.array(list(_load(name).embed(texts, batch_size=batch_size)), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def embed_query(name: str, query: str) -> np.ndarray:
    return embed(name, [_QUERY_PREFIX.get(name, "") + query])[0]


def limit_for(name: str) -> int:
    return next((int(model["max_tokens"]) for model in EMBED_MODELS if model["id"] == name), 512)
