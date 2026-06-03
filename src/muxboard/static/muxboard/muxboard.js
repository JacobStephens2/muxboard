(function () {
  'use strict';

  // muxboard dashboard controller: refresh, kill (modal), create (modal),
  // and relative-time formatting. The attach link is a plain <a> to the
  // attach page; no JS needed for it.

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  var BASE = (document.body.getAttribute('data-muxboard-base') || '').replace(/\/$/, '');

  function api(path) { return BASE + path; }

  // ---------- relative time ----------

  function formatRelative(unix) {
    var now = Math.floor(Date.now() / 1000);
    var d = now - unix;
    if (d < 0) d = 0;
    if (d < 60) return d + 's ago';
    if (d < 3600) return Math.floor(d / 60) + 'm ago';
    if (d < 86400) {
      var h = Math.floor(d / 3600);
      var m = Math.floor((d % 3600) / 60);
      return h + 'h' + (m < 10 ? '0' : '') + m + 'm ago';
    }
    return Math.floor(d / 86400) + 'd ago';
  }

  function applyRelativeTimes(root) {
    $$('time[data-relative-unix]', root).forEach(function (el) {
      var u = parseInt(el.getAttribute('data-relative-unix'), 10);
      if (!u || isNaN(u)) return;
      el.textContent = formatRelative(u);
      el.title = new Date(u * 1000).toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
    });
  }

  // ---------- refresh ----------

  function refreshAll(btn) {
    if (!btn) return;
    var orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    fetch(api('/api/refresh'), { method: 'POST', credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function () { window.location.reload(); })
      .catch(function () {
        btn.disabled = false;
        btn.textContent = orig;
        window.alert('Refresh failed.');
      });
  }

  // ---------- kill ----------

  var killDialog = null, killCtx = null;

  function openKill(host, user, name) {
    if (!killDialog) killDialog = $('[data-mb-kill-dialog]');
    if (!killDialog) return;
    killCtx = { host: host, user: user, name: name };
    $('[data-mb-kill-label]', killDialog).textContent = name;
    $('[data-mb-kill-host-label]', killDialog).textContent = host;
    $('[data-mb-kill-user-label]', killDialog).textContent = user;
    var c = $('[data-mb-kill-confirm]', killDialog);
    if (c) c.value = name;
    var err = $('[data-mb-kill-error]', killDialog);
    err.hidden = true; err.textContent = '';
    var go = $('[data-mb-kill-go]', killDialog);
    go.disabled = false; go.textContent = 'Kill session';
    killDialog.showModal();
  }

  function closeKill() { if (killDialog && killDialog.open) killDialog.close(); killCtx = null; }

  function doKill() {
    if (!killCtx) return;
    var go = $('[data-mb-kill-go]', killDialog);
    var err = $('[data-mb-kill-error]', killDialog);
    err.hidden = true; err.textContent = '';
    var c = $('[data-mb-kill-confirm]', killDialog);
    var val = c ? (c.value || '').trim() : killCtx.name;
    if (val !== killCtx.name) {
      err.hidden = false;
      err.textContent = 'Type the session name (' + killCtx.name + ') to confirm.';
      return;
    }
    go.disabled = true; go.textContent = 'Killing...';
    var body = new URLSearchParams();
    body.set('name', killCtx.name);
    body.set('confirm', val);
    fetch(api('/api/' + encodeURIComponent(killCtx.host) + '/' + encodeURIComponent(killCtx.user) + '/kill'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.body.ok) {
          err.hidden = false;
          err.textContent = (res.body && res.body.error) || 'failed';
          go.disabled = false; go.textContent = 'Retry kill';
          return;
        }
        closeKill(); window.location.reload();
      })
      .catch(function (e) {
        err.hidden = false; err.textContent = String(e);
        go.disabled = false; go.textContent = 'Retry kill';
      });
  }

  // ---------- create ----------

  var newDialog = null, newCtx = null;

  function openNew(host, user) {
    if (!newDialog) newDialog = $('[data-mb-new-dialog]');
    if (!newDialog) return;
    newCtx = { host: host, user: user };
    $('[data-mb-new-host-label]', newDialog).textContent = host;
    $('[data-mb-new-user-label]', newDialog).textContent = user;
    var nameInput = $('[data-mb-new-name]', newDialog);
    var cmdInput = $('[data-mb-new-cmd]', newDialog);
    nameInput.value = ''; cmdInput.value = '';
    var err = $('[data-mb-new-error]', newDialog);
    err.hidden = true; err.textContent = '';
    var go = $('[data-mb-new-go]', newDialog);
    go.disabled = false; go.textContent = 'Create';
    newDialog.showModal();
    setTimeout(function () { nameInput.focus(); }, 0);
  }

  function closeNew() { if (newDialog && newDialog.open) newDialog.close(); newCtx = null; }

  function doCreate() {
    if (!newCtx) return;
    var nameInput = $('[data-mb-new-name]', newDialog);
    var cmdInput = $('[data-mb-new-cmd]', newDialog);
    var err = $('[data-mb-new-error]', newDialog);
    var go = $('[data-mb-new-go]', newDialog);
    var name = (nameInput.value || '').trim();
    var cmd = (cmdInput.value || '').trim();
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) {
      err.hidden = false;
      err.textContent = 'Name must be 1-64 chars of [A-Za-z0-9_-].';
      return;
    }
    err.hidden = true; err.textContent = '';
    go.disabled = true; go.textContent = 'Creating...';
    var body = new URLSearchParams();
    body.set('name', name);
    if (cmd) body.set('command', cmd);
    fetch(api('/api/' + encodeURIComponent(newCtx.host) + '/' + encodeURIComponent(newCtx.user) + '/create'), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.body.ok) {
          err.hidden = false;
          err.textContent = (res.body && res.body.error) || 'failed';
          go.disabled = false; go.textContent = 'Retry create';
          return;
        }
        closeNew(); window.location.reload();
      })
      .catch(function (e) {
        err.hidden = false; err.textContent = String(e);
        go.disabled = false; go.textContent = 'Retry create';
      });
  }

  // ---------- wire up ----------

  document.addEventListener('DOMContentLoaded', function () {
    applyRelativeTimes(document);

    document.addEventListener('click', function (ev) {
      var t = ev.target;
      if (!t || !t.closest) return;
      if (t.closest('[data-mb-refresh]')) { ev.preventDefault(); refreshAll(t.closest('[data-mb-refresh]')); return; }
      var kb = t.closest('[data-mb-kill-host]');
      if (kb) {
        ev.preventDefault();
        openKill(kb.getAttribute('data-mb-kill-host'), kb.getAttribute('data-mb-kill-user'), kb.getAttribute('data-mb-kill-name'));
        return;
      }
      if (t.closest('[data-mb-kill-cancel]')) { ev.preventDefault(); closeKill(); return; }
      if (t.closest('[data-mb-kill-go]')) { ev.preventDefault(); doKill(); return; }
      var nb = t.closest('[data-mb-new-host]');
      if (nb) {
        ev.preventDefault();
        openNew(nb.getAttribute('data-mb-new-host'), nb.getAttribute('data-mb-new-user'));
        return;
      }
      if (t.closest('[data-mb-new-cancel]')) { ev.preventDefault(); closeNew(); return; }
      if (t.closest('[data-mb-new-go]')) { ev.preventDefault(); doCreate(); return; }
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter') return;
      if (newDialog && newDialog.open && ev.target && ev.target.matches('[data-mb-new-name], [data-mb-new-cmd]')) {
        ev.preventDefault();
        doCreate();
      }
    });
  });
})();
