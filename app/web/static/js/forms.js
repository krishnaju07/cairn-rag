import { toast } from "./app.js";

let pendingFiles = null;

const assign = (input, files) => {
  const transfer = new DataTransfer();
  [...files].forEach((file) => transfer.items.add(file));
  input.files = transfer.files;
  input.dispatchEvent(new Event("change", { bubbles: true }));
};

document.addEventListener("dragover", (event) => {
  const zone = event.target.closest?.("[data-dropzone]");
  if (zone) {
    event.preventDefault();
    zone.classList.add("over");
  }
});

document.addEventListener("dragleave", (event) => event.target.closest?.("[data-dropzone]")?.classList.remove("over"));

document.addEventListener("drop", (event) => {
  const zone = event.target.closest?.("[data-dropzone]");
  if (!zone || !event.dataTransfer?.files?.length) return;
  event.preventDefault();
  zone.classList.remove("over");
  assign(zone.querySelector("input[type=file]"), event.dataTransfer.files);
});

const hasFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
let depth = 0;

document.addEventListener("dragenter", (event) => {
  if (!hasFiles(event) || !document.querySelector("[data-collection-drop]")) return;
  depth += 1;
  document.getElementById("drop-overlay")?.classList.add("show");
});

document.addEventListener("dragleave", (event) => {
  if (!hasFiles(event)) return;
  depth = Math.max(depth - 1, 0);
  if (!depth) document.getElementById("drop-overlay")?.classList.remove("show");
});

document.addEventListener("dragover", (event) => {
  if (hasFiles(event) && document.querySelector("[data-collection-drop]")) event.preventDefault();
});

document.addEventListener("drop", (event) => {
  const page = document.querySelector("[data-collection-drop]");
  depth = 0;
  document.getElementById("drop-overlay")?.classList.remove("show");
  if (!page || !hasFiles(event) || event.target.closest("[data-dropzone]")) return;
  event.preventDefault();
  pendingFiles = event.dataTransfer.files;
  window.htmx.ajax("GET", `/collections/${page.dataset.collectionDrop}/upload`, { target: "#drawer-body", swap: "innerHTML" });
  import("./app.js").then(({ openDrawer }) => openDrawer("Upload", true));
});

document.body.addEventListener("htmx:afterSettle", () => {
  const input = document.getElementById("upload-input");
  if (pendingFiles && input) {
    const files = pendingFiles;
    pendingFiles = null;
    assign(input, files);
  }
});

document.addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    const source = document.querySelector(copy.dataset.copy);
    try {
      await navigator.clipboard.writeText(source.textContent.trim());
      toast("Copied to the clipboard", "success");
    } catch (error) {
      toast("Copy failed. Select the text and copy it manually.", "error");
    }
  }
  const reveal = event.target.closest("[data-reveal]");
  if (reveal) {
    const field = reveal.closest(".input-reveal").querySelector("input");
    field.type = field.type === "password" ? "text" : "password";
  }
  const toggle = event.target.closest("[data-toggle]");
  if (toggle) document.querySelector(toggle.dataset.toggle)?.toggleAttribute("hidden");
  const recommended = event.target.closest("[data-use-recommended]");
  if (recommended) {
    event.preventDefault();
    const form = recommended.closest("form");
    const mode = form.querySelector("input[name=retrieval_mode]:checked").value;
    const range = form.querySelector("input[name=min_relevance]");
    range.value = JSON.parse(recommended.dataset.recommended)[mode];
    form.querySelector("input[name=use_recommended]").value = "1";
    range.dispatchEvent(new Event("input", { bubbles: true }));
    form.querySelector("input[name=use_recommended]").value = "1";
  }
});

document.addEventListener("input", (event) => {
  const range = event.target.closest?.("input[type=range][data-output]");
  if (!range) return;
  const output = document.getElementById(range.dataset.output);
  output.textContent = Number(range.value).toFixed(Number(range.dataset.decimals || 0));
  if (range.name === "min_relevance" && event.isTrusted) range.form.querySelector("input[name=use_recommended]").value = "";
});

document.addEventListener("change", (event) => {
  const radio = event.target.closest?.("form[data-search-settings] input[name=retrieval_mode]");
  if (!radio) return;
  const form = radio.form;
  const link = form.querySelector("[data-use-recommended]");
  const values = JSON.parse(link.dataset.recommended);
  document.getElementById("g-min-rec").textContent = values[radio.value].toFixed(2);
  if (form.querySelector("input[name=use_recommended]").value) link.click();
});
