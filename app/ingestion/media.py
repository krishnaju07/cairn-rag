import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..config import MODEL_DIR

AUDIO = {"mp3", "wav", "m4a", "flac", "ogg", "opus", "aac", "webm"}
IMAGE = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}
MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "webm": "audio/webm",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
SPEECH_MODELS = (
    ("tiny", "Tiny", "About 75 MB. Fastest, but misses more words."),
    ("base", "Base", "About 145 MB. A good balance of speed and accuracy."),
    ("small", "Small", "About 480 MB. Noticeably more accurate, and several times slower."),
)
WINDOW_SECONDS = 90
TOKENS_PER_SECOND = 2.6
IMAGE_TOKENS = 250
MAX_IMAGE_SIDE = 3000
ROW_SHARE = 0.5
PARAGRAPH_GAP = 0.8
# RapidOCR bundles a Chinese and English recognizer that often drops the spaces between English words.
OCR_REPO = "SWHL/RapidOCR"
OCR_MODEL = "PP-OCRv3/en_PP-OCRv3_rec_infer.onnx"
OCR_REVISION = "1cfba2e90fc938db55889873735088de210cc173"
MISSING = "Audio and image files need the media extras. Install them with: pip install -e '.[media]'"

Layout = dict[str, Any]
log = logging.getLogger("cairn.media")


class MediaError(Exception):
    pass


def _unreadable(action: str, error: Exception) -> MediaError:
    log.warning("%s: %s", action, error)
    return MediaError(f"{action}. The file may be damaged or in an unsupported format.")


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Line:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def middle(self) -> float:
        return (self.y0 + self.y1) / 2


def kind_of(ext: str) -> str:
    return "audio" if ext in AUDIO else ("image" if ext in IMAGE else "")


def clock(seconds: Optional[float]) -> str:
    total = max(int(seconds or 0), 0)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def audio_info(path: Path) -> tuple[float, str]:
    try:
        import av
    except ImportError as error:
        raise MediaError(MISSING) from error
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise MediaError("This file has no audio track.")
            seconds = container.duration / av.time_base if container.duration else 0.0
            return seconds, str(container.metadata.get("title") or "").strip()
    except MediaError:
        raise
    except Exception as error:
        raise _unreadable("Could not read this audio file", error) from error


def estimate_audio(seconds: float) -> dict[str, Any]:
    return {"pages": max(1, math.ceil(seconds / WINDOW_SECONDS)), "paged": False, "tokens": round(seconds * TOKENS_PER_SECOND)}


def check_image(path: Path) -> None:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
    except Exception as error:
        raise _unreadable("Could not read this image", error) from error


_whisper_models: dict[str, Any] = {}
_whisper_lock = threading.Lock()
_transcribing = threading.Lock()


def _whisper(name: str):
    with _whisper_lock:
        if name not in _whisper_models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:
                raise MediaError(MISSING) from error
            folder = str(MODEL_DIR / "whisper")
            _whisper_models[name] = WhisperModel(name, device="cpu", compute_type="int8", download_root=folder)
        return _whisper_models[name]


def transcribe(path: Path, model: str, deadline: Optional[float]) -> tuple[list[Segment], str]:
    engine = _whisper(model)
    segments: list[Segment] = []
    with _transcribing:
        try:
            found, info = engine.transcribe(str(path), vad_filter=True, condition_on_previous_text=False)
            for item in found:
                if deadline and time.monotonic() > deadline:
                    raise MediaError("Transcription exceeded the audio time limit set on the Text Extraction step.")
                text = item.text.strip()
                if text:
                    segments.append(Segment(item.start, item.end, text))
        except MediaError:
            raise
        except Exception as error:
            raise _unreadable("Could not transcribe this audio file", error) from error
    if not segments:
        raise MediaError("No speech was found in this recording.")
    return segments, info.language


def audio_pages(segments: list[Segment]) -> tuple[list[str], list[Layout]]:
    groups: list[list[Segment]] = []
    for segment in segments:
        if groups and segment.end - groups[-1][0].start <= WINDOW_SECONDS:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    pages, layouts = [], []
    for group in groups:
        text, cues = "", []
        for segment in group:
            text += (" " if text else "") + segment.text
            cues.append([round(segment.start, 2), round(segment.end, 2), len(text) - len(segment.text), len(text)])
        pages.append(text)
        layouts.append({"start": round(group[0].start, 2), "end": round(group[-1].end, 2), "cues": cues})
    return pages, layouts


_ocr_engine: Any = None
_ocr_lock = threading.Lock()


def _ocr():
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            try:
                from huggingface_hub import hf_hub_download
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as error:
                raise MediaError(MISSING) from error
            try:
                recognizer = hf_hub_download(OCR_REPO, OCR_MODEL, revision=OCR_REVISION, cache_dir=str(MODEL_DIR / "ocr"))
            except Exception as error:
                raise MediaError(f"Could not download the English text-recognition model: {error}") from error
            _ocr_engine = RapidOCR(rec_model_path=recognizer)
        return _ocr_engine


def read_lines(path: Path) -> list[Line]:
    try:
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        width, height = image.size
        found, _ = _ocr()(np.asarray(image)[:, :, ::-1])
    except MediaError:
        raise
    except Exception as error:
        raise _unreadable("Could not read the text in this image", error) from error
    lines = []
    for corners, text, _ in found or []:
        xs, ys = [point[0] for point in corners], [point[1] for point in corners]
        if text.strip():
            lines.append(Line(min(xs) / width, min(ys) / height, max(xs) / width, max(ys) / height, text.strip()))
    if not lines:
        raise MediaError("No text was found in this image. Photos and drawings without any text cannot be searched yet.")
    return lines


def _rows(lines: list[Line]) -> list[list[Line]]:
    rows: list[list[Line]] = []
    for line in sorted(lines, key=lambda item: item.middle):
        anchor = rows[-1][0] if rows else None
        if anchor and abs(line.middle - anchor.middle) <= ROW_SHARE * max(line.height, anchor.height):
            rows[-1].append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda item: item.x0) for row in rows]


def image_page(lines: list[Line]) -> tuple[str, Layout]:
    rows = _rows(lines)
    typical = sorted(line.height for line in lines)[len(lines) // 2]
    text, boxes = "", []
    for index, row in enumerate(rows):
        if index:
            gap = min(line.y0 for line in row) - max(line.y1 for line in rows[index - 1])
            text += "\n\n" if gap > PARAGRAPH_GAP * typical else "\n"
        for cell, line in enumerate(row):
            text += " | " if cell else ""
            corners = [round(value, 4) for value in (line.x0, line.y0, line.x1, line.y1)]
            boxes.append([*corners, len(text), len(text) + len(line.text)])
            text += line.text
    return text, {"boxes": boxes}
