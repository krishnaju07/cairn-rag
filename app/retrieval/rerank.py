import math
import threading

from ..config import MODEL_DIR, RERANK_MODEL

_encoder = None
_lock = threading.Lock()


def _load():
    global _encoder
    with _lock:
        if _encoder is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _encoder = TextCrossEncoder(model_name=RERANK_MODEL, cache_dir=str(MODEL_DIR))
        return _encoder


def rerank(query: str, texts: list[str]) -> list[float]:
    if not texts:
        return []
    logits = list(_load().rerank(query, texts))
    return [1.0 / (1.0 + math.exp(-float(value))) for value in logits]
