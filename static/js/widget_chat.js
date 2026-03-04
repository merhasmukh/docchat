/**
 * DocChat widget chat logic.
 * Runs inside the /widget/ iframe page.
 * Config is read from URL search params: color, title, greeting, mode.
 */
(function () {
  'use strict';

  // ── Config from URL params ─────────────────────────────────────────────────
  var params   = new URLSearchParams(window.location.search);
  var COLOR    = params.get('color')    || '#432323';
  var TITLE    = params.get('title')    || 'DocChat';
  var GREETING = params.get('greeting') || '';

  var TOKEN_KEY = 'docchat_widget_token';

  // ── Apply theme ────────────────────────────────────────────────────────────
  document.documentElement.style.setProperty('--wg-accent', COLOR);

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var $ = function (id) { return document.getElementById(id); };

  var elTitle      = $('wg-title');
  var elDocBadge   = $('wg-doc-indicator');
  var elDocName    = $('wg-doc-name');
  var elNodoc      = $('wg-nodoc');
  var elAuth       = $('wg-auth');
  var elStep1      = $('wg-step1');
  var elStep2      = $('wg-step2');
  var elName       = $('wg-name');
  var elEmail      = $('wg-email');
  var elCode       = $('wg-code');
  var elAuthErr    = $('wg-auth-error');
  var elOtpErr     = $('wg-otp-error');
  var elEmailHint  = $('wg-email-hint');
  var elCountdown  = $('wg-countdown');
  var elReqBtn     = $('wg-req-btn');
  var elVerifyBtn  = $('wg-verify-btn');
  var elResendBtn  = $('wg-resend-btn');
  var elSepEl      = $('wg-sep');
  var elBackBtn    = $('wg-back-btn');
  var elChat       = $('wg-chat');
  var elMessages   = $('wg-messages');
  var elInputBar   = $('wg-input-bar');
  var elInput      = $('wg-input');
  var elSendBtn    = $('wg-send-btn');

  // ── State ──────────────────────────────────────────────────────────────────
  var verificationId = null;
  var countdownTimer = null;
  var isStreaming    = false;
  var greetingShown  = false;

  // ── Helpers ────────────────────────────────────────────────────────────────
  function getCookie(name) {
    var val = document.cookie.split(';').map(function (c) { return c.trim(); });
    for (var i = 0; i < val.length; i++) {
      if (val[i].startsWith(name + '=')) return decodeURIComponent(val[i].slice(name.length + 1));
    }
    return '';
  }

  function apiHeaders() {
    var h = { 'Content-Type': 'application/json' };
    var csrf = getCookie('csrftoken');
    if (csrf) h['X-CSRFToken'] = csrf;
    var token = localStorage.getItem(TOKEN_KEY);
    if (token) h['X-Chat-Token'] = token;
    return h;
  }

  function show(el)  { el.classList.remove('d-none'); }
  function hide(el)  { el.classList.add('d-none'); }

  function showError(el, msg) { el.textContent = msg; show(el); }
  function clearError(el)     { el.textContent = ''; hide(el); }

  function setLoading(btn, loading) {
    btn.disabled = loading;
    if (loading) {
      btn.dataset.text = btn.textContent;
      btn.innerHTML = '<span class="wg-spinner"></span>';
    } else {
      btn.textContent = btn.dataset.text || btn.textContent;
    }
  }

  function renderMarkdown(text) {
    if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
      marked.setOptions({ breaks: true, gfm: true });
      return DOMPurify.sanitize(marked.parse(text));
    }
    return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
               .replace(/\n/g,'<br>');
  }

  // ── Screen switching ───────────────────────────────────────────────────────
  function showNodoc() {
    hide(elAuth); hide(elChat); hide(elInputBar); hide(elNodoc);
    show(elNodoc);
  }

  function showAuthStep1() {
    clearError(elAuthErr);
    show(elStep1); hide(elStep2);
    show(elAuth); hide(elChat); hide(elInputBar); hide(elNodoc);
  }

  function showChat() {
    hide(elAuth); hide(elNodoc);
    show(elChat); show(elInputBar);
    scrollToBottom();
  }

  function scrollToBottom() {
    elChat.scrollTop = elChat.scrollHeight;
  }

  // ── Message bubbles ────────────────────────────────────────────────────────
  function addUserBubble(text) {
    var msg = document.createElement('div');
    msg.className = 'wg-msg wg-user';
    msg.innerHTML = '<div class="wg-bubble">' +
      text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>') +
      '</div>';
    elMessages.appendChild(msg);
    scrollToBottom();
    return msg;
  }

  function addBotBubble(html, showCopy) {
    var msg = document.createElement('div');
    msg.className = 'wg-msg wg-bot';
    var bubble = document.createElement('div');
    bubble.className = 'wg-bubble';
    if (html) bubble.innerHTML = html;
    msg.appendChild(bubble);

    if (showCopy !== false) {
      var actions = document.createElement('div');
      actions.className = 'wg-msg-actions';
      var copyBtn = document.createElement('button');
      copyBtn.className = 'wg-copy-btn';
      copyBtn.textContent = 'Copy';
      copyBtn.addEventListener('click', function () {
        navigator.clipboard.writeText(bubble.innerText || bubble.textContent).then(function () {
          copyBtn.textContent = 'Copied!';
          setTimeout(function () { copyBtn.textContent = 'Copy'; }, 1500);
        });
      });
      actions.appendChild(copyBtn);
      msg.appendChild(actions);
    }

    elMessages.appendChild(msg);
    scrollToBottom();
    return { msg: msg, bubble: bubble };
  }

  // ── Greeting ───────────────────────────────────────────────────────────────
  function maybeShowGreeting() {
    if (GREETING && !greetingShown) {
      greetingShown = true;
      addBotBubble(renderMarkdown(GREETING), false);
    }
  }

  // ── Init: status check ─────────────────────────────────────────────────────
  elTitle.textContent = TITLE;

  fetch('/status/', { headers: apiHeaders() })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.document_loaded) {
        showNodoc();
        return;
      }

      // elDocName.textContent = data.filename || '';
      // show(elDocBadge);

      var token = localStorage.getItem(TOKEN_KEY);
      if (token && data.session_active) {
        loadHistory();
      } else {
        localStorage.removeItem(TOKEN_KEY);
        showAuthStep1();
      }
    })
    .catch(function () { showNodoc(); });

  // ── Load history ───────────────────────────────────────────────────────────
  function loadHistory() {
    fetch('/history/', { headers: apiHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        elMessages.innerHTML = '';
        maybeShowGreeting();
        if (data.messages && data.messages.length) {
          data.messages.forEach(function (m) {
            if (m.role === 'user') {
              addUserBubble(m.content);
            } else {
              addBotBubble(renderMarkdown(m.content));
            }
          });
        }
        showChat();
      })
      .catch(function () {
        elMessages.innerHTML = '';
        maybeShowGreeting();
        showChat();
      });
  }

  // ── OTP Step 1: request code ───────────────────────────────────────────────
  elReqBtn.addEventListener('click', function () {
    var name  = elName.value.trim();
    var email = elEmail.value.trim();
    clearError(elAuthErr);

    if (!name)  return showError(elAuthErr, 'Please enter your name.');
    if (!email) return showError(elAuthErr, 'Please enter your email address.');

    setLoading(elReqBtn, true);

    fetch('/request-otp/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ name: name, email: email }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setLoading(elReqBtn, false);
        if (data.status === 'ok') {
          verificationId = data.verification_id;
          elEmailHint.textContent = 'Enter the code sent to ' + data.email_hint;
          clearError(elOtpErr);
          elCode.value = '';
          hide(elStep1);
          show(elStep2);
          startCountdown(60);
        } else {
          showError(elAuthErr, data.message || 'Something went wrong. Please try again.');
        }
      })
      .catch(function () {
        setLoading(elReqBtn, false);
        showError(elAuthErr, 'Network error. Please try again.');
      });
  });

  // Enter key on name / email fields
  [elName, elEmail].forEach(function (el) {
    el.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); elReqBtn.click(); }
    });
  });

  // ── OTP countdown ──────────────────────────────────────────────────────────
  function startCountdown(seconds) {
    clearInterval(countdownTimer);
    hide(elResendBtn); hide(elSepEl); show(elCountdown);
    elCountdown.textContent = 'Resend in ' + seconds + 's';

    countdownTimer = setInterval(function () {
      seconds--;
      if (seconds > 0) {
        elCountdown.textContent = 'Resend in ' + seconds + 's';
      } else {
        clearInterval(countdownTimer);
        hide(elCountdown);
        show(elResendBtn);
        show(elSepEl);
      }
    }, 1000);
  }

  // ── OTP Step 2: verify ─────────────────────────────────────────────────────
  elVerifyBtn.addEventListener('click', function () {
    var code = elCode.value.trim();
    clearError(elOtpErr);

    if (!code) return showError(elOtpErr, 'Please enter the verification code.');
    if (!verificationId) return showAuthStep1();

    setLoading(elVerifyBtn, true);

    fetch('/verify-otp/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ verification_id: verificationId, code: code }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        setLoading(elVerifyBtn, false);
        if (data.status === 'ok') {
          clearInterval(countdownTimer);
          localStorage.setItem(TOKEN_KEY, data.token);
          elMessages.innerHTML = '';
          maybeShowGreeting();
          showChat();
        } else {
          if (data.code === 'expired') {
            showError(elOtpErr, 'Code has expired. Request a new one.');
            hide(elResendBtn); show(elBackBtn);
          } else {
            showError(elOtpErr, data.message || 'Incorrect code. Please try again.');
          }
        }
      })
      .catch(function () {
        setLoading(elVerifyBtn, false);
        showError(elOtpErr, 'Network error. Please try again.');
      });
  });

  elCode.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); elVerifyBtn.click(); }
  });

  // auto-advance when 6 digits entered
  elCode.addEventListener('input', function () {
    if (elCode.value.replace(/\D/g,'').length === 6) elVerifyBtn.click();
  });

  // ── Resend ─────────────────────────────────────────────────────────────────
  elResendBtn.addEventListener('click', function () {
    if (!verificationId) return;
    clearError(elOtpErr);
    hide(elResendBtn); hide(elSepEl);

    fetch('/resend-otp/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ verification_id: verificationId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status === 'ok') {
          startCountdown(60);
        } else {
          showError(elOtpErr, data.message || 'Could not resend. Please start again.');
          show(elBackBtn);
        }
      })
      .catch(function () {
        showError(elOtpErr, 'Network error. Please try again.');
      });
  });

  // ── Back ───────────────────────────────────────────────────────────────────
  elBackBtn.addEventListener('click', function () {
    clearInterval(countdownTimer);
    verificationId = null;
    showAuthStep1();
  });

  // ── Chat: send message ─────────────────────────────────────────────────────
  function sendMessage() {
    if (isStreaming) return;
    var question = elInput.value.trim();
    if (!question) return;

    elInput.value = '';
    autoResize();
    addUserBubble(question);

    var ref = addBotBubble('', false);
    var bubble  = ref.bubble;
    bubble.classList.add('wg-cursor');

    isStreaming = true;
    elSendBtn.disabled = true;

    var raw = '';

    fetch('/chat/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ question: question }),
    })
      .then(function (response) {
        if (!response.ok) {
          return response.text().then(function (t) {
            throw new Error('Server error ' + response.status + ': ' + t);
          });
        }
        var reader = response.body.getReader();
        var decoder = new TextDecoder();

        function pump() {
          return reader.read().then(function (result) {
            if (result.done) return;
            var chunk = decoder.decode(result.value, { stream: true });
            chunk.split('\n').forEach(function (line) {
              if (!line.startsWith('data: ')) return;
              var token = line.slice(6);
              if (token === '[DONE]') return;
              if (token.startsWith('[ERROR:')) {
                bubble.classList.remove('wg-cursor');
                bubble.innerHTML = '<em style="color:#dc2626">An error occurred. Please try again.</em>';
                isStreaming = false;
                elSendBtn.disabled = false;
                return;
              }
              raw += token.replace(/\\n/g, '\n');
              bubble.innerHTML = renderMarkdown(raw);
              scrollToBottom();
            });
            return pump();
          });
        }

        return pump();
      })
      .then(function () {
        bubble.classList.remove('wg-cursor');
        if (raw) bubble.innerHTML = renderMarkdown(raw);

        // Add copy button
        var actions = document.createElement('div');
        actions.className = 'wg-msg-actions';
        var copyBtn = document.createElement('button');
        copyBtn.className = 'wg-copy-btn';
        copyBtn.textContent = 'Copy';
        copyBtn.addEventListener('click', function () {
          navigator.clipboard.writeText(bubble.innerText || bubble.textContent).then(function () {
            copyBtn.textContent = 'Copied!';
            setTimeout(function () { copyBtn.textContent = 'Copy'; }, 1500);
          });
        });
        actions.appendChild(copyBtn);
        ref.msg.appendChild(actions);

        isStreaming = false;
        elSendBtn.disabled = false;
        scrollToBottom();
      })
      .catch(function (err) {
        bubble.classList.remove('wg-cursor');
        bubble.innerHTML = '<em style="color:#dc2626">Connection error. Please try again.</em>';
        console.error('[DocChat widget] stream error:', err);
        isStreaming = false;
        elSendBtn.disabled = false;
      });
  }

  elSendBtn.addEventListener('click', sendMessage);

  elInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // ── Auto-resize textarea ───────────────────────────────────────────────────
  function autoResize() {
    elInput.style.height = 'auto';
    elInput.style.height = Math.min(elInput.scrollHeight, 120) + 'px';
  }

  elInput.addEventListener('input', autoResize);

}());
