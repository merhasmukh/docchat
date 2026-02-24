// ─── CSRF helper (reads Django's csrftoken cookie) ───────────────────────────
function getCookie(name) {
  const val = document.cookie.split("; ").find(r => r.startsWith(name + "="));
  return val ? decodeURIComponent(val.split("=")[1]) : null;
}

// ─── Markdown config ──────────────────────────────────────────────────────────
marked.setOptions({ breaks: true, gfm: true });

function renderMarkdown(raw) {
  const html = marked.parse(raw);
  // Wrap <table> in a scroll container — works regardless of marked version
  const wrapped = html
    .replace(/<table>/g, '<div class="table-scroll"><table>')
    .replace(/<\/table>/g, "</table></div>");
  return DOMPurify.sanitize(wrapped);
}

// ─── DOM refs ────────────────────────────────────────────────────────────────
const fileInput     = document.getElementById("file-input");
const uploadBtn     = document.getElementById("upload-btn");
const dropZone      = document.getElementById("drop-zone");
const uploadSection = document.getElementById("upload-section");
const uploadStatus  = document.getElementById("upload-status");
const uploadMessage = document.getElementById("upload-message");
const uploadError   = document.getElementById("upload-error");
const errorMessage  = document.getElementById("error-message");
const retryBtn      = document.getElementById("retry-btn");
const chatSection   = document.getElementById("chat-section");
const chatWindow    = document.getElementById("chat-window");
const questionInput = document.getElementById("question-input");
const sendBtn       = document.getElementById("send-btn");
const resetBtn      = document.getElementById("reset-btn");
const docIndicator  = document.getElementById("doc-indicator");

// ─── Page load: restore state if session has a document loaded ────────────────
(async function checkStatus() {
  try {
    const res = await fetch("/status/");
    const data = await res.json();
    if (data.document_loaded) showChatMode(data.filename);
  } catch (_) {}
})();

// ─── Upload interactions ──────────────────────────────────────────────────────
uploadBtn.addEventListener("click", () => fileInput.click());

dropZone.addEventListener("dragover", (e) => {
  e.preventDefault();
  dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragover"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragover");
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});
fileInput.addEventListener("change", () => {
  if (fileInput.files.length > 0) uploadFile(fileInput.files[0]);
});
retryBtn.addEventListener("click", () => {
  uploadError.classList.add("hidden");
  dropZone.classList.remove("hidden");
  fileInput.value = "";
});

// ─── Upload logic ─────────────────────────────────────────────────────────────
async function uploadFile(file) {
  if (file.size > 50 * 1024 * 1024) {
    showUploadError("File too large. Maximum size is 50 MB.");
    return;
  }
  dropZone.classList.add("hidden");
  uploadError.classList.add("hidden");
  uploadStatus.classList.remove("hidden");
  uploadMessage.textContent = `Processing "${file.name}"… this may take a minute.`;

  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch("/upload/", {
      method: "POST",
      headers: { "X-CSRFToken": getCookie("csrftoken") },
      body: formData,
    });
    const data = await res.json();
    if (data.status === "ok") showChatMode(data.filename);
    else showUploadError(data.message || "Upload failed.");
  } catch (err) {
    showUploadError("Network error: " + err.message);
  }
}

function showUploadError(msg) {
  uploadStatus.classList.add("hidden");
  dropZone.classList.add("hidden");
  errorMessage.textContent = msg;
  uploadError.classList.remove("hidden");
}

// ─── Mode switching ───────────────────────────────────────────────────────────
function showChatMode(filename) {
  uploadSection.classList.add("hidden");
  chatSection.classList.remove("hidden");
  resetBtn.classList.remove("hidden");
  docIndicator.textContent = `Document: ${filename}`;
  questionInput.focus();
}

function showUploadMode() {
  chatSection.classList.add("hidden");
  chatWindow.innerHTML = '<div class="welcome-msg">Document loaded. Ask me anything about it.</div>';
  uploadSection.classList.remove("hidden");
  uploadStatus.classList.add("hidden");
  uploadError.classList.add("hidden");
  dropZone.classList.remove("hidden");
  resetBtn.classList.add("hidden");
  docIndicator.textContent = "No document loaded";
  fileInput.value = "";
}

// ─── Reset ────────────────────────────────────────────────────────────────────
resetBtn.addEventListener("click", async () => {
  try {
    await fetch("/reset/", {
      method: "POST",
      headers: { "X-CSRFToken": getCookie("csrftoken") },
    });
  } catch (_) {}
  showUploadMode();
});

// ─── Chat ─────────────────────────────────────────────────────────────────────
sendBtn.addEventListener("click", sendMessage);
questionInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

function sendMessage() {
  const question = questionInput.value.trim();
  if (!question || sendBtn.disabled) return;

  appendUserBubble(question);
  questionInput.value = "";
  setInputEnabled(false);

  const asmBubble = appendAssistantBubble();
  streamChat(question, asmBubble);
}

// ─── Streaming chat via fetch + ReadableStream ────────────────────────────────
async function streamChat(question, bubble) {
  let rawText = "";

  try {
    const res = await fetch("/chat/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
      },
      body: JSON.stringify({ question }),
    });

    if (!res.ok) {
      let msg = "Request failed.";
      try { msg = (await res.json()).message || msg; } catch (_) {}
      setAssistantContent(bubble, `**Error:** ${msg}`);
      bubble.classList.remove("streaming");
      setInputEnabled(true);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const token = line.slice(6);

        if (token === "[DONE]") {
          // Final render without the blinking cursor
          setAssistantContent(bubble, rawText);
          bubble.classList.remove("streaming");
          setInputEnabled(true);
          scrollToBottom();
          return;
        }
        if (token.startsWith("[ERROR:")) {
          setAssistantContent(bubble, `**Error:** ${token.slice(7, -1)}`);
          bubble.classList.remove("streaming");
          setInputEnabled(true);
          scrollToBottom();
          return;
        }

        // Unescape newlines encoded by the server, accumulate
        rawText += token.replace(/\\n/g, "\n");
        // Re-render markdown as tokens arrive
        setAssistantContent(bubble, rawText);
        scrollToBottom();
      }
    }
  } catch (err) {
    setAssistantContent(bubble, `**Stream error:** ${err.message}`);
    bubble.classList.remove("streaming");
  } finally {
    setInputEnabled(true);
  }
}

// ─── Bubble builders ──────────────────────────────────────────────────────────
function appendUserBubble(text) {
  removeWelcome();
  const div = document.createElement("div");
  div.className = "bubble user";
  div.textContent = text;   // user text shown as-is (no markdown)
  chatWindow.appendChild(div);
  scrollToBottom();
}

function appendAssistantBubble() {
  removeWelcome();
  const wrapper = document.createElement("div");
  wrapper.className = "bubble-wrapper";

  const div = document.createElement("div");
  div.className = "bubble assistant streaming";
  // empty until first token arrives

  const actions = document.createElement("div");
  actions.className = "bubble-actions";

  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-btn";
  copyBtn.title = "Copy response";
  copyBtn.innerHTML = "&#x2398;"; // ⎘ copy symbol
  copyBtn.addEventListener("click", () => {
    const text = div.innerText;
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.innerHTML = "&#10003;"; // ✓
      setTimeout(() => { copyBtn.innerHTML = "&#x2398;"; }, 1500);
    });
  });

  actions.appendChild(copyBtn);
  wrapper.appendChild(div);
  wrapper.appendChild(actions);
  chatWindow.appendChild(wrapper);
  scrollToBottom();
  return div;
}

function setAssistantContent(bubble, rawMarkdown) {
  // Wrap in a div so block elements (tables, lists) are scoped
  bubble.innerHTML = `<div class="md-body">${renderMarkdown(rawMarkdown)}</div>`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function removeWelcome() {
  const w = chatWindow.querySelector(".welcome-msg");
  if (w) w.remove();
}

function scrollToBottom() {
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function setInputEnabled(enabled) {
  questionInput.disabled = !enabled;
  sendBtn.disabled = !enabled;
  if (enabled) questionInput.focus();
}
