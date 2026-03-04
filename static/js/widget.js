/**
 * DocChat embeddable widget loader.
 *
 * Add to any webpage:
 *
 *   <script
 *     src="https://your-docchat.com/static/js/widget.js"
 *     data-server="https://your-docchat.com"
 *     data-position="bottom-right"
 *     data-color="#432323"
 *     data-title="Ask our AI"
 *     data-greeting="Hello! How can I help?"
 *   ></script>
 *
 * Attributes:
 *   data-server    Required. Base URL of the DocChat server (no trailing slash).
 *   data-position  "bottom-right" (default) or "bottom-left".
 *   data-color     Accent colour for the bubble and widget header. Default: #432323.
 *   data-title     Widget header title. Default: "DocChat".
 *   data-greeting  Optional opening message shown as the first assistant bubble.
 *   data-mode      "popup" (default corner popup) or "fullpage" (full viewport overlay).
 */
(function () {
  'use strict';

  var s = document.currentScript;
  if (!s) return; // guard: loaded async/defer

  var server   = (s.getAttribute('data-server')   || '').replace(/\/$/, '');
  var position = s.getAttribute('data-position')  || 'bottom-right';
  var color    = s.getAttribute('data-color')     || '#735557';
  var title    = s.getAttribute('data-title')     || 'DocChat';
  var greeting = s.getAttribute('data-greeting')  || '';
  var mode     = s.getAttribute('data-mode')      || 'popup';

  if (!server) {
    console.warn('[DocChat] data-server attribute is required.');
    return;
  }

  var isRight = position !== 'bottom-left';
  var edge    = isRight ? 'right' : 'left';

  // ── Build iframe URL ──────────────────────────────────────────────────────
  var params = new URLSearchParams({ color: color, title: title, greeting: greeting, mode: mode });
  var iframeUrl = server + '/widget/?' + params.toString();

  // ── Inject styles ─────────────────────────────────────────────────────────
  var style = document.createElement('style');
  style.textContent = [
    '#docchat-bubble{',
    '  position:fixed;',
    '  bottom:calc(24px + env(safe-area-inset-bottom,0px));',
    '  ' + edge + ':max(24px,calc(24px + env(safe-area-inset-' + edge + ',0px)));',
    '  width:56px;height:56px;border-radius:50%;',
    '  background:' + color + ';border:none;cursor:pointer;',
    '  box-shadow:0 4px 16px rgba(0,0,0,.28);',
    '  display:flex;align-items:center;justify-content:center;',
    '  z-index:999998;transition:transform .2s,box-shadow .2s;',
    '  outline:none;',
    '}',
    '#docchat-bubble:hover{transform:scale(1.08);box-shadow:0 6px 20px rgba(0,0,0,.35);}',
    /* Minimum 56x56 tap target, already set. Active feedback for touch. */
    '#docchat-bubble:active{transform:scale(.94);}',
    '#docchat-bubble svg{width:26px;height:26px;fill:#fff;pointer-events:none;}',

    '#docchat-wrap{',
    '  position:fixed;' + edge + ':24px;',
    '  bottom:calc(92px + env(safe-area-inset-bottom,0px));',
    '  width:380px;height:560px;',
    '  border-radius:16px;overflow:hidden;',
    '  box-shadow:0 8px 32px rgba(0,0,0,.22);',
    '  z-index:999997;display:none;',
    '  transform:scale(.95) translateY(8px);opacity:0;',
    '  transition:transform .2s,opacity .2s;',
    '}',
    '#docchat-wrap.dc-open{display:block;}',
    '#docchat-wrap.dc-visible{transform:scale(1) translateY(0);opacity:1;}',
    '#docchat-wrap iframe{width:100%;height:100%;border:none;display:block;}',

    /* fullpage mode */
    '#docchat-wrap.dc-fullpage{',
    '  top:0;left:0;right:0;bottom:0;',
    '  width:100%;height:100%;',
    '  border-radius:0;',
    '}',

    /* mobile: always full viewport */
    '@media(max-width:440px){',
    '  #docchat-wrap{top:0!important;left:0!important;right:0!important;bottom:0!important;width:100%!important;height:100%!important;border-radius:0!important;}',
    '  #docchat-bubble{bottom:max(16px,calc(env(safe-area-inset-bottom,0px) + 16px));}',
    '}',

    /* When the iframe covers the full viewport (fullpage mode or mobile popup),
       move the close button to the top-right so it doesn't overlap the input bar */
    '#docchat-bubble.dc-top{',
    '  bottom:auto!important;',
    '  top:calc(16px + env(safe-area-inset-top,0px))!important;',
    '  right:16px!important;',
    '  left:auto!important;',
    '}',
  ].join('');
  document.head.appendChild(style);

  // ── SVG icons ─────────────────────────────────────────────────────────────
  var ICON_CHAT  = '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>';

  // ── Bubble button ─────────────────────────────────────────────────────────
  var btn = document.createElement('button');
  btn.id = 'docchat-bubble';
  btn.setAttribute('aria-label', 'Open ' + title);
  btn.innerHTML = ICON_CHAT;
  document.body.appendChild(btn);

  // ── iframe wrapper ────────────────────────────────────────────────────────
  var wrap = document.createElement('div');
  wrap.id = 'docchat-wrap';
  if (mode === 'fullpage') wrap.classList.add('dc-fullpage');

  var frame = document.createElement('iframe');
  frame.src = iframeUrl;
  frame.title = title;
  frame.allow = 'clipboard-write';
  wrap.appendChild(frame);
  document.body.appendChild(wrap);

  // ── Toggle open / close ───────────────────────────────────────────────────
  var isOpen = false;

  // Returns true when the iframe covers the full viewport so the close button
  // needs to move to the top-right instead of sitting over the input bar.
  function coveringViewport() {
    return mode === 'fullpage' || window.innerWidth <= 440;
  }

  btn.addEventListener('click', function () {
    isOpen = !isOpen;
    btn.innerHTML = isOpen ? ICON_CLOSE : ICON_CHAT;
    btn.setAttribute('aria-label', (isOpen ? 'Close ' : 'Open ') + title);
    if (isOpen) {
      if (coveringViewport()) btn.classList.add('dc-top');
      wrap.classList.add('dc-open');
      requestAnimationFrame(function () { wrap.classList.add('dc-visible'); });
    } else {
      btn.classList.remove('dc-top');
      wrap.classList.remove('dc-visible');
      setTimeout(function () { wrap.classList.remove('dc-open'); }, 200);
    }
  });
}());
