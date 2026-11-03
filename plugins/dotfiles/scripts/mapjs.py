"""The one inline script of the interactive map, and the policy that pins it.

The page carries a Content-Security-Policy that allows nothing except this exact script (by its SHA-256) and inline styles:
`default-src 'none'` blocks every connection, image, font, frame and object, so even a bug in the script cannot send anything
anywhere. The script is plain, has no libraries and never builds markup from text: it moves, shows and hides nodes the server
already rendered (escaped), reads `data-*` attributes, and writes text with `textContent`. Its only state is the selection,
in memory. Nothing is stored, and nothing is sent.

`FORBIDDEN` lists what the script must never contain. The tests check it, so a later edit cannot quietly add a way to
build markup from text, load code, or talk to the network.
"""
import base64, hashlib

FORBIDDEN = ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'Function(', 'setTimeout', 'setInterval',
             'fetch(', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'sendBeacon', 'import(', 'importScripts', 'window.open', 'location', 'postMessage',
             'localStorage', 'sessionStorage', 'indexedDB', 'cookie', 'javascript:', 'srcdoc', 'createElement(\'script\')', 'createElement("script")')

SCRIPT = r'''
(function () {
  'use strict';
  var root = document.documentElement;
  root.classList.add('js');
  function one(sel, ctx) { return (ctx || document).querySelector(sel); }
  function all(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }

  // ---- reveal the controls that only work with scripts
  all('[data-js]').forEach(function (el) { el.hidden = false; });

  // ---- views: the overview, or one group laid out in full
  var overview = one('#tm-overview');
  var crumbs = one('#crumbs');
  var crumbName = one('#crumb-name');
  function showView(g) {
    var found = false;
    all('.tm[data-g]').forEach(function (v) {
      var on = v.getAttribute('data-g') === g;
      v.hidden = !on;
      if (on) { found = true; crumbName.textContent = v.getAttribute('data-title'); }
    });
    overview.hidden = found;
    crumbs.hidden = !found;
    closeDetail();
  }

  // ---- detail panel: a copy of the pre-rendered, pre-escaped block for the clicked thing
  var panel = one('#detail');
  var body = one('#detail-body');
  function closeDetail() { panel.hidden = true; body.textContent = ''; }
  function showDetail(n) {
    var src = one('#dstore [data-d="' + n + '"]');
    if (!src) { return; }
    body.textContent = '';
    body.appendChild(src.cloneNode(true));
    panel.hidden = false;
    syncSelection();
    panel.scrollIntoView({ block: 'nearest' });
  }

  // ---- selection: keys the user ticks, copied as text. Never run, never sent.
  var selected = new Map();
  var bar = one('#selbar');
  var count = one('#selcount');
  var bAccept = one('#copy-accept');
  var bTidy = one('#copy-tidy');
  var msg = one('#msg');
  var box = one('#copybox');
  function toggle(key, cls) {
    if (selected.has(key)) { selected.delete(key); } else { selected.set(key, cls); }
    syncSelection();
  }
  function syncSelection() {
    all('input.pick').forEach(function (c) { c.checked = selected.has(c.getAttribute('data-key')); });
    all('button.sel').forEach(function (b) { b.textContent = selected.has(b.getAttribute('data-key')) ? 'Selected (click to remove)' : 'Select'; });
    var n = selected.size;
    bar.hidden = n === 0;
    count.textContent = n + (n === 1 ? ' item selected' : ' items selected');
    var allOrphans = n > 0;
    selected.forEach(function (cls) { if (cls !== 'orphaned') { allOrphans = false; } });
    bAccept.disabled = n === 0;
    bTidy.disabled = !allOrphans;
    bTidy.title = allOrphans || n === 0 ? '' : 'Only orphan candidates can go to /dotfiles:tidy';
  }
  function lines(command) { return command + '\n' + Array.from(selected.keys()).join('\n'); }
  function fallbackCopy(text) {
    box.hidden = false;
    box.value = text;
    box.focus();
    box.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    if (ok) { box.hidden = true; }
    return ok;
  }
  function copy(text, what) {
    function done(ok) {
      msg.textContent = ok ? 'Copied ' + what + '. Paste it into Claude Code.' : 'Could not copy automatically. The text is selected below: press Cmd+C or Ctrl+C.';
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(fallbackCopy(text)); });
    } else {
      done(fallbackCopy(text));
    }
  }
  bAccept.addEventListener('click', function () { copy(lines('/dotfiles:accept'), 'the accept command'); });
  bTidy.addEventListener('click', function () { copy(lines('/dotfiles:tidy'), 'the tidy command'); });
  one('#clear').addEventListener('click', function () { selected.clear(); box.hidden = true; msg.textContent = 'Selection cleared.'; syncSelection(); });

  // ---- filters and search: hide what does not match, never re-layout
  var query = one('#q');
  var chips = all('button[data-f]');
  var shown = one('#shown');
  function applyFilter() {
    var q = query.value.trim().toLowerCase();
    var want = {};
    var any = false;
    chips.forEach(function (c) { if (c.getAttribute('aria-pressed') === 'true') { want[c.getAttribute('data-f')] = true; any = true; } });
    var total = 0, vis = 0;
    all('[data-name]').forEach(function (el) {
      var okText = q === '' || el.getAttribute('data-name').indexOf(q) !== -1;
      var cls = el.getAttribute('data-cls');
      var okCls = !any || want[cls] === true || (want.accepted === true && el.getAttribute('data-acc') === '1');
      var keep = okText && okCls;
      el.hidden = !keep;
      if (!el.closest('.tm[data-g]')) {
        total += 1;
        if (keep) { vis += 1; }
      }
    });
    shown.textContent = q === '' && !any ? '' : 'Showing ' + vis + ' of ' + total;
  }
  query.addEventListener('input', applyFilter);
  chips.forEach(function (c) {
    c.addEventListener('click', function () {
      c.setAttribute('aria-pressed', c.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
      applyFilter();
    });
  });

  // ---- sorting the manager tables
  function sortTable(btn) {
    var th = btn.parentNode;
    var table = th.parentNode.parentNode.parentNode;
    var col = Array.prototype.indexOf.call(th.parentNode.children, th);
    var asc = th.getAttribute('aria-sort') !== 'ascending';
    var numeric = btn.getAttribute('data-type') === 'num';
    var tbody = one('tbody', table);
    var rows = all('tr', tbody);
    function val(r) {
      var cell = r.children[col];
      var v = cell.hasAttribute('data-v') ? cell.getAttribute('data-v') : cell.textContent.toLowerCase();
      return numeric ? Number(v) || 0 : v;
    }
    rows.sort(function (a, b) {
      var x = val(a), y = val(b);
      return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
    all('th', th.parentNode).forEach(function (h) { h.removeAttribute('aria-sort'); });
    th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
  }

  // ---- one delegated click handler
  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t.closest) { return; }
    var go = t.closest('[data-go]');
    if (go) { showView(go.getAttribute('data-go')); return; }
    if (t.closest('[data-back]')) { showView(null); return; }
    if (t.closest('#detail-close')) { closeDetail(); return; }
    var pick = t.closest('input.pick');
    if (pick) { toggle(pick.getAttribute('data-key'), pick.getAttribute('data-cls')); return; }
    var sel = t.closest('button.sel');
    if (sel) { toggle(sel.getAttribute('data-key'), sel.getAttribute('data-cls')); return; }
    var sortBtn = t.closest('th button[data-type]');
    if (sortBtn) { sortTable(sortBtn); return; }
    var block = t.closest('.t[data-n]');
    if (block) { showDetail(block.getAttribute('data-n')); }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') {
      if (!panel.hidden) { closeDetail(); } else if (!crumbs.hidden) { showView(null); }
      return;
    }
    if ((ev.key === 'Enter' || ev.key === ' ') && ev.target.matches && ev.target.matches('.t[tabindex], .gl[data-go]')) {
      ev.preventDefault();
      ev.target.click();
    }
  });
  syncSelection();
})();
'''


def script_hash(script=SCRIPT):
    return 'sha256-' + base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode()


def csp(script=SCRIPT):
    return f"default-src 'none'; script-src '{script_hash(script)}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
