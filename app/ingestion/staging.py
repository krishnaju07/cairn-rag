import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import MAX_UPLOAD_MB, UPLOAD_DIR
from . import media, tokens
from .extract import SUPPORTED, SUPPORTED_NAMES, ExtractionError, ScannedPdfError, extract

STAGE_DIR = UPLOAD_DIR / "staged"
STAGE_DIR.mkdir(parents=True, exist_ok=True)
MAX_AGE_SECONDS = 24 * 3600
STAGE_ID = re.compile(r"up_[0-9a-f]{12}")
SCANNED_TOKENS_PER_PAGE = 600


def _scanned_record(path: Path) -> dict[str, Any]:
    import pymupdf

    pages = len(pymupdf.open(path))
    return {"pages": pages, "paged": True, "tokens": pages * SCANNED_TOKENS_PER_PAGE}


def stage(filename: str, data: bytes, allow_scanned: bool = False) -> dict[str, Any]:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in SUPPORTED:
        raise ExtractionError(f"Unsupported file type .{ext or '?'}. Supported: {SUPPORTED_NAMES}.")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise ExtractionError(f"File is larger than {MAX_UPLOAD_MB} MB.")
    stage_id = f"up_{uuid.uuid4().hex[:12]}"
    path = STAGE_DIR / f"{stage_id}.{ext}"
    path.write_bytes(data)
    try:
        if ext in media.AUDIO:
            seconds, _ = media.audio_info(path)
            counts = {**media.estimate_audio(seconds), "media": "audio", "seconds": round(seconds)}
        elif ext in media.IMAGE:
            media.check_image(path)
            counts = {"pages": 1, "paged": False, "tokens": media.IMAGE_TOKENS, "media": "image"}
        else:
            extraction = extract(path, ext)
            total = sum(tokens.count_many(extraction.pages))
            counts = {"pages": len(extraction.pages), "paged": extraction.paged, "tokens": total}
    except media.MediaError as error:
        path.unlink(missing_ok=True)
        raise ExtractionError(str(error)) from error
    except ScannedPdfError:
        if not allow_scanned:
            path.unlink(missing_ok=True)
            raise
        counts = _scanned_record(path)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    record = {"stage_id": stage_id, "filename": Path(filename).name, "ext": ext, "size_bytes": len(data), **counts}
    (STAGE_DIR / f"{stage_id}.json").write_text(json.dumps(record))
    return record


def load(stage_id: str) -> dict[str, Any]:
    sidecar = STAGE_DIR / f"{stage_id}.json"
    if not STAGE_ID.fullmatch(stage_id) or not sidecar.exists():
        raise ExtractionError("This upload has expired. Choose the file again.")
    record = json.loads(sidecar.read_text())
    record["path"] = STAGE_DIR / f"{stage_id}.{record['ext']}"
    return record


def discard(stage_id: str) -> None:
    if not STAGE_ID.fullmatch(stage_id):
        return
    for path in STAGE_DIR.glob(f"{stage_id}.*"):
        path.unlink(missing_ok=True)


def purge_old() -> None:
    cutoff = time.time() - MAX_AGE_SECONDS
    for path in STAGE_DIR.iterdir():
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
