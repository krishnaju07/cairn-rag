import re
from dataclasses import dataclass, replace

from . import pages as page_utils
from . import tokens

_SENTENCE_BREAK = re.compile(r"\n{2,}|(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")
_LINE_BREAK = re.compile(r"\n+")

Unit = tuple[int, int, int]


@dataclass
class Chunk:
    text: str
    start: int
    end: int
    tokens: int


def chunk_document(
    pages: list[str], size: int = 500, overlap: int = 50, by_line: bool = False, within_pages: bool = True
) -> list[Chunk]:
    text, starts = page_utils.join(pages)
    if not within_pages:
        return chunk_text(text, size, overlap, by_line)
    chunks: list[Chunk] = []
    for page, origin in zip(pages, starts):
        for chunk in chunk_text(page, size, overlap, by_line):
            chunks.append(replace(chunk, start=chunk.start + origin, end=chunk.end + origin))
    return chunks


def chunk_text(text: str, size: int = 500, overlap: int = 50, by_line: bool = False) -> list[Chunk]:
    if overlap >= size:
        raise ValueError("Overlap must be smaller than the chunk size.")
    units = _units(text, size, overlap, by_line)
    groups: list[tuple[int, int]] = []
    i = 0
    while i < len(units):
        j, total = i, 0
        while j < len(units) and (j == i or total + units[j][2] <= size):
            total += units[j][2]
            j += 1
        groups.append((i, j))
        if j >= len(units):
            break
        back, carried = j, 0
        while back - 1 > i and carried + units[back - 1][2] <= overlap:
            back -= 1
            carried += units[back][2]
        i = back
    spans = [(units[first][0], units[last - 1][1]) for first, last in groups]
    texts = [text[start:end] for start, end in spans]
    counts = tokens.count_many(texts)
    return [Chunk(body, start, end, count) for body, (start, end), count in zip(texts, spans, counts)]


def _units(text: str, size: int, overlap: int, by_line: bool) -> list[Unit]:
    pattern = _LINE_BREAK if by_line else _SENTENCE_BREAK
    spans, cursor = [], 0
    for match in pattern.finditer(text):
        spans.append((cursor, match.start()))
        cursor = match.end()
    spans.append((cursor, len(text)))
    spans = [(start, end) for start, end in spans if text[start:end].strip()]
    counts = tokens.count_many([text[start:end] for start, end in spans])
    units: list[Unit] = []
    for (start, end), count in zip(spans, counts):
        if count > size:
            units.extend(_windows(text, start, end, size, overlap))
        else:
            units.append((start, end, count))
    return units


def _windows(text: str, start: int, end: int, size: int, overlap: int) -> list[Unit]:
    token_spans, scale = tokens.spans(text[start:end])
    width = max(int(size / scale), 1)
    stride = max(width - int(overlap / scale), 1)
    windows = []
    for first in range(0, len(token_spans), stride):
        last = min(first + width, len(token_spans))
        window_start = start + token_spans[first][0]
        window_end = start + token_spans[last - 1][1]
        windows.append((window_start, window_end, round((last - first) * scale)))
        if last == len(token_spans):
            break
    return windows
