/**
 * DocChat admin — show/hide upload vs paste-text fields
 * based on the source_choice radio button selection.
 */
(function () {
  'use strict';

  var FILE_FIELDS = ['upload_file'];
  var TEXT_FIELDS = ['doc_label', 'pasted_text', 'text_context_mode'];

  function rowFor(fieldName) {
    // Django admin wraps each field in a <div class="form-row field-FIELDNAME">
    return document.querySelector('.form-row.field-' + fieldName);
  }

  function applyVisibility(source) {
    var isText = (source === 'text');

    FILE_FIELDS.forEach(function (f) {
      var row = rowFor(f);
      if (row) row.style.display = isText ? 'none' : '';
    });

    TEXT_FIELDS.forEach(function (f) {
      var row = rowFor(f);
      if (row) row.style.display = isText ? '' : 'none';
    });
  }

  function init() {
    var radios = document.querySelectorAll('input[name="source_choice"]');
    if (!radios.length) return;  // not on the add-document page

    radios.forEach(function (radio) {
      radio.addEventListener('change', function () {
        applyVisibility(this.value);
      });
    });

    // Set initial state from the currently-checked radio
    var checked = document.querySelector('input[name="source_choice"]:checked');
    applyVisibility(checked ? checked.value : 'file');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}());
