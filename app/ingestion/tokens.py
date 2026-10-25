import re
from functools import lru_cache

from ..config import MODEL_DIR, TOKENIZER_REPO

_WORD = re.compile(r"\w+|[^\w\s]")
_FALLBACK_SCALE = 1.3


@lru_cache(maxsize=1)
def _tokenizer():
    try:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        path = hf_hub_download(TOKENIZER_REPO, "tokenizer.json", cache_dir=str(MODEL_DIR))
        tokenizer = Tokenizer.from_file(path)
        tokenizer.no_padding()
        tokenizer.no_truncation()
        return tokenizer
    except Exception:
        return None


def is_exact() -> bool:
    return _tokenizer() is not None


def count(text: str) -> int:
    return count_many([text])[0]


def count_many(texts: list[str]) -> list[int]:
    tokenizer = _tokenizer()
    if tokenizer is None:
        return [round(len(_WORD.findall(text)) * _FALLBACK_SCALE) for text in texts]
    encodings = tokenizer.encode_batch(texts, add_special_tokens=False)
    return [len(encoding.ids) for encoding in encodings]


def spans(text: str) -> tuple[list[tuple[int, int]], float]:
    tokenizer = _tokenizer()
    if tokenizer is None:
        return [match.span() for match in _WORD.finditer(text)], _FALLBACK_SCALE
    encoding = tokenizer.encode(text, add_special_tokens=False)
    return [tuple(pair) for pair in encoding.offsets], 1.0
