/**
 * DocChat widget chat logic.
 * Runs inside the /widget/ iframe page.
 * Config is read from URL search params: color, title, greeting, mode.
 */
(function () {
  'use strict';

  // ── Config from URL params ─────────────────────────────────────────────────
  var params   = new URLSearchParams(window.location.search);
  var COLOR       = params.get('color')       || '#C45500';
  var TITLE       = params.get('title')       || 'DocChat';
  var GREETING    = params.get('greeting')    || '';
  var NUDGE       = params.get('nudge')       || '';
  var NUDGE_DELAY = parseInt(params.get('nudge_delay') || '10', 10) * 1000;

  var TOKEN_KEY = 'docchat_widget_token';

  // ── Apply theme ────────────────────────────────────────────────────────────
  document.documentElement.style.setProperty('--wg-accent', COLOR);

  // ── DOM refs ───────────────────────────────────────────────────────────────
  var $ = function (id) { return document.getElementById(id); };

  var elTitle      = $('wg-title');
  var elNodoc      = $('wg-nodoc');
  var elAuth       = $('wg-auth');
  var elStep1      = $('wg-step1');
  var elStep2      = $('wg-step2');
  var elName       = $('wg-name');
  var elEmail      = $('wg-email');
  var elMobile     = $('wg-mobile');
  var elCountry    = $('wg-country-code');
  var elMobileErr  = $('wg-mobile-error');
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
  var elNewChatBtn = $('wg-new-chat-btn');

  // ── State ──────────────────────────────────────────────────────────────────
  var verificationId  = null;
  var countdownTimer  = null;
  var nudgeTimer      = null;
  var isStreaming     = false;
  var greetingShown   = false;
  var pendingUserName = '';   // name captured from form or returning session
  var sessionCfg      = { collect_name: true, collect_email: true, verify_email: true, collect_mobile: false };

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

  function showBtn(el)  { el.style.display = ''; }
  function hideBtn(el)  { el.style.display = 'none'; }

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

  // ── SVG icons ──────────────────────────────────────────────────────────────
  var ICON_COPY    = '<i class="fa-regular fa-copy"></i>';
  var ICON_CHECK   = '<i class="fa-solid fa-check"></i>';
  var ICON_LIKE    = '<i class="fa-regular fa-thumbs-up"></i>';
  var ICON_DISLIKE = '<i class="fa-regular fa-thumbs-down"></i>';

  // ── Feedback ───────────────────────────────────────────────────────────────
  function _attachWidgetFeedback(ref, msgId, likedState) {
    var actions = ref.msg.querySelector('.wg-msg-actions');
    if (!actions) return;

    var likeBtn = document.createElement('button');
    likeBtn.className = 'wg-fb-btn wg-like-btn';
    likeBtn.title = 'Helpful';
    likeBtn.innerHTML = ICON_LIKE;

    var dislikeBtn = document.createElement('button');
    dislikeBtn.className = 'wg-fb-btn wg-dislike-btn';
    dislikeBtn.title = 'Not helpful';
    dislikeBtn.innerHTML = ICON_DISLIKE;

    // Restore prior feedback state if any
    if (likedState === true) {
      likeBtn.classList.add('active');
      likeBtn.disabled = true;
      dislikeBtn.disabled = true;
    } else if (likedState === false) {
      dislikeBtn.classList.add('active');
      likeBtn.disabled = true;
      dislikeBtn.disabled = true;
    }

    likeBtn.addEventListener('click', function () { _submitWidgetFeedback(msgId, true, likeBtn, dislikeBtn); });
    dislikeBtn.addEventListener('click', function () { _submitWidgetFeedback(msgId, false, likeBtn, dislikeBtn); });

    actions.appendChild(likeBtn);
    actions.appendChild(dislikeBtn);
  }

  function _submitWidgetFeedback(msgId, liked, likeBtn, dislikeBtn) {
    likeBtn.classList.toggle('active', liked);
    dislikeBtn.classList.toggle('active', !liked);
    likeBtn.disabled = true;
    dislikeBtn.disabled = true;
    fetch('/feedback/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ message_id: msgId, liked: liked }),
    }).catch(function () {});
  }

  // ── Screen switching ───────────────────────────────────────────────────────
  function showNodoc() {
    hide(elAuth); hide(elChat); hide(elInputBar); hide(elNodoc);
    hideBtn(elNewChatBtn);
    show(elNodoc);
  }

  function showAuthStep1() {
    clearError(elAuthErr);
    // Show/hide fields based on admin config
    var fieldName   = document.getElementById('wg-field-name');
    var fieldEmail  = document.getElementById('wg-field-email');
    var fieldMobile = document.getElementById('wg-field-mobile');
    if (fieldName)   fieldName.style.display   = sessionCfg.collect_name   ? '' : 'none';
    if (fieldEmail)  fieldEmail.style.display  = sessionCfg.collect_email  ? '' : 'none';
    if (fieldMobile) fieldMobile.style.display = sessionCfg.collect_mobile ? '' : 'none';
    // Adjust button label
    elReqBtn.textContent = (sessionCfg.collect_email && sessionCfg.verify_email)
      ? 'Send verification code'
      : 'Start Chat';
    show(elStep1); hide(elStep2);
    show(elAuth); hide(elChat); hide(elInputBar); hide(elNodoc);
    hideBtn(elNewChatBtn);
  }

  function showChat() {
    hide(elAuth); hide(elNodoc);
    show(elChat); show(elInputBar);
    showBtn(elNewChatBtn);
    scrollToBottom();
    startNudgeTimer();
  }

  function scrollToBottom() {
    elChat.scrollTop = elChat.scrollHeight;
  }

  // ── Reset / New Chat ───────────────────────────────────────────────────────
  function resetSession() {
    fetch('/reset/', { method: 'POST', headers: apiHeaders() }).catch(function () {});
    localStorage.removeItem(TOKEN_KEY);
    clearInterval(countdownTimer);
    isStreaming = false;
    greetingShown = false;
    pendingUserName = '';
    stopNudgeTimer();
    elMessages.innerHTML = '';
    // Clear auth fields and reset button state so the next session starts blank
    elName.value   = '';
    elEmail.value  = '';
    elMobile.value = '';
    var ccSelect = document.getElementById('wg-country-code');
    if (ccSelect) ccSelect.selectedIndex = 0;
    clearError(elAuthErr);
    elReqBtn.disabled = false;
    hideBtn(elNewChatBtn);
    if (!sessionCfg.collect_name && !sessionCfg.collect_email && !sessionCfg.collect_mobile) {
      createDirectSession({});
    } else {
      showAuthStep1();
    }
  }

  elNewChatBtn.addEventListener('click', resetSession);

  // ── Timestamp ──────────────────────────────────────────────────────────────
  function makeTimestamp() {
    var now = new Date();
    var h = now.getHours(), m = now.getMinutes();
    var ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    var ts = document.createElement('span');
    ts.className = 'wg-ts';
    ts.textContent = h + ':' + (m < 10 ? '0' : '') + m + ' ' + ampm;
    return ts;
  }

  // ── Message bubbles ────────────────────────────────────────────────────────
  function addUserBubble(text) {
    var msg = document.createElement('div');
    msg.className = 'wg-msg wg-user';
    var bubble = document.createElement('div');
    bubble.className = 'wg-bubble';
    bubble.innerHTML = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
    msg.appendChild(bubble);
    msg.appendChild(makeTimestamp());
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
      copyBtn.title = 'Copy';
      copyBtn.innerHTML = ICON_COPY;
      copyBtn.addEventListener('click', function () {
        navigator.clipboard.writeText(bubble.innerText || bubble.textContent).then(function () {
          copyBtn.innerHTML = ICON_CHECK;
          setTimeout(function () { copyBtn.innerHTML = ICON_COPY; }, 1500);
        });
      });
      actions.appendChild(copyBtn);
      msg.appendChild(actions);
    }

    var ts = makeTimestamp();
    msg.appendChild(ts);

    elMessages.appendChild(msg);
    scrollToBottom();
    return { msg: msg, bubble: bubble };
  }

  // ── Nudge timer ────────────────────────────────────────────────────────────
  function stopNudgeTimer() {
    clearTimeout(nudgeTimer);
    nudgeTimer = null;
  }

  function startNudgeTimer() {
    if (!NUDGE) return;
    stopNudgeTimer();
    nudgeTimer = setTimeout(function () {
      nudgeTimer = null;
      addBotBubble(renderMarkdown(NUDGE), false);
    }, NUDGE_DELAY);
  }

  // ── Greeting ───────────────────────────────────────────────────────────────
  function resolveGreeting(name) {
    if (!GREETING) return '';
    return name
      ? GREETING.replace(/\{name\}/g, name)
      : GREETING.replace(/\{name\}/g, '');
  }

  function maybeShowGreeting() {
    if (GREETING && !greetingShown) {
      greetingShown = true;
      addBotBubble(renderMarkdown(resolveGreeting(pendingUserName)), false);
    }
  }

  // ── Init: fetch session config + status check ──────────────────────────────
  elTitle.textContent = TITLE;

  Promise.allSettled([
    fetch('/session-config/'),
    fetch('/status/', { headers: apiHeaders() }),
  ]).then(function (results) {
    var cfgRes    = results[0];
    var statusRes = results[1];

    var cfgPromise = (cfgRes.status === 'fulfilled' && cfgRes.value.ok)
      ? cfgRes.value.json().catch(function () { return sessionCfg; })
      : Promise.resolve(sessionCfg);

    var statusPromise = (statusRes.status === 'fulfilled')
      ? statusRes.value.json().catch(function () { return {}; })
      : Promise.resolve({});

    return Promise.all([cfgPromise, statusPromise]);
  }).then(function (vals) {
    var cfg  = vals[0];
    var data = vals[1];
    sessionCfg = cfg;

    if (!data.document_loaded) { showNodoc(); return; }

    var token = localStorage.getItem(TOKEN_KEY);
    if (token && data.session_active) {
      loadHistory();
    } else {
      localStorage.removeItem(TOKEN_KEY);
      if (!sessionCfg.collect_name && !sessionCfg.collect_email && !sessionCfg.collect_mobile) {
        // Anonymous — create session immediately
        createDirectSession({});
      } else {
        showAuthStep1();
      }
    }
  }).catch(function () { showNodoc(); });

  // ── Load history ───────────────────────────────────────────────────────────
  function loadHistory() {
    fetch('/history/', { headers: apiHeaders() })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        pendingUserName = data.user_name || '';
        elMessages.innerHTML = '';
        maybeShowGreeting();
        if (data.messages && data.messages.length) {
          data.messages.forEach(function (m) {
            if (m.role === 'user') {
              addUserBubble(m.content);
            } else {
              var ref = addBotBubble(renderMarkdown(m.content), false);
              // Build actions row for restored messages
              var actions = document.createElement('div');
              actions.className = 'wg-msg-actions';
              var copyBtn = document.createElement('button');
              copyBtn.className = 'wg-copy-btn';
              copyBtn.title = 'Copy';
              copyBtn.innerHTML = ICON_COPY;
              copyBtn.addEventListener('click', function () {
                navigator.clipboard.writeText(ref.bubble.innerText || ref.bubble.textContent).then(function () {
                  copyBtn.innerHTML = ICON_CHECK;
                  setTimeout(function () { copyBtn.innerHTML = ICON_COPY; }, 1500);
                });
              });
              actions.appendChild(copyBtn);
              var tsEl = ref.msg.querySelector('.wg-ts');
              ref.msg.insertBefore(actions, tsEl || null);
              if (m.id) {
                _attachWidgetFeedback(ref, m.id, m.liked);
              }
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

  // ── Direct session (no OTP) ────────────────────────────────────────────────
  function createDirectSession(payload) {
    fetch('/start-session/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status === 'ok') {
          localStorage.setItem(TOKEN_KEY, data.token);
          elMessages.innerHTML = '';
          maybeShowGreeting();
          showChat();
        } else {
          showError(elAuthErr, data.message || 'Failed to start session. Please try again.');
          setLoading(elReqBtn, false);
        }
      })
      .catch(function () {
        showError(elAuthErr, 'Network error. Please try again.');
        setLoading(elReqBtn, false);
      });
  }

  // ── Mobile validation ───────────────────────────────────────────────────────
  function validateMobile() {
    var digits  = elMobile.value.replace(/[\s\-]/g, '');
    var code    = elCountry.value;
    var opt     = elCountry.options[elCountry.selectedIndex];
    var reqLen  = parseInt(opt.getAttribute('data-digits') || '10', 10);
    var pattern = opt.getAttribute('data-pattern') || '';

    if (!digits) {
      showError(elMobileErr, 'કૃપા કરી તમારો મોબાઈલ નંબર જણાવો ');
      return null;
    }
    if (!/^\d+$/.test(digits)) {
      showError(elMobileErr, 'Mobile number must contain only digits.');
      return null;
    }
    if (digits.length !== reqLen) {
      showError(elMobileErr, 'Mobile number must be exactly ' + reqLen + ' digits for ' + code + '.');
      return null;
    }
    if (pattern && !new RegExp(pattern).test(digits)) {
      showError(elMobileErr, 'Please enter a valid mobile number.');
      return null;
    }
    clearError(elMobileErr);
    return code + digits;
  }

  // ── Step 1: submit → OTP or direct session ─────────────────────────────────
  elReqBtn.addEventListener('click', function () {
    var name   = elName.value.trim();
    var email  = elEmail.value.trim();
    var mobile = '';
    pendingUserName = name;
    clearError(elAuthErr);

    if (sessionCfg.collect_name  && !name)  return showError(elAuthErr, 'કૃપા કરી તમારું નામ જણાવો ');
    if (sessionCfg.collect_email && !email) return showError(elAuthErr, 'કૃપા કરી તમારું ઈમેઇલ એડ્રેસ જણાવો ');

    if (sessionCfg.collect_mobile) {
      mobile = validateMobile();
      if (mobile === null) return;
    }

    setLoading(elReqBtn, true);

    // ── Direct session (no OTP needed) ──────────────────────────────────────
    if (!sessionCfg.collect_email || !sessionCfg.verify_email) {
      var payload = {};
      if (sessionCfg.collect_name)   payload.name   = name;
      if (sessionCfg.collect_email)  payload.email  = email;
      if (sessionCfg.collect_mobile) payload.mobile = mobile;
      createDirectSession(payload);
      return;
    }

    // ── OTP flow ─────────────────────────────────────────────────────────────
    fetch('/request-otp/', {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify({ name: name, email: email, mobile: mobile }),
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
    stopNudgeTimer();

    elInput.value = '';
    autoResize();
    addUserBubble(question);

    var ref = addBotBubble('', false);
    var bubble  = ref.bubble;
    bubble.classList.add('wg-cursor');

    isStreaming = true;
    elSendBtn.disabled = true;

    var raw = '';
    var capturedMsgId = null;

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
              if (token === '[DONE]' || token.startsWith('[DONE:')) {
                var doneMatch = token.match(/^\[DONE:(\d+)\]$/);
                if (doneMatch) { capturedMsgId = parseInt(doneMatch[1], 10); }
                return;
              }
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

        // Build actions row (copy + like/dislike), insert before timestamp
        var actions = document.createElement('div');
        actions.className = 'wg-msg-actions';
        var copyBtn = document.createElement('button');
        copyBtn.className = 'wg-copy-btn';
        copyBtn.title = 'Copy';
        copyBtn.innerHTML = ICON_COPY;
        copyBtn.addEventListener('click', function () {
          navigator.clipboard.writeText(bubble.innerText || bubble.textContent).then(function () {
            copyBtn.innerHTML = ICON_CHECK;
            setTimeout(function () { copyBtn.innerHTML = ICON_COPY; }, 1500);
          });
        });
        actions.appendChild(copyBtn);
        // Insert before timestamp so order is: bubble → actions → timestamp
        var tsEl = ref.msg.querySelector('.wg-ts');
        ref.msg.insertBefore(actions, tsEl || null);
        // Attach like/dislike now that actions div exists
        if (capturedMsgId) { _attachWidgetFeedback(ref, capturedMsgId, null); }

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

  // ── Popup re-open: restart nudge timer ────────────────────────────────────
  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'docchat:opened') {
      // Only restart if the chat screen is already visible (past auth)
      if (!elChat.classList.contains('d-none')) {
        startNudgeTimer();
      }
    }
  });

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
