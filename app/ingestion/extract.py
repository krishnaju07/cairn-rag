import csv
import io
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PAGE_CHARS = 3000
CSV_ROWS_PER_PAGE = 40
SUPPORTED = {"pdf", "docx", "txt", "md", "csv"}
_SENTENCE_END = (".", "!", "?", ":", '"', "\u201d")
ROW_TOLERANCE = 2.5
CELL_CHARS = 32
MARGIN = 0.075
MIN_REPEATS = 3
REPEAT_SHARE = 0.4

Box = tuple[float, float, float, float, int, int]


class ExtractionError(Exception):
    pass


class ScannedPdfError(ExtractionError):
    pass


@dataclass
class Extraction:
    pages: list[str]
    paged: bool
    meta: dict[str, str] = field(default_factory=dict)


def extract(
    path: Path, ext: str, timeout_s: Optional[float] = None, engine: str = "local", tier: str = "cost_effective"
) -> Extraction:
    ext = ext.lower().lstrip(".")
    if ext not in SUPPORTED:
        raise ExtractionError(f"Unsupported file type .{ext}. Supported: PDF, DOCX, TXT, MD, CSV.")
    deadline = time.monotonic() + timeout_s if timeout_s else None
    if engine == "llamaparse" and ext == "pdf":
        return _llamaparse(path, tier, timeout_s or 120)
    reader = {"pdf": _pdf, "docx": _docx, "csv": _csv}.get(ext, _plain)
    result = reader(path, deadline)
    if not any(page.strip() for page in result.pages):
        raise ExtractionError("No text could be extracted from this file.")
    return result


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    block: int
    line: int


@dataclass
class Line:
    cells: list[list[Word]]

    @property
    def words(self) -> list[Word]:
        return [word for cell in self.cells for word in cell]

    @property
    def text(self) -> str:
        return " | ".join(" ".join(word.text for word in cell) for cell in self.cells)

    @property
    def top(self) -> float:
        return min(word.y0 for word in self.words)

    @property
    def bottom(self) -> float:
        return max(word.y1 for word in self.words)

    @property
    def right(self) -> float:
        return max(word.x1 for word in self.words)

    @property
    def left(self) -> float:
        return min(word.x0 for word in self.words)


def _words(page) -> list[Word]:
    blocks: dict[int, dict[int, list]] = {}
    for x0, y0, x1, y1, text, block, line, number in page.get_text("words"):
        blocks.setdefault(block, {}).setdefault(line, []).append((number, Word(x0, y0, x1, y1, text, block, line)))
    return [
        word
        for block in sorted(blocks)
        for line in sorted(blocks[block])
        for _, word in sorted(blocks[block][line], key=lambda item: item[0])
    ]


def _lines(words: list[Word]) -> list[Line]:
    lines: list[Line] = []
    for word in words:
        if lines and lines[-1].words[-1].block == word.block and lines[-1].words[-1].line == word.line:
            lines[-1].cells[0].append(word)
        else:
            lines.append(Line([[word]]))
    return lines


def _rows(lines: list[Line]) -> list[Line]:
    rows: list[Line] = []
    for line in lines:
        previous = rows[-1] if rows else None
        same_row = (
            previous is not None
            and abs(line.top - previous.top) <= ROW_TOLERANCE
            and line.left >= previous.right + 2
            and len(line.text) <= CELL_CHARS
            and all(len(" ".join(word.text for word in cell)) <= CELL_CHARS for cell in previous.cells)
        )
        if same_row:
            previous.cells.append(line.words)
        else:
            rows.append(line)
    return rows


def _signature(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower()).strip()


def _in_margin(line: Line, height: float) -> bool:
    return line.bottom < height * MARGIN or line.top > height * (1 - MARGIN)


def document_furniture(document) -> frozenset:
    counts: dict[str, int] = {}
    for page in document:
        seen = {_signature(line.text) for line in _rows(_lines(_words(page))) if _in_margin(line, page.rect.height)}
        for signature in seen:
            counts[signature] = counts.get(signature, 0) + 1
    needed = max(MIN_REPEATS, round(len(document) * REPEAT_SHARE))
    return frozenset(signature for signature, count in counts.items() if signature and count >= needed)


def pdf_layout(page, furniture: frozenset = frozenset()) -> tuple[str, list[Box]]:
    height = page.rect.height
    lines = [
        line for line in _rows(_lines(_words(page))) if not (_in_margin(line, height) and _signature(line.text) in furniture)
    ]
    text, boxes = "", []
    for index, line in enumerate(lines):
        joined = text.endswith("-") and re.search(r"[A-Za-z]-$", text) and line.words[0].text[:1].islower()
        if joined:
            text = text[:-1]
            boxes[-1] = (*boxes[-1][:5], len(text))
        elif index:
            text += (
                "\n\n"
                if text.rstrip().endswith(_SENTENCE_END) and lines[index - 1].words[-1].block != line.words[0].block
                else "\n"
            )
        for cell_index, cell in enumerate(line.cells):
            if cell_index:
                text += " | "
            for word_index, word in enumerate(cell):
                if word_index:
                    text += " "
                start = len(text)
                text += word.text
                boxes.append((word.x0, word.y0, word.x1, word.y1, start, len(text)))
    return text, boxes


def _check_deadline(deadline: Optional[float]) -> None:
    if deadline and time.monotonic() > deadline:
        raise ExtractionError("Extraction exceeded the time limit set on the Text Extraction step.")


def _pdf(path: Path, deadline: Optional[float]) -> Extraction:
    import pymupdf

    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise ExtractionError(f"Could not open this PDF: {error}") from error
    furniture = document_furniture(document)
    pages = []
    for page in document:
        _check_deadline(deadline)
        pages.append(pdf_layout(page, furniture)[0])
    if not any(page.strip() for page in pages):
        raise ScannedPdfError("This PDF has no text layer. Turn on LlamaParse in the Text Extraction step to read scanned PDFs.")
    meta = {key: value for key, value in (document.metadata or {}).items() if key in ("title", "author") and value}
    return Extraction(pages, True, meta)


def _llamaparse(path: Path, tier: str, timeout_s: float) -> Extraction:
    import pymupdf

    from .llamaparse import LlamaParseError, parse_pages

    try:
        pages = parse_pages(path, tier, timeout_s)
    except LlamaParseError as error:
        raise ExtractionError(str(error)) from error
    try:
        info = pymupdf.open(path).metadata or {}
    except Exception:
        info = {}
    meta = {key: value for key, value in info.items() if key in ("title", "author") and value}
    return Extraction(pages, True, {**meta, "extractor": "llamaparse"})


def _docx(path: Path, deadline: Optional[float]) -> Extraction:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(str(path))
    except Exception as error:
        raise ExtractionError(f"Could not open this DOCX: {error}") from error
    blocks: list[Optional[str]] = []
    for element in document.element.body.iterchildren():
        _check_deadline(deadline)
        if element.tag.endswith("}p"):
            paragraph = Paragraph(element, document)
            if paragraph.text.strip():
                blocks.append(paragraph.text.strip())
            if element.xpath('.//w:br[@w:type="page"]'):
                blocks.append(None)
        elif element.tag.endswith("}tbl"):
            for row in Table(element, document).rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))
    props = document.core_properties
    meta = {key: value for key, value in (("title", props.title), ("author", props.author)) if value}
    return Extraction(_paginate(blocks, "\n\n"), False, meta)


def _plain(path: Path, deadline: Optional[float]) -> Extraction:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    blocks = [block.strip() for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    meta = {}
    if path.suffix.lower() == ".md":
        heading = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
        if heading:
            meta["title"] = heading
    return Extraction(_paginate(blocks, "\n\n"), False, meta)


def _csv(path: Path, deadline: Optional[float]) -> Extraction:
    text = path.read_bytes().decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        raise ExtractionError("This CSV is empty.")
    header, body = rows[0], rows[1:]
    lines = []
    for row in body:
        _check_deadline(deadline)
        cells = [f"{name}: {value.strip()}" for name, value in zip(header, row) if value.strip()]
        if cells:
            lines.append(" | ".join(cells))
    pages = ["\n".join(lines[i : i + CSV_ROWS_PER_PAGE]) for i in range(0, len(lines), CSV_ROWS_PER_PAGE)]
    return Extraction(pages or ["\n".join(header)], False)


def _paginate(blocks: list[Optional[str]], separator: str) -> list[str]:
    pages, current, size = [], [], 0
    for block in blocks:
        if block is None:
            if current:
                pages.append(separator.join(current))
                current, size = [], 0
            continue
        if current and size + len(block) > PAGE_CHARS:
            pages.append(separator.join(current))
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current:
        pages.append(separator.join(current))
    return pages
