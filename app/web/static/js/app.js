import "./builder.js";
import "./forms.js";
import "./chat.js";

const drawer = document.getElementById.bind(document, "drawer");
const scrim = document.getElementById.bind(document, "scrim");
const modal = () => document.getElementById("modal");

export function openDrawer(title, wide = false) {
  drawer().classList.toggle("wide", wide);
  document.getElementById("drawer-title").textContent = title;
  drawer().classList.add("open");
  drawer().setAttribute("aria-hidden", "false");
  scrim().classList.add("open");
}

export function closeDrawer() {
  drawer().classList.remove("open");
  drawer().setAttribute("aria-hidden", "true");
  scrim().classList.remove("open");
}

export function openModal() {
  if (!modal().open) modal().showModal();
}

export function closeModal() {
  if (modal().open) modal().close();
}

const ICONS = {
  success: '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/></svg>',
  error: '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/></svg>',
  info: '<svg class="icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>',
};

export function toast(message, kind = "info") {
  const host = document.getElementById("toasts");
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.innerHTML = ICONS[kind] || ICONS.info;
  const text = document.createElement("span");
  text.textContent = message;
  node.append(text);
  host.append(node);
  setTimeout(() => {
    node.classList.add("leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
  }, kind === "error" ? 6000 : 3600);
}

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-close]") || event.target === scrim()) closeDrawer();
  if (event.target.closest("[data-close-modal]") && !event.target.closest("form[hx-post]:not([type=button])")) closeModal();
  if (event.target === modal()) closeModal();
  document.querySelectorAll("details.menu[open]").forEach((menu) => {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  });
});

document.addEventListener("click", (event) => {
  const row = event.target.closest("[data-href]");
  if (row && !event.target.closest("a, button, input, select, summary")) row.querySelector("a[href]")?.click();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

document.body.addEventListener("htmx:beforeRequest", (event) => {
  const trigger = event.detail.elt;
  if (trigger.dataset?.drawer) {
    openDrawer(trigger.dataset.drawer, "wide" in trigger.dataset);
    document.getElementById("drawer-body").innerHTML = '<div class="stack"><div class="skeleton" style="height:18px;width:70%"></div><div class="skeleton" style="height:120px"></div><div class="skeleton" style="height:18px;width:50%"></div></div>';
  }
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail.target;
  if (target.id === "modal-body") openModal();
  if (target.id === "drawer-body") {
    const mark = target.querySelector(".hl, mark");
    if (mark) setTimeout(() => mark.scrollIntoView({ block: "center", behavior: "smooth" }), 250);
  }
});

document.body.addEventListener("htmx:afterRequest", (event) => {
  const trigger = event.detail.elt;
  if (!event.detail.successful) return;
  if (trigger.matches?.("[data-close-drawer]")) closeDrawer();
  if (trigger.matches?.("form[data-close-modal]")) closeModal();
});

document.body.addEventListener("htmx:responseError", async (event) => {
  const { xhr } = event.detail;
  let message = `Request failed (${xhr.status}).`;
  try {
    message = JSON.parse(xhr.responseText).error || message;
  } catch (error) {
    message = xhr.responseText?.slice(0, 160) || message;
  }
  toast(message, "error");
});

document.body.addEventListener("htmx:sendError", () => toast("Could not reach the server. Is it still running?", "error"));

document.addEventListener("toast", (event) => toast(event.detail.message, event.detail.kind));
