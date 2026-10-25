import re

from ..retrieval.rerank import rerank
from ..retrieval.search import Hit

SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
SENTENCE_END = (".", "!", "?", ":")
MAX_SENTENCES = 2
MIN_SCORE = 0.1
RELATIVE_SCORE = 0.3
MIN_WORDS = 4
HEADING_WORDS = 8


def sentences(text: str) -> list[str]:
    units: list[str] = []
    for block in re.split(r"\n{2,}", text):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if lines and all(" | " in line for line in lines):
            units.extend(lines)
            continue
        if len(lines) > 1 and len(lines[0].split()) <= HEADING_WORDS and not lines[0].endswith(SENTENCE_END):
            lines = lines[1:]
        units.extend(SENTENCE.split(" ".join(lines)))
    return [unit for unit in units if len(unit.split()) >= MIN_WORDS or " | " in unit]


def overview_answer(passages: list[Hit]) -> str:
    parts, seen = [], set()
    for number, hit in enumerate(passages, start=1):
        if hit.document_id in seen:
            continue
        seen.add(hit.document_id)
        lead = " ".join(sentences(hit.text)[:2]) or hit.text[:240]
        parts.append(f"{hit.filename} begins: {lead} [{number}]")
    return "\n".join(parts)


def answer(question: str, passages: list[Hit]) -> str:
    candidates = [(number, sentence) for number, hit in enumerate(passages, start=1) for sentence in sentences(hit.text)]
    if not candidates:
        return ""
    scores = rerank(question, [sentence for _, sentence in candidates])
    ranked = sorted(zip(scores, candidates), key=lambda item: item[0], reverse=True)
    if ranked[0][0] < MIN_SCORE:
        return ""
    chosen = [item for item in ranked[:MAX_SENTENCES] if item[0] >= max(MIN_SCORE, ranked[0][0] * RELATIVE_SCORE)]
    chosen.sort(key=lambda item: candidates.index(item[1]))
    return " ".join(f"{sentence} [{number}]" for _, (number, sentence) in chosen)
