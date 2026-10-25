import math
from typing import Any

from ..generation import providers
from .catalog import CATALOG

PROMPT_OVERHEAD = 60
QUESTION_TOKENS = 30
REFERENCE_PAGES = 10
REFERENCE_TOKENS = 6000


def estimate(steps: list[dict[str, Any]], tokens: int, pages: int) -> dict[str, Any]:
    chunk = next((s["settings"] for s in steps if s["type"] == "chunk"), {"size": 500, "overlap": 50})
    chunks = max(1, math.ceil(tokens / max(chunk["size"] - chunk["overlap"], 1)))
    items: list[dict[str, Any]] = []
    for step in steps:
        if not CATALOG[step["type"]].costly:
            continue
        settings, spec = step["settings"], step["settings"].get("llm", "")
        item = {"step_id": step["id"], "type": step["type"], "label": CATALOG[step["type"]].label, "model": spec}
        if not providers.is_ready(spec):
            items.append({**item, "ready": False, "input_tokens": 0, "output_tokens": 0, "usd": None, "calls": 0, "local": False})
            continue
        if step["type"] == "questions":
            calls = min(settings["max_chunks"], chunks)
            per_call_in = min(chunk["size"], tokens) + PROMPT_OVERHEAD
            per_call_out = min(settings["max_tokens"], settings["per_chunk"] * QUESTION_TOKENS)
        else:
            calls = 1
            per_call_in = min(tokens, providers.input_budget(spec)) + PROMPT_OVERHEAD
            per_call_out = settings["max_tokens"]
        tokens_in, tokens_out = per_call_in * calls, per_call_out * calls
        items.append(
            {
                **item,
                "ready": True,
                "calls": calls,
                "input_tokens": tokens_in,
                "output_tokens": tokens_out,
                "usd": providers.cost_usd(spec, tokens_in, tokens_out),
                "local": providers.parse_spec(spec)[0] == "ollama",
                "free": providers.cost_usd(spec, 1, 1) == 0,
            }
        )
    known = [item["usd"] for item in items if item["usd"] is not None]
    extract_step = next((s["settings"] for s in steps if s["type"] == "extract"), {})
    return {
        "tokens": tokens,
        "pages": pages,
        "chunks": chunks,
        "items": items,
        "usd": round(sum(known), 6),
        "costly": bool(items),
        "llamaparse": extract_step.get("engine") == "llamaparse",
    }


def reference(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return estimate(steps, REFERENCE_TOKENS, REFERENCE_PAGES)


def combine(figures: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, dict[str, Any]] = {}
    for figure in figures:
        for item in figure["items"]:
            current = merged.setdefault(item["step_id"], {**item, "calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0})
            current["calls"] += item["calls"]
            current["input_tokens"] += item["input_tokens"]
            current["output_tokens"] += item["output_tokens"]
            current["usd"] = None if current["usd"] is None or item["usd"] is None else current["usd"] + item["usd"]
    items = list(merged.values())
    known = [item["usd"] for item in items if item["usd"] is not None]
    return {
        "tokens": sum(f["tokens"] for f in figures),
        "pages": sum(f["pages"] for f in figures),
        "chunks": sum(f["chunks"] for f in figures),
        "items": items,
        "usd": round(sum(known), 6),
        "costly": bool(items),
        "llamaparse": any(f.get("llamaparse") for f in figures),
    }
