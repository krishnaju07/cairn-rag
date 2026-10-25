const TOKEN = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[\d+\])/g;
const CITATION = /^\[\d+\]$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

const ICONS = {
  copy: '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  retry: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>',
  send: '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
};

export const element = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

export const svg = (name, size = 16) =>
  `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

export const iconButton = (name, label, onClick) => {
  const button = element("button", "icon-btn");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.innerHTML = svg(name);
  button.addEventListener("click", () => onClick(button));
  return button;
};

const extension = (filename) => (filename.includes(".") ? filename.split(".").pop().toLowerCase() : "txt");
const shortName = (filename) => filename.replace(/\.[^.]+$/, "").replace(/_+/g, " ");

const preview = (() => {
  let node = null;
  return {
    show(target, citation) {
      this.hide();
      node = element("div", "cite-pop");
      node.append(
        element("div", "cite-pop-head", `${citation.filename} · ${citation.label}`),
        element("p", "", citation.snippet || citation.text.slice(0, 240)),
        element("div", "cite-pop-foot", `${citation.found_by || "Related passage"} · click to open the page`),
      );
      document.body.append(node);
      const box = target.getBoundingClientRect();
      const width = node.offsetWidth;
      const left = Math.min(Math.max(box.left + box.width / 2 - width / 2, 12), window.innerWidth - width - 12);
      const above = box.top > node.offsetHeight + 24;
      node.style.left = `${left}px`;
      node.style.top = `${above ? box.top - node.offsetHeight - 10 : box.bottom + 10}px`;
      node.classList.add("show");
    },
    hide() {
      node?.remove();
      node = null;
    },
  };
})();

document.addEventListener("mouseover", (event) => {
  if (!(event.target instanceof Element) || !event.target.closest(".cite")) preview.hide();
});
document.addEventListener("scroll", () => preview.hide(), true);

const withPreview = (target, citation) => {
  target.addEventListener("mouseenter", () => preview.show(target, citation));
  target.addEventListener("mouseleave", () => preview.hide());
  target.addEventListener("focus", () => preview.show(target, citation));
  target.addEventListener("blur", () => preview.hide());
  target.addEventListener("click", () => preview.hide());
};

const chip = (number, citation, open) => {
  const button = element("button", "cite");
  button.type = "button";
  button.dataset.n = number;
  button.setAttribute("aria-label", `Source ${number}: ${citation.filename}, ${citation.label}`);
  button.append(element("span", "cite-n", number), element("span", "cite-name", shortName(citation.filename)));
  button.addEventListener("click", () => open(citation));
  withPreview(button, citation);
  return button;
};

const tokens = (text) => text.split(TOKEN).filter(Boolean);

const collapse = (rows) => {
  const cited = rows.flatMap((row, r) => row.flatMap((part, p) => (CITATION.test(part) ? [{ r, p, part }] : [])));
  const repeats = new Set(cited.filter((cite, index) => cited[index + 1]?.part === cite.part).map(({ r, p }) => `${r}:${p}`));
  return rows.map((row, r) => row.filter((_, p) => !repeats.has(`${r}:${p}`)));
};

const paragraphs = (row) => {
  const groups = [[]];
  for (const part of row) {
    groups.at(-1).push(part);
    if (CITATION.test(part) && groups.at(-1).some((item) => !CITATION.test(item) && item.trim())) groups.push([]);
  }
  return groups.filter((group) => group.some((item) => item.trim()));
};

const fill = (parent, parts, citations, open) => {
  for (const part of parts) {
    if (part.startsWith("**")) parent.append(element("strong", "", part.slice(2, -2)));
    else if (part.startsWith("`")) parent.append(element("code", "", part.slice(1, -1)));
    else if (CITATION.test(part)) {
      const number = part.slice(1, -1);
      const citation = citations[Number(number) - 1];
      parent.append(citation ? chip(number, citation, open) : part);
    } else parent.append(part);
  }
};

const parseBlocks = (text) => {
  const blocks = [];
  let current = null;
  for (const line of text.split("\n")) {
    const bullet = line.match(BULLET);
    const numbered = line.match(NUMBERED);
    const kind = bullet ? "ul" : numbered ? "ol" : "p";
    if (!line.trim()) {
      current = null;
    } else if (kind === "p") {
      if (current?.kind === "p") current.items.push(line.trim());
      else blocks.push((current = { kind, items: [line.trim()] }));
    } else {
      if (current?.kind !== kind) blocks.push((current = { kind, items: [] }));
      current.items.push((bullet || numbered)[1]);
    }
  }
  return blocks;
};

export const renderAnswer = (container, text, citations, open) => {
  container.replaceChildren();
  for (const block of parseBlocks(text)) {
    const rows = collapse(block.items.map(tokens));
    if (block.kind === "p") {
      rows.flatMap(paragraphs).forEach((group) => {
        const paragraph = element("p");
        fill(paragraph, group, citations, open);
        container.append(paragraph);
      });
    } else {
      const list = element(block.kind);
      rows.forEach((row) => {
        const item = element("li");
        fill(item, row, citations, open);
        list.append(item);
      });
      container.append(list);
    }
  }
};

export const sourceCard = (citation, open) => {
  const card = element("button", "source-card");
  card.type = "button";
  card.dataset.n = citation.n;
  const body = element("div", "source-body");
  const head = element("div", "source-head");
  head.append(
    element("span", "num", String(citation.n)),
    element("span", `file-badge sm file-${extension(citation.filename)}`, extension(citation.filename).toUpperCase()),
    element("span", "name", citation.filename),
  );
  body.append(
    head,
    element("div", "where", [citation.label, citation.found_by].filter(Boolean).join(" · ")),
    element("p", "snippet-line", citation.snippet || citation.text.slice(0, 200)),
  );
  card.append(body);
  card.addEventListener("click", () => open(citation));
  return card;
};

export const sourcesBlock = (citations, label, open) => {
  const row = element("div", "sources-row");
  row.hidden = true;
  citations.forEach((citation) => row.append(sourceCard(citation, open)));
  const toggle = element("button", "chip-btn");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  toggle.append(element("span", "", label));
  toggle.insertAdjacentHTML("beforeend", svg("chevron", 14));
  toggle.addEventListener("click", () => {
    row.hidden = !row.hidden;
    toggle.setAttribute("aria-expanded", String(!row.hidden));
    if (!row.hidden) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
  return { row, toggle };
};

export const highlightSources = (message, number) => {
  message.querySelectorAll(".cite, .source-card").forEach((node) => node.classList.toggle("active", node.dataset.n === String(number)));
};
