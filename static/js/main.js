// ─── Navbar scroll shadow ─────────────────────────────────────────────────────
window.addEventListener("scroll", () => {
  document.getElementById("app-navbar").classList.toggle("scrolled", window.scrollY > 10);
});

// ─── CSRF helper (reads Django's csrftoken cookie) ───────────────────────────
function getCookie(name) {
  const val = document.cookie.split("; ").find(r => r.startsWith(name + "="));
  return val ? decodeURIComponent(val.split("=")[1]) : null;
}

// ─── Session token (localStorage — no Django session dependency) ──────────────
const TOKEN_KEY = "docchat_token";
const getToken  = () => localStorage.getItem(TOKEN_KEY) || "";
const setToken  = (t) => localStorage.setItem(TOKEN_KEY, t);
const clearToken= () => localStorage.removeItem(TOKEN_KEY);

/** Build common headers for every API call. */
function apiHeaders(includeJson = false) {
  const h = { "X-Chat-Token": getToken() };
  if (includeJson) h["Content-Type"] = "application/json";
  return h;
}

// ─── Markdown config ──────────────────────────────────────────────────────────
marked.setOptions({ breaks: true, gfm: true });

function renderMarkdown(raw) {
  const html = marked.parse(raw);
  const wrapped = html
    .replace(/<table>/g, '<div class="table-scroll"><table>')
    .replace(/<\/table>/g, "</table></div>");
  return DOMPurify.sanitize(wrapped);
}

// ─── DOM refs ────────────────────────────────────────────────────────────────
const chatSection     = document.getElementById("chat-section");
const noDocSection    = document.getElementById("no-doc-section");
const chatWindow      = document.getElementById("chat-window");
const questionInput   = document.getElementById("question-input");
const sendBtn         = document.getElementById("send-btn");
const resetBtn        = document.getElementById("reset-btn");
const docIndicator    = document.getElementById("doc-indicator");
const docIndicatorTxt = document.getElementById("doc-indicator-text");

// Modal refs
const userModal      = document.getElementById("user-modal");
const userForm       = document.getElementById("user-form");
const nameInput      = document.getElementById("user-name");
const emailInput     = document.getElementById("user-email");
const modalError     = document.getElementById("modal-error");
const modalSubmitBtn = document.getElementById("modal-submit-btn");

// Track the active document filename
let currentFilename = null;

// ─── Page load: check active document + existing session token ────────────────
(async function checkStatus() {
  let documentLoaded = false;
  let sessionActive  = false;
  try {
    const res  = await fetch("/status/", { headers: apiHeaders() });
    const data = await res.json();
    documentLoaded  = data.document_loaded;
    currentFilename = data.filename;
    sessionActive   = data.session_active;
  } catch (_) {
    // Network error — treat as no document
  }

  if (documentLoaded) {
    if (sessionActive) {
      showChatMode(currentFilename);
      loadHistory();           // re-render previous messages from DB
    } else {
      showUserModal();
    }
  } else {
    showNoDocMode();
  }
})();

// ─── Mode helpers ─────────────────────────────────────────────────────────────
function showChatMode(filename) {
  userModal.classList.add("hidden");
  noDocSection.classList.add("hidden");
  chatSection.classList.remove("hidden");
  resetBtn.classList.remove("hidden");
  docIndicatorTxt.textContent = filename;
  docIndicator.classList.add("loaded");
  questionInput.focus();
}

function showNoDocMode() {
  userModal.classList.add("hidden");
  chatSection.classList.add("hidden");
  noDocSection.classList.remove("hidden");
  resetBtn.classList.add("hidden");
  docIndicatorTxt.textContent = "No document loaded";
  docIndicator.classList.remove("loaded");
}

// ─── Load history on session resume ──────────────────────────────────────────
async function loadHistory() {
  try {
    const res  = await fetch("/history/", { headers: apiHeaders() });
    const data = await res.json();
    if (!data.messages || data.messages.length === 0) return;

    for (const msg of data.messages) {
      if (msg.role === "user") {
        appendUserBubble(msg.content);
      } else {
        const bubble = appendAssistantBubble();
        bubble.classList.remove("streaming");
        setAssistantContent(bubble, msg.content);
      }
    }
    scrollToBottom();
  } catch (_) {}
}

// ─── User info modal ──────────────────────────────────────────────────────────
function showUserModal() {
  chatSection.classList.add("hidden");
  resetBtn.classList.add("hidden");
  noDocSection.classList.add("hidden");
  if (currentFilename) {
    docIndicatorTxt.textContent = currentFilename;
    docIndicator.classList.add("loaded");
  }
  nameInput.value  = "";
  emailInput.value = "";
  modalError.classList.add("hidden");
  modalSubmitBtn.disabled = false;
  modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
  userModal.classList.remove("hidden");
  nameInput.focus();
}

userForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name  = nameInput.value.trim();
  const email = emailInput.value.trim();

  if (!name) {
    modalError.textContent = "Please enter your name.";
    modalError.classList.remove("hidden");
    return;
  }
  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    modalError.textContent = "Please enter a valid email address.";
    modalError.classList.remove("hidden");
    return;
  }

  modalError.classList.add("hidden");
  modalSubmitBtn.disabled = true;
  modalSubmitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Starting…';

  try {
    const res = await fetch("/start-session/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCookie("csrftoken"),
      },
      body: JSON.stringify({ name, email }),
    });

    if (res.ok) {
      const data = await res.json();
      setToken(data.token);         // persist token to localStorage
      modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
      showChatMode(currentFilename);
    } else {
      const data = await res.json().catch(() => ({}));
      modalError.textContent = data.message || "Failed to start session. Please try again.";
      modalError.classList.remove("hidden");
      modalSubmitBtn.disabled = false;
      modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
    }
  } catch (_) {
    modalError.textContent = "Network error. Please try again.";
    modalError.classList.remove("hidden");
    modalSubmitBtn.disabled = false;
    modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
  }
});

// ─── Reset (end session → clear token → show modal for new session) ───────────
resetBtn.addEventListener("click", () => {
  clearToken();    // drop the localStorage token — old session stays in DB

  chatWindow.innerHTML = `
    <div class="welcome-msg">
      <i class="fa-regular fa-comment-dots welcome-icon"></i>
      <p>Document loaded. Ask me anything about it.</p>
    </div>`;

  showUserModal();
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
        ...apiHeaders(true),
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

    const reader  = res.body.getReader();
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

        rawText += token.replace(/\\n/g, "\n");
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
  div.textContent = text;
  chatWindow.appendChild(div);
  scrollToBottom();
}

function appendAssistantBubble() {
  removeWelcome();
  const wrapper = document.createElement("div");
  wrapper.className = "bubble-wrapper";

  const div = document.createElement("div");
  div.className = "bubble assistant streaming";

  const actions = document.createElement("div");
  actions.className = "bubble-actions";

  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-btn";
  copyBtn.title = "Copy response";
  copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>';
  copyBtn.addEventListener("click", () => {
    const text = div.innerText;
    navigator.clipboard.writeText(text).then(() => {
      copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
      setTimeout(() => { copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>'; }, 1500);
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
