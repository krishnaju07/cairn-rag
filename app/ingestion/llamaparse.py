import json
import time
from pathlib import Path

import httpx

from .. import settings

BASE_URL = "https://api.cloud.llamaindex.ai/api/v2/parse"
POLL_SECONDS = 2.0
TIERS = ("fast", "cost_effective", "agentic")


class LlamaParseError(Exception):
    pass


def configured() -> bool:
    return bool(settings.get("LLAMA_CLOUD_API_KEY"))


def _check(response: httpx.Response) -> dict:
    if response.status_code == 401:
        raise LlamaParseError("LlamaCloud rejected the API key.")
    if response.status_code >= 400:
        raise LlamaParseError(f"LlamaCloud returned {response.status_code}: {response.text[:200]}")
    return response.json()


def parse_pages(path: Path, tier: str, timeout_s: float) -> list[str]:
    if not configured():
        raise LlamaParseError("Set LLAMA_CLOUD_API_KEY in .env to use LlamaParse.")
    deadline = time.monotonic() + timeout_s
    project = settings.get("LLAMA_CLOUD_PROJECT_ID")
    params = {"project_id": project} if project else {}
    headers = {"Authorization": f"Bearer {settings.get('LLAMA_CLOUD_API_KEY')}"}
    try:
        with httpx.Client(headers=headers, timeout=60) as client:
            upload = _check(
                client.post(
                    f"{BASE_URL}/upload",
                    params=params,
                    files={"file": (path.name, path.read_bytes())},
                    data={"configuration": json.dumps({"tier": tier, "version": "latest"})},
                )
            )
            job_id = upload["id"]
            while True:
                job = _check(client.get(f"{BASE_URL}/{job_id}", params=params))["job"]
                if job["status"] == "COMPLETED":
                    break
                if job["status"] in ("FAILED", "CANCELLED"):
                    raise LlamaParseError(job.get("error_message") or f"LlamaParse job {job['status'].lower()}.")
                if time.monotonic() > deadline:
                    raise LlamaParseError("LlamaParse did not finish within the time limit set on the Text Extraction step.")
                time.sleep(POLL_SECONDS)
            result = _check(client.get(f"{BASE_URL}/{job_id}", params={**params, "expand": "text"}))
    except httpx.HTTPError as error:
        raise LlamaParseError(f"Could not reach LlamaCloud: {error}") from error
    pages = sorted(result.get("text", {}).get("pages", []), key=lambda page: page.get("page_number", 0))
    texts = [page.get("text") or page.get("markdown") or "" for page in pages if page.get("success", True)]
    if not any(text.strip() for text in texts):
        raise LlamaParseError("LlamaParse returned no text for this file.")
    return texts
