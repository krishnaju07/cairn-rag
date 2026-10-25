import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
MODEL_DIR = DATA_DIR / "models"
DB_PATH = DATA_DIR / "cairn.db"
ENV_FILE = Path(os.getenv("ENV_FILE", ROOT / ".env"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
NVIDIA_URL = os.getenv("NVIDIA_URL", "https://integrate.api.nvidia.com/v1")

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2"
TOKENIZER_REPO = "BAAI/bge-small-en-v1.5"

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "50"))
INGEST_WORKERS = int(os.getenv("INGEST_WORKERS", "2"))

for directory in (DATA_DIR, UPLOAD_DIR, MODEL_DIR):
    directory.mkdir(parents=True, exist_ok=True)
