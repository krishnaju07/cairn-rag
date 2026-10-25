from functools import lru_cache
from typing import Any, Optional

from . import db, library
from .ingestion.extract import document_furniture, pdf_layout
from .ingestion.pages import page_span

IMAGE_SCALE = 1.8
LINE_TOLERANCE = 3.0
Span = tuple[int, int]
Mark = tuple[int, int, bool]


class ViewerError(Exception):
    pass


def parse_focus(value: str) -> list[Span]:
    spans = []
    for part in value.split(","):
        start, _, end = part.partition("-")
        if start.isdigit() and end.isdigit() and int(end) > int(start):
            spans.append((int(start), int(end)))
    return spans


def view(document_id: str, page: int, chunk_id: Optional[int], focus: str = "") -> dict[str, Any]:
    document = library.get_document(document_id)
    count = document["page_count"]
    if not 1 <= page <= count:
        raise ViewerError("That page does not exist.")
    rows = {row["page"]: row for row in db.many("SELECT page, start_char, text FROM pages WHERE document_id = ?", (document_id,))}
    stored = rows[page]
    chunk = db.one("SELECT * FROM chunks WHERE id = ? AND document_id = ?", (chunk_id, document_id)) if chunk_id else None
    starts = [rows[number]["start_char"] for number in sorted(rows)]
    context = (
        _local(chunk["start_char"], chunk["end_char"], starts, page, stored["text"]) if chunk and _on_page(chunk, page) else None
    )
    focused = [span for span in (_local(s, e, starts, page, stored["text"]) for s, e in parse_focus(focus)) if span]
    marks = _marks(context, focused)
    layout = db.one("SELECT kind, data FROM page_layout WHERE document_id = ? AND page = ?", (document_id, page))
    kind = layout["kind"] if layout else ""
    payload: dict[str, Any] = {
        **_neighbours(chunk),
        "document": document,
        "page": page,
        "page_count": count,
        "paged": bool(document["paged"]),
        "chunk": chunk,
        "span": context,
        "segments": _segments(stored["text"], marks),
        "media": kind,
        "audio": None,
        "image": None,
        "rects": [],
    }
    if kind == "audio":
        payload["audio"] = _audio(document_id, db.loads(layout["data"], {}), stored["text"], marks)
    elif kind == "image":
        payload["image"] = f"/documents/{document_id}/media"
        payload["rects"] = _image_rects(db.loads(layout["data"], {}), marks)
    elif document["ext"] == "pdf":
        payload["image"] = f"/documents/{document_id}/pages/{page}/image"
        payload["rects"] = _rects(library.document_path(document_id), page, stored["text"], marks)
    return payload


def _level(marks: list[Mark], start: int, end: int) -> str:
    inside = [strong for first, last, strong in marks if first < end and start < last]
    return "strong" if any(inside) else ("soft" if inside else "")


def _audio(document_id: str, layout: dict[str, Any], text: str, marks: list[Mark]) -> dict[str, Any]:
    cues = [{"start": start, "end": end, "text": text[a:b], "level": _level(marks, a, b)} for start, end, a, b in layout["cues"]]
    seek = next((cue["start"] for level in ("strong", "soft") for cue in cues if cue["level"] == level), layout["start"])
    return {"src": f"/documents/{document_id}/media", "start": layout["start"], "end": layout["end"], "seek": seek, "cues": cues}


def _image_rects(layout: dict[str, Any], marks: list[Mark]) -> list[dict[str, Any]]:
    rects = []
    for x0, y0, x1, y1, start, end in layout["boxes"]:
        level = _level(marks, start, end)
        if level:
            rects.append({"x": x0 * 100, "y": y0 * 100, "w": (x1 - x0) * 100, "h": (y1 - y0) * 100, "strong": level == "strong"})
    return rects


def _on_page(chunk: dict[str, Any], page: int) -> bool:
    return chunk["page_start"] <= page <= chunk["page_end"]


def _local(start: int, end: int, starts: list[int], page: int, text: str) -> Optional[Span]:
    first, last = page_span(starts, len(text), page, start, end)
    return (first, last) if last > first else None


def _marks(context: Optional[Span], focused: list[Span]) -> list[Mark]:
    if not focused:
        return [(context[0], context[1], True)] if context else []
    return ([(context[0], context[1], False)] if context else []) + [(start, end, True) for start, end in focused]


def _segments(text: str, marks: list[Mark]) -> list[dict[str, Any]]:
    cuts = sorted({0, len(text), *(point for start, end, _ in marks for point in (start, end))})
    segments: list[dict[str, Any]] = []
    for start, end in zip(cuts, cuts[1:]):
        inside = [strong for first, last, strong in marks if first <= start and end <= last]
        level = "strong" if any(inside) else ("soft" if inside else "")
        if segments and segments[-1]["level"] == level:
            segments[-1]["text"] += text[start:end]
        else:
            segments.append({"text": text[start:end], "level": level})
    return segments


def _neighbours(chunk: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not chunk or chunk["kind"] != "chunk":
        return {"chunk_number": None, "chunk_total": None, "previous": None, "next": None}
    total = db.one("SELECT COUNT(*) AS n FROM chunks WHERE document_id = ? AND kind = 'chunk'", (chunk["document_id"],))["n"]
    sql = "SELECT id, page_start FROM chunks WHERE document_id = ? AND kind = 'chunk' AND idx = ?"
    return {
        "chunk_number": chunk["idx"] + 1,
        "chunk_total": total,
        "previous": db.one(sql, (chunk["document_id"], chunk["idx"] - 1)),
        "next": db.one(sql, (chunk["document_id"], chunk["idx"] + 1)),
    }


@lru_cache(maxsize=64)
def _open(path: str):
    import pymupdf

    return pymupdf.open(path)


@lru_cache(maxsize=16)
def _furniture(path: str) -> frozenset:
    return document_furniture(_open(path))


def _rects(path, page_number: int, stored_text: str, marks: list[Mark]) -> list[dict[str, Any]]:
    if not marks:
        return []
    page = _open(str(path))[page_number - 1]
    text, boxes = pdf_layout(page, _furniture(str(path)))
    if text != stored_text:
        return []
    width, height = page.rect.width, page.rect.height
    rects = []
    for first, last, strong in marks:
        for x0, y0, x1, y1 in _lines(boxes, first, last):
            rects.append(
                {
                    "x": x0 / width * 100,
                    "y": y0 / height * 100,
                    "w": (x1 - x0) / width * 100,
                    "h": (y1 - y0) / height * 100,
                    "strong": strong,
                }
            )
    return rects


def _lines(boxes: list[tuple], first: int, last: int) -> list[list[float]]:
    lines: list[list[float]] = []
    for x0, y0, x1, y1, start, end in boxes:
        if end <= first or start >= last:
            continue
        for line in lines:
            if abs(line[1] - y0) <= LINE_TOLERANCE:
                line[0], line[2] = min(line[0], x0), max(line[2], x1)
                line[1], line[3] = min(line[1], y0), max(line[3], y1)
                break
        else:
            lines.append([x0, y0, x1, y1])
    return lines


def page_image(document_id: str, page_number: int) -> bytes:
    import pymupdf

    path = library.document_path(document_id)
    pixmap = _open(str(path))[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(IMAGE_SCALE, IMAGE_SCALE))
    return pixmap.tobytes("png")
