import importlib.util
import logging
import os
import threading
from pathlib import Path

from . import db, library
from .generation import providers
from .ingestion import pipelines, staging

SAMPLES = Path(__file__).parent / "samples"
FLAG = "sample_seeded"
NAME = "Sample: Northwind Labs"
DESCRIPTION = (
    "Sample files from a fictional company, so you can try Cairn right away. "
    "Ask about vacation days, travel limits, the holiday calendar, the sensor pairing code or the offsite."
)
TEXT_FILES = ("Employee Handbook 2026.pdf", "Benefits Guide.docx", "Remote Work Policy.md", "Holiday Calendar 2026.csv")
MEDIA_FILES = ("Meridian Sensor Quick Start.png", "Quarterly Update.m4a")
MEDIA_LIBRARIES = ("faster_whisper", "rapidocr_onnxruntime")
log = logging.getLogger("cairn.sample")


def start() -> None:
    threading.Thread(target=ensure, daemon=True, name="sample").start()


def ensure() -> None:
    if os.getenv("SEED_SAMPLE", "1") == "0" or db.one("SELECT 1 AS x FROM settings WHERE key = ?", (FLAG,)):
        return
    db.run("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (FLAG,))
    if db.one("SELECT 1 AS x FROM collections LIMIT 1"):
        return
    try:
        seed()
    except Exception:
        log.exception("Could not create the sample collection")


def seed() -> None:
    pipeline = pipelines.publish(pipelines.create("Standard document processing", "standard")["id"])
    collection = library.create_collection(
        NAME, pipeline["id"], pipeline["latest_version"], providers.preferred_model(), description=DESCRIPTION
    )
    media_ready = all(importlib.util.find_spec(name) for name in MEDIA_LIBRARIES)
    for name in TEXT_FILES + (MEDIA_FILES if media_ready else ()):
        try:
            library.add_document(collection["id"], staging.stage(name, (SAMPLES / name).read_bytes())["stage_id"])
        except Exception:
            log.exception("Could not add the sample file %s", name)
    log.info("Created the sample collection with %s", "text, image and audio files" if media_ready else "text files")
