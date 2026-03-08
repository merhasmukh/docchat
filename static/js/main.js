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

// Modal refs — step 1
const userModal       = document.getElementById("user-modal");
const modalCloseBtn   = document.getElementById("modal-close-btn");
const userForm       = document.getElementById("user-form");
const nameInput      = document.getElementById("user-name");
const emailInput     = document.getElementById("user-email");
const modalError     = document.getElementById("modal-error");
const modalSubmitBtn = document.getElementById("modal-submit-btn");

// Modal refs — step 2 (OTP)
const otpStep1     = document.getElementById("um-step-1");
const otpStep2     = document.getElementById("um-step-2");
const otpForm      = document.getElementById("otp-form");
const otpInput     = document.getElementById("otp-input");
const otpError     = document.getElementById("otp-error");
const otpSubmitBtn = document.getElementById("otp-submit-btn");
const otpResendBtn = document.getElementById("otp-resend-btn");
const otpBackBtn   = document.getElementById("otp-back-btn");
const otpEmailHint = document.getElementById("otp-email-hint");

// OTP flow state
let otpVerificationId = null;
let otpCountdownTimer = null;
let otpResendUsed     = false;

// Track the active document filename
let currentFilename = null;

// Session config (loaded from /session-config/ on page load)
let sessionCfg = { collect_name: true, collect_email: true, verify_email: true };

// ─── Page load: fetch session config + check active document + session ─────────
(async function init() {
  // Fetch config and status in parallel
  const [cfgResult, statusResult] = await Promise.allSettled([
    fetch("/session-config/"),
    fetch("/status/", { headers: apiHeaders() }),
  ]);

  if (cfgResult.status === "fulfilled" && cfgResult.value.ok) {
    try { sessionCfg = await cfgResult.value.json(); } catch (_) {}
  }

  let documentLoaded = false;
  let sessionActive  = false;
  if (statusResult.status === "fulfilled") {
    try {
      const data = await statusResult.value.json();
      documentLoaded  = data.document_loaded;
      currentFilename = data.filename;
      sessionActive   = data.session_active;
    } catch (_) {}
  }

  if (documentLoaded) {
    if (sessionActive) {
      showChatMode(currentFilename);
      loadHistory();
    } else if (!sessionCfg.collect_name && !sessionCfg.collect_email) {
      // Anonymous mode — create session immediately, no modal needed
      await createDirectSession({});
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
  // Show/hide fields based on admin config
  const nameField  = document.getElementById("um-field-name");
  const emailField = document.getElementById("um-field-email");
  if (nameField)  nameField.style.display  = sessionCfg.collect_name  ? "" : "none";
  if (emailField) emailField.style.display = sessionCfg.collect_email ? "" : "none";

  // Always reset to step 1
  showOtpStep(1);
  nameInput.value  = "";
  emailInput.value = "";
  modalError.classList.add("hidden");
  modalSubmitBtn.disabled = false;
  modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
  userModal.classList.remove("hidden");
  (sessionCfg.collect_name ? nameInput : emailInput).focus();
}

function showOtpStep(step) {
  if (step === 1) {
    otpStep1.classList.remove("hidden");
    otpStep2.classList.add("hidden");
    stopOtpCountdown();
  } else {
    otpStep1.classList.add("hidden");
    otpStep2.classList.remove("hidden");
  }
}

// ─── Countdown timer ──────────────────────────────────────────────────────────
function startOtpCountdown() {
  stopOtpCountdown();
  let secondsLeft = 60;
  const timerEl = document.getElementById("otp-timer");
  if (timerEl) timerEl.textContent = secondsLeft;
  otpResendBtn.disabled = true;
  otpResendBtn.classList.remove("available");

  otpCountdownTimer = setInterval(() => {
    secondsLeft -= 1;
    const el = document.getElementById("otp-timer");
    if (el) el.textContent = secondsLeft;

    if (secondsLeft <= 0) {
      stopOtpCountdown();
      const countdownEl = document.getElementById("otp-countdown");
      if (countdownEl) countdownEl.innerHTML = '<i class="fa-regular fa-clock"></i> Code expired.';
      if (!otpResendUsed) {
        otpResendBtn.disabled = false;
        otpResendBtn.classList.add("available");
      }
    }
  }, 1000);
}

function stopOtpCountdown() {
  if (otpCountdownTimer !== null) {
    clearInterval(otpCountdownTimer);
    otpCountdownTimer = null;
  }
}

// ─── Direct session creation (no OTP) ─────────────────────────────────────────
async function createDirectSession(payload) {
  try {
    const res  = await fetch("/start-session/", {
      method:  "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
      body:    JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.status === "ok") {
      setToken(data.token);
      showChatMode(currentFilename);
      return true;
    } else {
      return { error: data.message || "Failed to start session. Please try again." };
    }
  } catch (_) {
    return { error: "Network error. Please try again." };
  }
}

// ─── Step 1: submit name + email → OTP or direct session ──────────────────────
userForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name  = nameInput.value.trim();
  const email = emailInput.value.trim();

  if (sessionCfg.collect_name && !name) {
    modalError.textContent = "Please enter your name.";
    modalError.classList.remove("hidden");
    return;
  }
  if (sessionCfg.collect_email && (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))) {
    modalError.textContent = "Please enter a valid email address.";
    modalError.classList.remove("hidden");
    return;
  }

  modalError.classList.add("hidden");
  modalSubmitBtn.disabled = true;

  // ── Direct session (no OTP) ──────────────────────────────────────────────────
  if (!sessionCfg.collect_email || !sessionCfg.verify_email) {
    modalSubmitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Starting…';
    const payload = {};
    if (sessionCfg.collect_name)  payload.name  = name;
    if (sessionCfg.collect_email) payload.email = email;
    const result = await createDirectSession(payload);
    if (result !== true) {
      modalError.textContent = result.error;
      modalError.classList.remove("hidden");
      modalSubmitBtn.disabled = false;
      modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
    }
    return;
  }

  // ── OTP flow ─────────────────────────────────────────────────────────────────
  modalSubmitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending code…';

  try {
    const res  = await fetch("/request-otp/", {
      method:  "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
      body:    JSON.stringify({ name, email }),
    });
    const data = await res.json();

    if (data.status === "ok") {
      otpVerificationId = data.verification_id;
      otpResendUsed     = false;
      otpEmailHint.textContent = data.email_hint;
      // Reset OTP step state
      otpInput.value = "";
      otpError.classList.add("hidden");
      otpSubmitBtn.disabled = false;
      otpSubmitBtn.innerHTML = '<i class="fa-solid fa-circle-check"></i>Verify & Start Chat';
      // Rebuild countdown display (may have been altered by prior session)
      const countdownEl = document.getElementById("otp-countdown");
      if (countdownEl) countdownEl.innerHTML =
        '<i class="fa-regular fa-clock"></i> Code expires in <strong id="otp-timer">60</strong>s';
      showOtpStep(2);
      startOtpCountdown();
      otpInput.focus();
    } else {
      modalError.textContent = data.message || "Failed to send code. Please try again.";
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

// ─── Step 2: submit OTP code → verify ─────────────────────────────────────────
otpForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = otpInput.value.trim();

  if (!code || !/^\d{6}$/.test(code)) {
    otpError.textContent = "Please enter the 6-digit code from your email.";
    otpError.classList.remove("hidden");
    return;
  }

  otpError.classList.add("hidden");
  otpSubmitBtn.disabled = true;
  otpSubmitBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Verifying…';

  try {
    const res  = await fetch("/verify-otp/", {
      method:  "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
      body:    JSON.stringify({ verification_id: otpVerificationId, code }),
    });
    const data = await res.json();

    if (data.status === "ok") {
      stopOtpCountdown();
      setToken(data.token);
      showChatMode(currentFilename);
    } else {
      otpError.textContent = data.message || "Verification failed. Please try again.";
      otpError.classList.remove("hidden");
      otpSubmitBtn.disabled = false;
      otpSubmitBtn.innerHTML = '<i class="fa-solid fa-circle-check"></i>Verify & Start Chat';
      // If expired, disable submit and let user resend or go back
      if (data.code === "expired") {
        otpSubmitBtn.disabled = true;
      }
    }
  } catch (_) {
    otpError.textContent = "Network error. Please try again.";
    otpError.classList.remove("hidden");
    otpSubmitBtn.disabled = false;
    otpSubmitBtn.innerHTML = '<i class="fa-solid fa-circle-check"></i>Verify & Start Chat';
  }
});

// ─── Resend button ────────────────────────────────────────────────────────────
otpResendBtn.addEventListener("click", async () => {
  if (!otpVerificationId || otpResendUsed) return;

  otpResendBtn.disabled = true;
  otpResendBtn.classList.remove("available");
  otpResendBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Sending…';
  otpError.classList.add("hidden");

  try {
    const res  = await fetch("/resend-otp/", {
      method:  "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
      body:    JSON.stringify({ verification_id: otpVerificationId }),
    });
    const data = await res.json();

    if (data.status === "ok") {
      otpResendUsed = true;
      // Rebuild countdown for fresh 60 seconds
      const countdownEl = document.getElementById("otp-countdown");
      if (countdownEl) countdownEl.innerHTML =
        '<i class="fa-regular fa-clock"></i> Code expires in <strong id="otp-timer">60</strong>s';
      otpInput.value = "";
      otpSubmitBtn.disabled = false;
      otpSubmitBtn.innerHTML = '<i class="fa-solid fa-circle-check"></i>Verify & Start Chat';
      otpResendBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Resend code';
      startOtpCountdown();
      otpInput.focus();
    } else {
      otpError.textContent = data.message || "Failed to resend. Please start again.";
      otpError.classList.remove("hidden");
      otpResendBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Resend code';
    }
  } catch (_) {
    otpError.textContent = "Network error. Please try again.";
    otpError.classList.remove("hidden");
    otpResendBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> Resend code';
    otpResendBtn.disabled = false;
  }
});

// ─── Back button: return to step 1 ───────────────────────────────────────────
otpBackBtn.addEventListener("click", () => {
  stopOtpCountdown();
  otpVerificationId = null;
  otpResendUsed     = false;
  showOtpStep(1);
  modalError.classList.add("hidden");
  modalSubmitBtn.disabled = false;
  modalSubmitBtn.innerHTML = '<i class="fa-solid fa-arrow-right-to-bracket"></i>Start Chat';
  nameInput.focus();
});

// ─── Modal close (×) button ───────────────────────────────────────────────────
modalCloseBtn.addEventListener("click", () => {
  stopOtpCountdown();
  userModal.classList.add("hidden");
  if (currentFilename) {
    chatSection.classList.remove("hidden");
    resetBtn.classList.remove("hidden");
    docIndicatorTxt.textContent = currentFilename;
    docIndicator.classList.add("loaded");
  }
});

// ─── OTP input: digits only ───────────────────────────────────────────────────
otpInput.addEventListener("input", () => {
  otpInput.value = otpInput.value.replace(/\D/g, "").slice(0, 6);
});

// ─── Reset (end session → clear token → show modal for new session) ───────────
resetBtn.addEventListener("click", () => {
  clearToken();    // drop the localStorage token — old session stays in DB

  chatWindow.innerHTML = `
    <div class="welcome-msg">
      <i class="fa-regular fa-comment-dots welcome-icon"></i>
      <p>Ask me anything about the document</p>
      <span class="welcome-hint">Press Enter to send &nbsp;&middot;&nbsp; Shift+Enter for new line</span>
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
