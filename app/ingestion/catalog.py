import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from ..generation import providers
from ..retrieval.embeddings import EMBED_MODELS, limit_for
from . import llamaparse, media

ABSTRACT_PROMPT = (
    "Write a concise abstract (2-3 sentences) that captures the main topic and key points of the following document.\n\n"
    "Document:\n{text}"
)
SENTIMENT_PROMPT = (
    "Classify the overall sentiment of the following document as positive, neutral, negative or mixed. "
    'Reply with JSON only: {"label": "...", "reason": "one sentence"}\n\nDocument:\n{text}'
)
QUESTIONS_PROMPT = (
    "Write {count} short questions that the following passage answers. One question per line, no numbering.\n\nPassage:\n{text}"
)

Field = dict[str, Any]


def _number(key: str, label: str, default: int, low: int, high: int, help: str = "") -> Field:
    return {"key": key, "label": label, "type": "number", "default": default, "min": low, "max": high, "help": help}


def _toggle(key: str, label: str, default: bool, help: str = "") -> Field:
    return {"key": key, "label": label, "type": "toggle", "default": default, "help": help}


def _llm_fields(prompt: str, max_tokens: int, tokens_help: str) -> tuple[Field, ...]:
    return (
        {"key": "llm", "label": "Model", "type": "model", "default": "", "required": True},
        _number("max_tokens", "Max output tokens", max_tokens, 20, 2000, tokens_help),
        {
            "key": "prompt",
            "label": "Prompt template",
            "type": "textarea",
            "default": prompt,
            "help": "Use {text} where the content goes.",
        },
    )


@dataclass(frozen=True)
class StepDef:
    type: str
    label: str
    category: str
    icon: str
    blurb: str
    required: bool = False
    once: bool = True
    first: bool = False
    costly: bool = False
    requires: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()
    guide: dict[str, str] = field(default_factory=dict)


CATALOG: dict[str, StepDef] = {
    definition.type: definition
    for definition in (
        StepDef(
            "extract",
            "Text Extraction",
            "Parsing",
            "file-text",
            "PDF, DOCX, TXT, MD, CSV, images and audio",
            required=True,
            first=True,
            fields=(
                {
                    "key": "engine",
                    "label": "OCR for scanned PDFs",
                    "type": "select",
                    "default": "local",
                    "options": [
                        {
                            "value": "local",
                            "label": "Off: read text layers locally",
                            "hint": "Free and private. PDFs without a text layer are rejected with a message that says so.",
                        },
                        {
                            "value": "llamaparse",
                            "label": "On: LlamaParse",
                            "hint": "Reads scanned PDFs and complex tables. Sends each PDF to LlamaCloud and uses your LlamaCloud credits. Needs LLAMA_CLOUD_API_KEY in .env.",
                        },
                    ],
                },
                {
                    "key": "tier",
                    "label": "LlamaParse tier",
                    "type": "select",
                    "default": "cost_effective",
                    "options": [
                        {"value": "fast", "label": "Fast", "hint": "Quickest and cheapest. Best for clean text-heavy scans."},
                        {
                            "value": "cost_effective",
                            "label": "Cost effective",
                            "hint": "Balanced quality and price. A good default.",
                        },
                        {
                            "value": "agentic",
                            "label": "Agentic",
                            "hint": "Highest quality for tables and messy layouts, and the most credits.",
                        },
                    ],
                    "help": "Only used when OCR is on.",
                },
                _number(
                    "timeout_s",
                    "Time limit per file (seconds)",
                    120,
                    10,
                    900,
                    "Extraction stops with a clear error past this limit.",
                ),
                {
                    "key": "speech_model",
                    "label": "Speech model for audio",
                    "type": "select",
                    "default": "base",
                    "options": [{"value": value, "label": label, "hint": hint} for value, label, hint in media.SPEECH_MODELS],
                    "help": "Audio is transcribed on this machine. The model downloads the first time an audio file is processed.",
                },
                _number(
                    "audio_timeout_s",
                    "Time limit per recording (seconds)",
                    1800,
                    60,
                    7200,
                    "Transcription stops with a clear error past this limit.",
                ),
            ),
            guide={
                "what": "Reads each uploaded file and turns it into plain text, page by page. PDFs keep their real pages; other formats are split into sections of about 3,000 characters so citations can still point somewhere. Images are read with OCR and audio is transcribed to text.",
                "when": "Always. Every pipeline starts here.",
                "how": "PDFs are read with PyMuPDF and every word keeps its position on the page, which is how passages are highlighted later. DOCX tables become rows, CSV rows become self-describing lines. Images are read locally with RapidOCR and each line of text keeps its position, so a cited passage is outlined on the image. Audio is transcribed locally with Whisper and cut into sections of about 90 seconds that keep their timestamps, so a citation opens the recording at that moment.",
                "config": "Turn on OCR to send PDFs to LlamaParse (LlamaCloud) instead. It handles scanned PDFs and complex tables but uses your LlamaCloud credits, and the page highlight then shows the cited passage beside the page image. Images and audio always stay on this machine. A larger speech model is more accurate and slower. The time limits protect the worker from very large files.",
                "tips": "Export slides and scans to text-based PDFs before uploading for the best results. Images need readable text: photos and drawings without any text cannot be searched yet. Audio and image support needs the media extras (pip install -e '.[media]').",
            },
        ),
        StepDef(
            "chunk",
            "Chunking",
            "Processing",
            "split",
            "Sentence-aware windows of text",
            required=True,
            requires=("extract",),
            fields=(
                _number(
                    "size",
                    "Chunk size (tokens)",
                    500,
                    50,
                    510,
                    "About 500 works well. The embedding model cannot read more than 512.",
                ),
                _number(
                    "overlap",
                    "Overlap (tokens)",
                    50,
                    0,
                    200,
                    "Repeated text at the start of the next chunk so ideas are not cut in half.",
                ),
                _toggle(
                    "within_pages",
                    "Keep chunks inside one page",
                    True,
                    "Makes citations point to exactly one page. Turn off to let chunks run across page breaks.",
                ),
                _number(
                    "detail_size",
                    "Detail passage size (tokens)",
                    120,
                    0,
                    300,
                    "Small passages inside each chunk make single facts findable. Results still cite the whole chunk. 0 turns it off.",
                ),
            ),
            guide={
                "what": "Splits the extracted text into passages small enough to embed and cite.",
                "when": "Always. Retrieval works on chunks, not on whole documents.",
                "how": "Text is split on paragraphs and sentences, then packed up to the token size. The last sentences of one chunk are repeated at the start of the next, up to the overlap. Sentences longer than a chunk are cut into overlapping windows. Chunks longer than the detail size also get small detail passages; they are searched, but a match always returns and cites its whole chunk.",
                "config": "Smaller chunks give sharper citations; larger chunks keep more context per answer. Overlap must stay below the chunk size.",
                "tips": "Token counts use the embedding model's own tokenizer, so a chunk never exceeds what the model can read.",
            },
        ),
        StepDef(
            "embed",
            "Embedding",
            "Processing",
            "waypoints",
            "Local sentence embeddings",
            required=True,
            requires=("chunk",),
            fields=(
                {
                    "key": "model",
                    "label": "Embedding model",
                    "type": "select",
                    "default": EMBED_MODELS[0]["id"],
                    "options": [{"value": m["id"], "label": m["label"], "hint": m["hint"]} for m in EMBED_MODELS],
                },
                _number("batch_size", "Batch size", 32, 1, 128, "Chunks embedded per batch. Lower it if memory is tight."),
            ),
            guide={
                "what": "Turns every chunk into a vector so passages with similar meaning end up close together.",
                "when": "Always. Semantic and hybrid retrieval depend on it.",
                "how": "Models run locally through ONNX, so nothing leaves your machine and nothing is billed. The first run downloads the model once.",
                "config": "The model chosen here is stored with the collection, and questions are embedded with the same model at query time.",
                "tips": "Changing the model means re-processing documents, so pick it before you upload.",
            },
        ),
        StepDef(
            "index",
            "Vector Indexing",
            "Storage",
            "database",
            "Stores the chunks and vectors for search",
            required=True,
            requires=("embed",),
            guide={
                "what": "Writes chunks, their vectors and a keyword (BM25) index to the collection so it can be searched.",
                "when": "Always. Nothing is searchable until this step runs.",
                "how": "Vectors are kept in SQLite and searched exactly with a cosine scan, which is fast and precise up to a few hundred thousand chunks. The keyword index is an FTS5 table with stemming.",
                "config": "No settings. Test runs skip this step's writes completely.",
                "tips": "Keyword and hybrid retrieval read from the same step, so no separate setup is needed.",
            },
        ),
        StepDef(
            "metadata",
            "Metadata Extraction",
            "Enrichment",
            "tags",
            "Title, author, language, page and word counts",
            requires=("extract",),
            guide={
                "what": "Records the document title, author, detected language, page count and word count.",
                "when": "Use it when you want to filter or display documents by these fields.",
                "how": "Titles come from the file's own metadata or its first heading, with the file name as a fallback. Language is detected from the text.",
                "config": "No settings.",
                "tips": "It is cheap and local, so it is on in the standard template.",
            },
        ),
        StepDef(
            "abstract",
            "LLM Abstract Generation",
            "Enrichment",
            "sparkles",
            "Summarize the document with a language model",
            costly=True,
            requires=("extract", "embed", "index"),
            fields=_llm_fields(ABSTRACT_PROMPT, 300, "Upper bound for the summary length."),
            guide={
                "what": "Generates a short abstract of the whole document and stores it as an extra searchable row.",
                "when": "Use it for long documents where a question is about the overall topic rather than one passage.",
                "how": "The document text is sent to the chosen model once. The abstract is embedded and indexed, but it is never used as a source in answers because it is generated text.",
                "config": "Costs scale with document length. A local Ollama model is free; Anthropic models bill per token.",
                "tips": "Check the estimate before processing large uploads.",
            },
        ),
        StepDef(
            "sentiment",
            "LLM Sentiment Analysis",
            "Enrichment",
            "smile",
            "Classify document sentiment",
            costly=True,
            requires=("extract",),
            fields=_llm_fields(SENTIMENT_PROMPT, 120, "The answer is short; 120 is plenty."),
            guide={
                "what": "Labels each document positive, neutral, negative or mixed and keeps the reason.",
                "when": "Use it for feedback, reviews and support transcripts.",
                "how": "A single model call per document. The label is stored with the document metadata.",
                "config": "The prompt must ask for JSON with label and reason.",
                "tips": "Skip it for policy or technical documents where sentiment carries no signal.",
            },
        ),
        StepDef(
            "questions",
            "Question Generation",
            "Enrichment",
            "message-circle-question",
            "Generate synthetic questions to improve retrieval",
            costly=True,
            requires=("chunk", "embed", "index"),
            fields=(
                *_llm_fields(QUESTIONS_PROMPT, 200, "Upper bound for the questions of one chunk."),
                _number(
                    "max_chunks",
                    "Chunks to cover per document",
                    20,
                    1,
                    200,
                    "Evenly sampled across the document to control cost.",
                ),
                _number("per_chunk", "Questions per chunk", 2, 1, 5),
            ),
            guide={
                "what": "Writes questions that each chunk answers and indexes them, so a user's question can match a chunk by wording that never appears in it.",
                "when": "Use it when users phrase questions very differently from the document text.",
                "how": "Questions are embedded as extra rows. When one matches, the original chunk is returned and cited, never the question.",
                "config": "Cost grows with the number of chunks covered, so the sample size is capped.",
                "tips": "Start with a small sample and compare retrieval before covering everything.",
            },
        ),
    )
}

STANDARD_TEMPLATE = ("extract", "chunk", "embed", "index", "metadata")


def new_step(step_type: str, settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"id": f"st_{uuid.uuid4().hex[:8]}", "type": step_type, "settings": clean_settings(step_type, settings or {})}


def standard_steps() -> list[dict[str, Any]]:
    return [new_step(step_type) for step_type in STANDARD_TEMPLATE]


def clean_settings(step_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for spec in CATALOG[step_type].fields:
        value = raw.get(spec["key"], spec["default"])
        kind = spec["type"]
        if kind == "number":
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = spec["default"]
            value = max(spec["min"], min(spec["max"], value))
        elif kind == "toggle":
            value = value in (True, "true", "on", "1", 1)
        elif kind == "select":
            value = value if value in {option["value"] for option in spec["options"]} else spec["default"]
        elif kind == "model":
            value = str(value or "") or providers.default_llm()
        else:
            value = str(value) if value is not None else spec["default"]
        cleaned[spec["key"]] = value
    return cleaned


def summary(step: dict[str, Any]) -> str:
    settings, kind = step["settings"], step["type"]
    if kind == "chunk":
        scope = "per page" if settings["within_pages"] else "across pages"
        details = f", {settings['detail_size']}-token details" if settings["detail_size"] else ""
        return f"Sentence-aware, up to {settings['size']} tokens, {settings['overlap']} overlap, {scope}{details}"
    if kind == "embed":
        label = next((m["label"] for m in EMBED_MODELS if m["id"] == settings["model"]), settings["model"])
        return f"Local: {label}"
    if kind in ("abstract", "sentiment", "questions"):
        model = settings["llm"].replace(":", " ", 1) if settings["llm"] else "no model selected"
        return f"{model}, up to {settings['max_tokens']} tokens"
    if kind == "extract" and settings["engine"] == "llamaparse":
        return f"PDFs through LlamaParse ({settings['tier'].replace('_', ' ')}), other files locally"
    if kind == "extract":
        return f"{CATALOG[kind].blurb}. Audio: Whisper {settings['speech_model']}"
    return CATALOG[kind].blurb


def needs_setup(step: dict[str, Any]) -> bool:
    if step["type"] == "extract":
        return step["settings"]["engine"] == "llamaparse" and not llamaparse.configured()
    return CATALOG[step["type"]].costly and not providers.is_ready(step["settings"].get("llm", ""))


def order_problems(types: list[str]) -> list[tuple[int, str]]:
    problems = []
    for index, step_type in enumerate(types):
        definition = CATALOG[step_type]
        if definition.first and index != 0:
            problems.append((index, f"{definition.label} must be the first step."))
        if definition.once and step_type in types[:index]:
            problems.append((index, f"{definition.label} can only be used once."))
        for required in definition.requires:
            if required not in types[:index]:
                problems.append((index, f"{definition.label} needs {CATALOG[required].label} earlier in the pipeline."))
    return problems


def valid_slots(steps: list[dict[str, Any]], step_type: str, moving_id: Optional[str] = None) -> list[int]:
    rest = [step["type"] for step in steps if step["id"] != moving_id]
    return [k for k in range(len(rest) + 1) if not order_problems(rest[:k] + [step_type] + rest[k:])]


def validate(steps: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    problems: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    types = [step["type"] for step in steps]
    for index, message in order_problems(types):
        problems.append({"step_id": steps[index]["id"], "message": message})
    for definition in CATALOG.values():
        if definition.required and definition.type not in types:
            problems.append({"step_id": None, "message": f"{definition.label} is required in every pipeline."})
    for step in steps:
        label = CATALOG[step["type"]].label
        if step["type"] == "chunk" and step["settings"]["overlap"] >= step["settings"]["size"]:
            problems.append({"step_id": step["id"], "message": "Overlap must be smaller than the chunk size."})
        if needs_setup(step):
            need = "LLAMA_CLOUD_API_KEY in .env" if step["type"] == "extract" else "a model"
            problems.append({"step_id": step["id"], "message": f"{label} needs {need} before it can run."})
    chunk = next((s for s in steps if s["type"] == "chunk"), None)
    embed = next((s for s in steps if s["type"] == "embed"), None)
    if chunk and embed:
        limit, detail = limit_for(embed["settings"]["model"]), chunk["settings"]["detail_size"]
        if chunk["settings"]["size"] > limit and (detail == 0 or detail > limit):
            warnings.append(
                {
                    "step_id": embed["id"],
                    "message": f"Text above {limit} tokens is cut off by this embedding model. Turn on detail passages of {limit} tokens or fewer, or use a smaller chunk size.",
                }
            )
    return {"problems": problems, "warnings": warnings}


def rules_for(steps: list[dict[str, Any]], step: dict[str, Any]) -> list[dict[str, Any]]:
    definition = CATALOG[step["type"]]
    index = steps.index(step)
    earlier = {s["type"] for s in steps[:index]}
    items = [{"ok": True, "text": "Required in every pipeline" if definition.required else "Optional"}]
    for required in definition.requires:
        items.append({"ok": required in earlier, "text": f"Needs {CATALOG[required].label} earlier in the pipeline"})
    for other in CATALOG.values():
        if step["type"] in other.requires and other.required:
            later = {s["type"] for s in steps[index + 1 :]}
            items.append({"ok": other.type in later, "text": f"{other.label} needs it earlier in the pipeline"})
    if definition.once:
        items.append({"ok": sum(1 for s in steps if s["type"] == step["type"]) == 1, "text": "Can be used once per pipeline"})
    if definition.first:
        items.append({"ok": index == 0, "text": "Must be the first step"})
    return items


def describe(steps: list[dict[str, Any]]) -> dict[str, Any]:
    present = {step["type"] for step in steps}
    described_steps = [
        {
            **step,
            "label": CATALOG[step["type"]].label,
            "category": CATALOG[step["type"]].category,
            "icon": CATALOG[step["type"]].icon,
            "required": CATALOG[step["type"]].required,
            "costly": CATALOG[step["type"]].costly,
            "summary": summary(step),
            "needs_setup": needs_setup(step),
            "slots": valid_slots(steps, step["type"], step["id"]),
            "rules": rules_for(steps, step),
        }
        for step in steps
    ]
    entries = []
    for definition in CATALOG.values():
        slots = valid_slots(steps, definition.type)
        if definition.once and definition.type in present:
            state, reason, add_at = "in_pipeline", "", None
        elif not slots:
            attempt = order_problems([s["type"] for s in steps] + [definition.type])
            state, reason, add_at = "unavailable", next((message for _, message in attempt), "Not available here."), None
        else:
            state, reason, add_at = "available", "", slots[-1]
        entries.append(
            {
                "type": definition.type,
                "label": definition.label,
                "category": definition.category,
                "icon": definition.icon,
                "blurb": definition.blurb,
                "costly": definition.costly,
                "required": definition.required,
                "state": state,
                "reason": reason,
                "add_at": add_at,
                "at_end": add_at == len(steps),
            }
        )
    return {"steps": described_steps, "catalog": entries, **validate(steps)}
