/* Attach client: the Attach page's Link, Copy and Clipboard rules
 * (ADR-0001, ADR-0002), the client half of the bridge wire protocol, and
 * the Attach session controller that owns the page's behaviour.
 *
 * The page builds DOM, xterm and its addons and hands them to this module.
 * Everything the module needs from the browser (platform, open, clipboard,
 * the Clipboard query dialog, socket, timers, view callbacks) is passed in, so Node tests drive
 * it with fakes. Regex Links and the Copy join come from wrap-url.js, which
 * the page loads first.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./wrap-url.js"));
  } else {
    root.mbAttach = factory(root.mbWrapUrl);
  }
})(typeof self !== "undefined" ? self : this, function (wrap) {
  "use strict";

  if (!wrap) throw new Error("mbAttach: wrap-url.js must load first");

  function requireDep(env, name, type) {
    if (!env || typeof env[name] !== type || env[name] === null) {
      throw new TypeError("mbAttach: " + name + " must be a " + type);
    }
    return env[name];
  }

  function isMac(platform) {
    return /Mac|iPhone|iPad/.test(platform);
  }

  // Link activation: Cmd on macOS, Ctrl elsewhere; http(s) only.
  function links(env) {
    var platform = requireDep(env, "platform", "string");
    var open = requireDep(env, "open", "function");
    var title = requireDep(env, "title", "function");
    var mac = isMac(platform);

    function modifierHeld(ev) {
      return mac ? !!(ev && ev.metaKey) : !!(ev && ev.ctrlKey);
    }

    function activate(ev, uri) {
      if (!modifierHeld(ev)) return;
      var url;
      try { url = new URL(uri); } catch (e) { return; }
      if (url.protocol !== "http:" && url.protocol !== "https:") return;
      open(url.href, "_blank", "noopener,noreferrer");
    }

    function hover(_ev, text) { title(text || ""); }
    function leave() { title(""); }

    // Regex Links: join xterm soft wrap and Session hard wrap (Claude login
    // URLs). WebLinksAddon only concatenates isWrapped rows, so a TUI newline
    // at cols made the Link the first line. OSC 8 uses the handler instead.
    function provider(term) {
      return {
        provideLinks: function (y, callback) {
          var rows = wrap.bufferLines(term);
          var found = wrap.linksOnLine(rows, y - 1, term.cols, activate);
          found.forEach(function (link) {
            link.hover = hover;
            link.leave = leave;
          });
          callback(found.length ? found : undefined);
        },
      };
    }

    return {
      handler: {
        activate: activate,
        hover: hover,
        leave: leave,
        allowNonHttpProtocols: false,
      },
      provider: provider,
    };
  }

  // Copy: resolves "copied", "empty" or "failed"; never rejects.
  function copy(term, clipboard) {
    var text = wrap.copyText(term.getSelection());
    if (!text) return Promise.resolve("empty");
    if (!clipboard || typeof clipboard.writeText !== "function") {
      return Promise.resolve("failed");
    }
    return Promise.resolve()
      .then(function () { return clipboard.writeText(text); })
      .then(function () { return "copied"; }, function () { return "failed"; });
  }

  // Ctrl/Cmd+C copies only with a Selection; otherwise it reaches the Session.
  function isCopyChord(ev, hasSelection) {
    return (
      !!hasSelection &&
      ev.type === "keydown" &&
      !!(ev.ctrlKey || ev.metaKey) &&
      !ev.altKey &&
      !ev.shiftKey &&
      (ev.key === "c" || ev.key === "C")
    );
  }

  function utf8Bytes(s) {
    return new TextEncoder().encode(s || "").length;
  }

  // The push cap as the refusal notice states it: whole KiB, else bytes.
  function capText(max) {
    return max > 0 && max % 1024 === 0 ? max / 1024 + " KiB" : max + " B";
  }

  // Clipboard query consent: resolves true only on Allow. Deny, or the
  // dialog's own close (Escape), resolves false. Settles once, then closes
  // the dialog and drops its listeners.
  function askDialog(dialog) {
    return new Promise(function (resolve) {
      var allow = dialog.querySelector("[data-mb-clip-allow]");
      var deny = dialog.querySelector("[data-mb-clip-deny]");
      var settled = false;
      function done(ok) {
        if (settled) return;
        settled = true;
        if (allow) allow.removeEventListener("click", onAllow);
        if (deny) deny.removeEventListener("click", onDeny);
        dialog.removeEventListener("close", onClose);
        if (dialog.open) dialog.close();
        resolve(ok);
      }
      function onAllow(ev) { ev.preventDefault(); done(true); }
      function onDeny(ev) { ev.preventDefault(); done(false); }
      function onClose() { done(false); }
      if (allow) allow.addEventListener("click", onAllow);
      if (deny) deny.addEventListener("click", onDeny);
      dialog.addEventListener("close", onClose);
      try { dialog.showModal(); } catch (e) { done(false); }
    });
  }

  // ClipboardAddon provider: Clipboard push and Clipboard query (ADR-0001).
  // The dialog is rendered only in read-write mode, so a missing one denies
  // each query rather than failing construction (Kill and Copy keep working).
  function clipboardProvider(env) {
    var mode = requireDep(env, "mode", "string");
    var max = requireDep(env, "max", "number");
    var notify = requireDep(env, "notify", "function");
    var clipboard = env.clipboard;
    var dialog = env.dialog;
    var asking = false;

    // One prompt at a time: a query while one is open is denied, so one
    // Allow never grants two reads.
    function ask() {
      if (!dialog || typeof dialog.showModal !== "function" || asking) {
        return Promise.resolve(false);
      }
      asking = true;
      return askDialog(dialog).then(function (ok) {
        asking = false;
        return ok;
      });
    }

    return {
      readText: function (sel) {
        // Reject (do not resolve '') so ClipboardAddon does not inject an
        // empty OSC 52 reply into the Session. Deny means no read.
        if (mode !== "read-write" || sel !== "c") {
          return Promise.reject(new Error("clipboard query disabled"));
        }
        return ask().then(function (ok) {
          if (!ok) throw new Error("clipboard query denied");
          return clipboard.readText();
        }).then(function (text) {
          if (!text) throw new Error("clipboard empty");
          return text;
        });
      },
      writeText: function (sel, text) {
        if (sel !== "c") return Promise.resolve();
        var n = utf8Bytes(text);
        if (n > max) {
          notify("clipboard: refused (" + n + " B > " + capText(max) + ")");
          return Promise.resolve();
        }
        return Promise.resolve()
          .then(function () { return clipboard.writeText(text); })
          .then(function () {
            notify("clipboard: " + n + " B from session");
          }, function () {
            notify("clipboard: write failed");
          });
      },
    };
  }

  function encodeInput(data) {
    return JSON.stringify({ type: "input", data: data });
  }

  function encodeResize(cols, rows) {
    return JSON.stringify({ type: "resize", cols: cols, rows: rows });
  }

  function encodePing() {
    return JSON.stringify({ type: "ping" });
  }

  // Incoming frame -> {type:"bytes"|"error"|"text"|"blob"}, or null.
  function decode(data) {
    if (data instanceof ArrayBuffer) {
      return { type: "bytes", bytes: new Uint8Array(data) };
    }
    if (typeof data === "string") {
      try {
        var obj = JSON.parse(data);
        if (obj && obj.type === "error") {
          return { type: "error", message: obj.message };
        }
      } catch (e) { /* not JSON - raw text */ }
      return { type: "text", text: data };
    }
    if (data && data.arrayBuffer) {
      return {
        type: "blob",
        bytes: data.arrayBuffer().then(function (buf) { return new Uint8Array(buf); }),
      };
    }
    return null;
  }

  // Attach session controller: the page's connection lifecycle, input, Kill, status
  // flash and Copy feedback. The page builds xterm and the DOM and passes
  // them in with the socket factory, timers and view callbacks.
  function controller(env) {
    var target = requireDep(env, "target", "object");
    requireDep(target, "name", "string");
    var base = requireDep(env, "base", "string");
    var location = requireDep(env, "location", "object");
    requireDep(location, "host", "string");
    var openSocket = requireDep(env, "socket", "function");
    var term = requireDep(env, "term", "object");
    var fit = requireDep(env, "fit", "function");
    var timers = requireDep(env, "timers", "object");
    var setTimeoutFn = requireDep(timers, "setTimeout", "function");
    var clearTimeoutFn = requireDep(timers, "clearTimeout", "function");
    var setIntervalFn = requireDep(timers, "setInterval", "function");
    var sessions = requireDep(env, "sessions", "object");
    var confirm = requireDep(env, "confirm", "function");
    var closeWindow = requireDep(env, "closeWindow", "function");
    var view = requireDep(env, "view", "object");
    // Optional: navigator.clipboard is undefined off secure origins, and
    // copy() then reports "failed".
    var clipboard = env.clipboard;

    var ws = null;
    var connected = false;
    var killed = false;
    var state = "connecting";
    var resizeTimer = null;
    var flashTimer = null;
    var copiedTimer = null;

    function socketUrl() {
      var proto = location.protocol === "https:" ? "wss:" : "ws:";
      return proto + "//" + location.host + base +
        "/ws/" + encodeURIComponent(target.host) +
        "/" + encodeURIComponent(target.user) +
        "/" + encodeURIComponent(target.name);
    }

    function setStatus(next, text) {
      state = next;
      view.status(next, text);
    }

    // Show a message, then restore "connected" only if still connected.
    function flash(text) {
      if (killed) return;
      if (flashTimer) clearTimeoutFn(flashTimer);
      setStatus(state, text);
      flashTimer = setTimeoutFn(function () {
        flashTimer = null;
        if (connected) setStatus("open", "connected");
      }, 2500);
    }

    function closeSocket() {
      try { if (ws) ws.close(); } catch (e) { /* already closed */ }
    }

    function renderKilled() {
      killed = true;
      connected = false;
      closeSocket();
      setStatus("closed", "session killed");
      view.killError(null);
      view.killed();
    }

    // Kill: resolves once the outcome is rendered. A browser refuses
    // closeWindow on a tab it did not open, so the killed state renders too.
    function kill() {
      if (!confirm("Kill session " + target.name + "?")) return Promise.resolve();
      view.killError(null);
      view.killBusy(true);
      return sessions.kill(target).then(function (out) {
        if (!out.ok) {
          view.killError(out.error);
          setStatus("error", "kill failed");
          view.killBusy(false);
          return;
        }
        closeWindow();
        renderKilled();
      });
    }

    function sendResize() {
      if (!connected) return;
      fit();
      ws.send(encodeResize(term.cols, term.rows));
    }

    function onMessage(ev) {
      var frame = decode(ev.data);
      if (!frame) return;
      if (frame.type === "bytes") {
        term.write(frame.bytes);
      } else if (frame.type === "error") {
        term.writeln("\r\n\x1b[31m[bridge error] " + frame.message + "\x1b[0m");
      } else if (frame.type === "text") {
        term.write(frame.text);
      } else if (frame.type === "blob") {
        frame.bytes.then(function (bytes) { term.write(bytes); });
      }
    }

    function syncCopyButton() {
      var selected = term.hasSelection();
      view.copyButton({
        disabled: !selected,
        title: selected ? "" : "nothing selected",
        label: copiedTimer ? "Copied" : "Copy",
      });
    }

    function showCopied() {
      if (copiedTimer) clearTimeoutFn(copiedTimer);
      copiedTimer = setTimeoutFn(function () {
        copiedTimer = null;
        syncCopyButton();
      }, 1500);
      syncCopyButton();
    }

    // Copy: resolves once the outcome is rendered.
    function doCopy() {
      return copy(term, clipboard).then(function (outcome) {
        if (outcome === "copied") showCopied();
        else if (outcome === "failed") flash("copy failed");
      });
    }

    syncCopyButton();
    term.onSelectionChange(function () { syncCopyButton(); });
    term.attachCustomKeyEventHandler(function (ev) {
      if (isCopyChord(ev, term.hasSelection())) {
        ev.preventDefault();
        doCopy();
        return false;
      }
      return true;
    });

    term.onData(function (data) {
      if (!connected) return;
      ws.send(encodeInput(data));
    });

    function start() {
      setStatus("connecting", "connecting...");
      ws = openSocket(socketUrl());
      ws.binaryType = "arraybuffer";

      ws.addEventListener("open", function () {
        if (killed) {
          closeSocket();
          return;
        }
        connected = true;
        setStatus("open", "connected");
        sendResize();
        term.focus();
      });
      ws.addEventListener("message", onMessage);
      ws.addEventListener("close", function (ev) {
        connected = false;
        if (killed) return;
        setStatus("closed", "disconnected" + (ev.code ? " (" + ev.code + ")" : ""));
        term.writeln("\r\n\x1b[33m[bridge closed]\x1b[0m");
      });
      ws.addEventListener("error", function () {
        if (killed) return;
        setStatus("error", "connection error");
      });

      setIntervalFn(function () {
        if (connected) { try { ws.send(encodePing()); } catch (e) { /* closing */ } }
      }, 30000);
    }

    // Window resize: debounced; sent only while connected.
    function resize() {
      if (resizeTimer) clearTimeoutFn(resizeTimer);
      resizeTimer = setTimeoutFn(function () {
        resizeTimer = null;
        sendResize();
      }, 80);
    }

    return {
      start: start,
      resize: resize,
      kill: kill,
      flash: flash,
      copy: doCopy,
    };
  }

  return {
    controller: controller,
    links: links,
    copy: copy,
    isCopyChord: isCopyChord,
    clipboardProvider: clipboardProvider,
    encodeInput: encodeInput,
    encodeResize: encodeResize,
    encodePing: encodePing,
    decode: decode,
  };
});
