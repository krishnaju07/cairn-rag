import { openDrawer } from "./app.js";
import { element, highlightSources, iconButton, renderAnswer, sourcesBlock, svg } from "./chat-render.js";

const PIN_DISTANCE = 90;
const HISTORY_ROWS = 12;
const sourceUrl = (citation) =>
  `/sources/${citation.document_id}?page=${citation.page_start}&chunk=${citation.chunk_id}&n=${citation.n}` +
  `&found=${encodeURIComponent(citation.found_by || "")}&focus=${(citation.focus || []).map((span) => span.join("-")).join(",")}`;

const initialize = () => {
  const chat = document.getElementById("chat");
  if (!chat || chat.dataset.bound) return;
  chat.dataset.bound = "1";

  const id = chat.dataset.collection;
  const messages = document.getElementById("messages");
  const form = document.getElementById("composer");
  const input = document.getElementById("question");
  const send = form.querySelector(".send");
  const mode = document.getElementById("mode");
  const jump = document.getElementById("jump");
  const reset = document.getElementById("new-chat");
  const sinceKey = `cairn.chat.since.${id}`;
  const modeKey = `cairn.chat.mode.${id}`;
  const read = (key) => {
    try {
      return localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  };
  const write = (key, value) => {
    try {
      localStorage.setItem(key, value);
    } catch (error) {
      return;
    }
  };

  let controller = null;
  let lastQuestion = "";

  const pinned = () => messages.scrollHeight - messages.scrollTop - messages.clientHeight < PIN_DISTANCE;
  const scrollDown = (force = false, smooth = false) => {
    if (force || pinned()) messages.scrollTo({ top: messages.scrollHeight, behavior: smooth ? "smooth" : "instant" });
    jump.hidden = pinned();
  };
  const resize = () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  };
  const setBusy = (busy) => {
    send.classList.toggle("stop", busy);
    send.setAttribute("aria-label", busy ? "Stop" : "Send");
    send.innerHTML = svg(busy ? "stop" : "send", busy ? 16 : 18);
    input.disabled = false;
  };

  const open = (message) => (citation) => {
    highlightSources(message, citation.n);
    openDrawer("Source", true);
    window.htmx.ajax("GET", sourceUrl(citation), { target: "#drawer-body", swap: "innerHTML" });
  };

  const addUser = (text) => {
    document.getElementById("chat-empty")?.remove();
    const message = element("div", "msg msg-user");
    message.append(element("div", "bubble", text));
    messages.append(message);
    return message;
  };

  const addAssistant = () => {
    const message = element("div", "msg");
    const status = element("div", "msg-status");
    const answer = element("div", "answer");
    message.append(status, answer);
    messages.append(message);
    return { message, status, answer };
  };

  const copyButton = (answer) =>
    iconButton("copy", "Copy answer", (button) => {
      navigator.clipboard.writeText(answer.innerText).then(() => {
        button.innerHTML = svg("check");
        setTimeout(() => (button.innerHTML = svg("copy")), 1500);
      });
    });

  const actions = (refs, result, question, toggle, note) => {
    const bar = element("div", "msg-actions");
    const meta = [note, result.model && result.model.replace(":", " · "), result.model && result.latency_ms && `${(result.latency_ms / 1000).toFixed(1)} s`];
    bar.append(copyButton(refs.answer), iconButton("retry", "Ask again", () => ask(question)));
    if (toggle) bar.append(toggle);
    bar.append(element("span", "dim", meta.filter(Boolean).join(" · ")));
    refs.message.append(bar);
  };

  const finish = (refs, result, question) => {
    refs.status.textContent = "";
    refs.message.querySelector(".typing")?.remove();
    const opener = open(refs.message);
    let sources = null;
    if (result.answered) {
      renderAnswer(refs.answer, result.answer, result.citations, opener);
      if (result.citations.length) sources = sourcesBlock(result.citations, `Sources · ${result.citations.length}`, opener);
    } else {
      refs.answer.className = "answer refused";
      refs.answer.replaceChildren(element("strong", "", result.answer), element("p", "dim", "Ask about specific details in your documents, or try “Summarize this document”."));
      if (result.closest?.length) sources = sourcesBlock(result.closest, "Closest passages", opener);
    }
    actions(refs, result, question, sources?.toggle);
    if (sources) refs.message.append(sources.row);
  };

  const restore = () => {
    let rows = [];
    try {
      rows = JSON.parse(chat.dataset.history);
    } catch (error) {
      rows = [];
    }
    const since = Number(read(sinceKey) || 0);
    const shown = rows.filter((row) => row.created_at > since).slice(-HISTORY_ROWS);
    shown.forEach((row) => {
      addUser(row.question);
      const refs = addAssistant();
      finish(
        refs,
        {
          answered: Boolean(row.answered),
          answer: row.answer,
          citations: row.answered ? row.citations : [],
          closest: row.answered ? [] : row.citations,
          model: row.model,
          latency_ms: row.latency_ms,
        },
        row.question,
      );
    });
    reset.hidden = shown.length === 0;
    if (shown.length) scrollDown(true);
  };

  const ask = async (question) => {
    if (controller || !question.trim()) return;
    lastQuestion = question;
    controller = new AbortController();
    setBusy(true);
    reset.hidden = false;
    addUser(question);
    const refs = addAssistant();
    const typing = element("div", "typing");
    typing.innerHTML = "<span></span><span></span><span></span>";
    refs.message.append(typing);
    refs.status.textContent = "Searching your documents…";
    scrollDown(true);

    let raw = "";
    let passages = [];
    const opener = open(refs.message);
    try {
      const response = await fetch(`/api/collections/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, mode: mode.value }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error((await response.json()).error || `Request failed (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop();
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue;
          const event = JSON.parse(frame.slice(6));
          if (event.type === "sources") {
            passages = event.citations;
            refs.status.textContent = `Reading ${passages.length} passage${passages.length === 1 ? "" : "s"}…`;
          } else if (event.type === "delta") {
            raw += event.text;
            refs.status.textContent = "Writing the answer…";
            renderAnswer(refs.answer, raw, passages, opener);
          } else if (event.type === "reset") {
            raw = "";
            refs.status.textContent = "Trying again…";
          } else if (event.type === "error") {
            throw new Error(event.message);
          } else if (event.type === "done") {
            finish(refs, event.result, question);
          }
          scrollDown();
        }
      }
    } catch (error) {
      refs.message.querySelector(".typing")?.remove();
      refs.status.textContent = "";
      if (error.name === "AbortError") {
        actions(refs, {}, question, null, "Stopped");
      } else {
        refs.answer.className = "callout callout-error";
        refs.answer.textContent = error.message;
      }
    } finally {
      controller = null;
      setBusy(false);
      input.focus();
      scrollDown();
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (controller) {
      controller.abort();
      return;
    }
    const question = input.value;
    input.value = "";
    resize();
    ask(question);
  });
  form.addEventListener("mousedown", (event) => {
    if (!event.target.closest("button, select, label")) {
      event.preventDefault();
      input.focus();
    }
  });
  input.addEventListener("input", resize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    } else if (event.key === "ArrowUp" && !input.value && lastQuestion) {
      event.preventDefault();
      input.value = lastQuestion;
      resize();
    }
  });
  document.addEventListener("keydown", (event) => {
    const typing = /input|textarea|select/i.test(document.activeElement?.tagName || "");
    if (event.key === "/" && !typing && document.getElementById("chat")) {
      event.preventDefault();
      input.focus();
    }
  });
  messages.addEventListener("scroll", () => (jump.hidden = pinned()));
  jump.addEventListener("click", () => scrollDown(true, true));
  mode.value = read(modeKey) || chat.dataset.mode;
  mode.addEventListener("change", () => write(modeKey, mode.value));
  reset.addEventListener("click", () => {
    write(sinceKey, String(Date.now() / 1000));
    messages.replaceChildren(...document.getElementById("chat-empty-template").content.cloneNode(true).children);
    bindSuggestions();
    reset.hidden = true;
  });

  const bindSuggestions = () =>
    chat.querySelectorAll("[data-ask]").forEach((button) => button.addEventListener("click", () => ask(button.textContent)));
  bindSuggestions();
  restore();
  input.focus();
};

document.addEventListener("htmx:load", initialize);
document.addEventListener("DOMContentLoaded", initialize);
