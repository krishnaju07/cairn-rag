import uuid
from typing import Any, Optional

from .. import db
from . import catalog, estimate


class PipelineError(Exception):
    pass


def _fingerprint(steps: list[dict[str, Any]]) -> str:
    return db.dumps([{"type": s["type"], "settings": s["settings"]} for s in steps])


def _load(raw: Optional[str]) -> list[dict[str, Any]]:
    steps = db.loads(raw, [])
    return [{**step, "settings": catalog.clean_settings(step["type"], step["settings"])} for step in steps]


def _row(pipeline_id: str) -> dict[str, Any]:
    row = db.one("SELECT * FROM pipelines WHERE id = ?", (pipeline_id,))
    if not row:
        raise PipelineError("Pipeline not found.")
    return row


TEMPLATES = {
    "standard": (
        "Standard document processing",
        "Extract text, chunk, embed locally (no API key needed), index and extract metadata.",
    ),
    "required": (
        "Start from the required steps",
        "Only the four steps every searchable collection needs. Add the rest yourself.",
    ),
    "copy": ("Copy an existing pipeline", "Start from one of your pipelines, without the collections that use it."),
    "scratch": ("Empty pipeline", "Start with no steps and add each one where it works."),
}


def template_steps(template: str, source_id: Optional[str] = None) -> list[dict[str, Any]]:
    if template == "standard":
        return catalog.standard_steps()
    if template == "required":
        return [catalog.new_step(step_type) for step_type in catalog.STANDARD_TEMPLATE if catalog.CATALOG[step_type].required]
    if template == "copy" and source_id:
        return [catalog.new_step(step["type"], step["settings"]) for step in draft_steps(source_id)]
    return []


def create(name: str, template: str, description: str = "", source_id: Optional[str] = None) -> dict[str, Any]:
    steps = template_steps(template, source_id)
    pipeline_id = db.new_id("pl")
    stamp = db.now()
    db.run(
        "INSERT INTO pipelines (id, name, description, draft, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (pipeline_id, name.strip() or "Untitled pipeline", description, db.dumps(steps), stamp, stamp),
    )
    return get(pipeline_id)


def list_all() -> list[dict[str, Any]]:
    items = []
    for row in db.many("SELECT * FROM pipelines ORDER BY updated_at DESC"):
        steps = _load(row["draft"])
        latest = db.one("SELECT MAX(version) AS v FROM pipeline_versions WHERE pipeline_id = ?", (row["id"],))["v"] or 0
        uses = db.one("SELECT COUNT(*) AS n FROM collections WHERE pipeline_id = ?", (row["id"],))["n"]
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "updated_at": row["updated_at"],
                "latest_version": latest,
                "step_count": len(steps),
                "labels": [catalog.CATALOG[s["type"]].label for s in steps],
                "collections": uses,
            }
        )
    return items


def published_versions() -> list[dict[str, Any]]:
    rows = db.many(
        "SELECT p.id, p.name, v.version FROM pipelines p JOIN pipeline_versions v ON v.pipeline_id = p.id "
        "ORDER BY p.name, v.version DESC"
    )
    return rows


def get(pipeline_id: str) -> dict[str, Any]:
    row = _row(pipeline_id)
    steps = _load(row["draft"])
    versions = db.many(
        "SELECT version, steps, created_at FROM pipeline_versions WHERE pipeline_id = ? ORDER BY version DESC", (pipeline_id,)
    )
    latest = versions[0] if versions else None
    published = bool(latest) and _fingerprint(_load(latest["steps"])) == _fingerprint(steps)
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "latest_version": latest["version"] if latest else 0,
        "has_changes": not published,
        "versions": [
            {
                "version": v["version"],
                "created_at": v["created_at"],
                "labels": [catalog.CATALOG[s["type"]].label for s in _load(v["steps"])],
            }
            for v in versions
        ],
        "reference_estimate": estimate.reference(steps),
        **catalog.describe(steps),
    }


def draft_steps(pipeline_id: str) -> list[dict[str, Any]]:
    return _load(_row(pipeline_id)["draft"])


def rename(pipeline_id: str, name: str, description: Optional[str] = None) -> dict[str, Any]:
    row = _row(pipeline_id)
    db.run(
        "UPDATE pipelines SET name = ?, description = ?, updated_at = ? WHERE id = ?",
        (name.strip() or row["name"], row["description"] if description is None else description, db.now(), pipeline_id),
    )
    return get(pipeline_id)


def save_draft(pipeline_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    _row(pipeline_id)
    cleaned = []
    for step in steps:
        if step.get("type") not in catalog.CATALOG:
            raise PipelineError(f"Unknown step type '{step.get('type')}'.")
        cleaned.append(
            {
                "id": step.get("id") or f"st_{uuid.uuid4().hex[:8]}",
                "type": step["type"],
                "settings": catalog.clean_settings(step["type"], step.get("settings") or {}),
            }
        )
    db.run("UPDATE pipelines SET draft = ?, updated_at = ? WHERE id = ?", (db.dumps(cleaned), db.now(), pipeline_id))
    return get(pipeline_id)


def publish(pipeline_id: str) -> dict[str, Any]:
    row = _row(pipeline_id)
    steps = _load(row["draft"])
    problems = catalog.validate(steps)["problems"]
    if problems:
        raise PipelineError(problems[0]["message"])
    latest = db.one(
        "SELECT version, steps FROM pipeline_versions WHERE pipeline_id = ? ORDER BY version DESC LIMIT 1", (pipeline_id,)
    )
    if latest and _fingerprint(_load(latest["steps"])) == _fingerprint(steps):
        raise PipelineError(f"No changes since v{latest['version']}.")
    version = (latest["version"] if latest else 0) + 1
    db.run(
        "INSERT INTO pipeline_versions (id, pipeline_id, version, steps, created_at) VALUES (?, ?, ?, ?, ?)",
        (db.new_id("pv"), pipeline_id, version, db.dumps(steps), db.now()),
    )
    return get(pipeline_id)


def restore(pipeline_id: str, version: int) -> dict[str, Any]:
    _row(pipeline_id)
    stored = db.one("SELECT steps FROM pipeline_versions WHERE pipeline_id = ? AND version = ?", (pipeline_id, version))
    if not stored:
        raise PipelineError(f"Version {version} does not exist.")
    db.run("UPDATE pipelines SET draft = ?, updated_at = ? WHERE id = ?", (stored["steps"], db.now(), pipeline_id))
    return get(pipeline_id)


def version_steps(pipeline_id: str, version: int) -> list[dict[str, Any]]:
    stored = db.one("SELECT steps FROM pipeline_versions WHERE pipeline_id = ? AND version = ?", (pipeline_id, version))
    if not stored:
        raise PipelineError(f"Version {version} does not exist.")
    return _load(stored["steps"])


def delete(pipeline_id: str) -> None:
    _row(pipeline_id)
    if db.one("SELECT 1 AS x FROM collections WHERE pipeline_id = ?", (pipeline_id,)):
        raise PipelineError("This pipeline is used by a collection and cannot be deleted.")
    db.run("DELETE FROM pipelines WHERE id = ?", (pipeline_id,))
