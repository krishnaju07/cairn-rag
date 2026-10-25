import re
from dataclasses import dataclass, field

from ..retrieval.search import Hit
from ..retrieval.text import STOPWORDS, stem

MARKER = re.compile(r"\[(?:(?:passage|source|doc|p)\s*)?\d+(?:\s*[,;]\s*\d+)*\]", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
BOUNDARY = re.compile(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")
MIN_SUPPORT = 0.45
LEADING_SUBJECT = re.compile(r"(^|[.!?]\s+|\n)[ \t]*\[(?:[Pp](?:assage)?\s*)?\d+\]\s+(?=[a-z])", re.M)
LABEL = re.compile(r"\b(?:passages?|sources?)\s+(?=\[(?:[Pp](?:assage)?\s*)?\d)", re.I)
HINT_BONUS = 0.05


def readable(filename: str) -> str:
    return re.sub(r"[_\-.]+", " ", filename)


def content_words(text: str) -> set[str]:
    words = re.findall(r"[\w$%.]+", text.lower())
    return {stem(word.strip(".")) for word in words if word not in STOPWORDS and (len(word) > 2 or word.isdigit())}


def passage_words(hit: Hit) -> set[str]:
    return content_words(f"{readable(hit.filename)} {hit.text}")


def grounding(answer_text: str, passages: list[Hit]) -> float:
    words = content_words(MARKER.sub("", answer_text))
    if not words:
        return 0.0
    seen: set[str] = set()
    for hit in passages:
        seen |= passage_words(hit)
    return len(words & seen) / len(words)


def _rarity(sets: list[set[str]]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for words in sets:
        for word in words:
            counts[word] = counts.get(word, 0) + 1
    return {word: 1.0 / count for word, count in counts.items()}


@dataclass
class Attribution:
    text: str
    focus: dict[int, list[tuple[int, int]]] = field(default_factory=dict)


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, cursor = [], 0
    for match in BOUNDARY.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(text)))
    return [(start, end) for start, end in spans if text[start:end].strip()]


def best_span(words: set[str], passage: str) -> tuple[int, int] | None:
    scored = [(len(content_words(passage[start:end]) & words), start, end) for start, end in sentence_spans(passage)]
    best = max(scored, default=None)
    return (best[1], best[2]) if best and best[0] else None


def tidy(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"[ \t]+([.,;:!?])", r"\1", text))


def attribute(text: str, passages: list[Hit]) -> Attribution:
    hinted = {int(n) for marker in MARKER.findall(text) for n in re.findall(r"\d+", marker)}
    sets = [passage_words(hit) for hit in passages]
    rarity = _rarity(sets)
    lines, focus = [], {}
    text = LEADING_SUBJECT.sub(lambda match: f"{match.group(1)}The document ", LABEL.sub("", text))
    for line in tidy(MARKER.sub("", text)).split("\n"):
        pieces = []
        for sentence in (s.strip() for s in SENTENCE.split(line) if s.strip()):
            words = content_words(sentence)
            if not words:
                pieces.append(sentence)
                continue
            total = sum(rarity.get(word, 1.0) for word in words)
            scores = [
                sum(rarity.get(word, 1.0) for word in words & covered) / total + (HINT_BONUS if index + 1 in hinted else 0.0)
                for index, covered in enumerate(sets)
            ]
            best = max(range(len(passages)), key=scores.__getitem__)
            if scores[best] < MIN_SUPPORT:
                pieces.append(sentence)
                continue
            pieces.append(f"{sentence} [{best + 1}]")
            span = best_span(words, passages[best].text)
            if span and span not in focus.setdefault(best + 1, []):
                focus[best + 1].append(span)
        if pieces:
            lines.append(" ".join(pieces))
    return Attribution("\n".join(lines).strip(), focus)


def specific_terms(question: str) -> list[str]:
    terms = []
    for term in TERM.findall(question):
        if any(character.isdigit() for character in term) and len(term) >= 2:
            terms.append(term.lower())
    return terms


def covers_terms(terms: list[str], hit: Hit) -> bool:
    text = hit.text.lower()
    return all(term in text for term in terms)
