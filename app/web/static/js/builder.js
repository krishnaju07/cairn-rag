let drag = null;

const hint = (text) => {
  const node = document.getElementById("builder-hint");
  if (node) node.textContent = text ?? node.dataset.default;
};

const reset = () => {
  if (!drag) return;
  drag.row.classList.remove("dragging");
  drag.steps.classList.remove("dragging-active");
  drag.steps.querySelectorAll(".step-gap").forEach((gap) => gap.classList.remove("valid", "over"));
  hint();
  drag = null;
};

const gapFor = (target, clientY) => {
  const direct = target.closest?.(".step-gap.valid");
  if (direct) return direct;
  const row = target.closest?.(".step");
  if (!row || row === drag.row) return null;
  const bounds = row.getBoundingClientRect();
  const index = Number(row.dataset.index);
  const gap = clientY < bounds.top + bounds.height / 2 ? index : index + 1;
  const candidate = drag.steps.querySelector(`.step-gap[data-gap="${gap}"]`);
  return candidate?.classList.contains("valid") ? candidate : null;
};

document.addEventListener("dragstart", (event) => {
  const row = event.target.closest?.(".step[draggable]");
  if (!row) return;
  const steps = row.closest("#steps");
  const index = Number(row.dataset.index);
  const slots = row.dataset.slots ? row.dataset.slots.split(",").map(Number) : [];
  const gaps = slots.filter((slot) => slot !== index).map((slot) => (slot < index ? slot : slot + 1));
  drag = { row, index, steps, gaps };
  event.dataTransfer.effectAllowed = "move";
  event.dataTransfer.setData("text/plain", row.dataset.id);
  requestAnimationFrame(() => {
    if (!drag) return;
    row.classList.add("dragging");
    steps.classList.add("dragging-active");
    gaps.forEach((gap) => steps.querySelector(`.step-gap[data-gap="${gap}"]`)?.classList.add("valid"));
    hint(
      gaps.length
        ? `Drop ${row.dataset.label} in one of the ${gaps.length} highlighted place${gaps.length === 1 ? "" : "s"}.`
        : `${row.dataset.label} only works where it is, so it cannot move.`,
    );
  });
});

document.addEventListener("dragover", (event) => {
  if (!drag) return;
  const gap = gapFor(event.target, event.clientY);
  drag.steps.querySelectorAll(".step-gap.over").forEach((node) => node !== gap && node.classList.remove("over"));
  if (gap) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    gap.classList.add("over");
  }
});

document.addEventListener("drop", (event) => {
  if (!drag) return;
  const gap = gapFor(event.target, event.clientY);
  if (!gap) return reset();
  event.preventDefault();
  const position = Number(gap.dataset.gap);
  const to = position <= drag.index ? position : position - 1;
  const url = `/pipelines/${drag.steps.dataset.pipeline}/steps/${drag.row.dataset.id}/move`;
  reset();
  window.htmx.ajax("POST", url, { target: "#builder", swap: "innerHTML", values: { to } });
});

document.addEventListener("dragend", reset);
