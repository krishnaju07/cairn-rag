import re
from dataclasses import dataclass

_MARKER = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")


@dataclass
class Mapped:
    text: str
    sources: list[int]
    dropped: list[int]


def map_citations(answer: str, passage_count: int) -> Mapped:
    order: dict[int, int] = {}
    dropped: list[int] = []

    def renumber(match: re.Match) -> str:
        kept = []
        for number in (int(part) for part in re.split(r"[,;]\s*", match.group(1))):
            if not 1 <= number <= passage_count:
                dropped.append(number)
                continue
            order.setdefault(number, len(order) + 1)
            kept.append(order[number])
        return "".join(f"[{number}]" for number in sorted(set(kept)))

    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", _MARKER.sub(renumber, answer)).strip()
    sources = [original for original, _ in sorted(order.items(), key=lambda item: item[1])]
    return Mapped(text, sources, dropped)
