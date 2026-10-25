import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Optional

from .catalog import CATALOG
from .steps import RUNNERS, Context

Listener = Callable[["StepReport"], None]
log = logging.getLogger("cairn.ingest")


@dataclass
class StepReport:
    position: int
    step_id: str
    type: str
    label: str
    status: str
    duration_ms: Optional[int] = None
    summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_pipeline(
    steps: list[dict[str, Any]],
    ctx: Context,
    include_costly: Optional[Iterable[str]] = None,
    listener: Optional[Listener] = None,
) -> list[StepReport]:
    allowed = None if include_costly is None else set(include_costly)
    notify = listener or (lambda report: None)
    reports: list[StepReport] = []
    for position, step in enumerate(steps):
        definition = CATALOG[step["type"]]
        report = StepReport(position, step["id"], step["type"], definition.label, "pending")
        reports.append(report)
        if definition.costly and allowed is not None and step["id"] not in allowed:
            report.status, report.summary = "skipped", "Skipped. This step is costly and was not selected to run."
            notify(report)
            continue
        report.status = "running"
        notify(report)
        started = time.perf_counter()
        try:
            output = RUNNERS[step["type"]](ctx, step["settings"])
            report.status, report.summary, report.detail = "done", output.summary, output.detail
        except Exception as error:
            log.warning("%s failed: %s", definition.label, error, exc_info=not isinstance(error, (ValueError, OSError)))
            report.status, report.error, report.summary = "failed", str(error) or error.__class__.__name__, "Failed"
        report.duration_ms = round((time.perf_counter() - started) * 1000)
        notify(report)
        if report.status == "failed" and definition.required:
            for offset, later in enumerate(steps[position + 1 :], start=position + 1):
                skipped = StepReport(offset, later["id"], later["type"], CATALOG[later["type"]].label, "skipped")
                skipped.summary = "Not run because an earlier required step failed."
                reports.append(skipped)
                notify(skipped)
            break
    return reports
