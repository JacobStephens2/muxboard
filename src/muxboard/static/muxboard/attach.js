/* Attach client: the Attach page's Link, Copy and Clipboard rules
 * (ADR-0001, ADR-0002) and the client half of the bridge wire protocol.
 *
 * The page wires DOM, xterm and the WebSocket to this module. Everything
 * the module needs from the browser (platform, open, clipboard, prompt,
 * notify) is passed in, so Node tests drive it with fakes. Regex Links and
 * the Copy join come from wrap-url.js, which the page loads first.
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
    if (!env || typeof env[name] !== type) {
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

  // ClipboardAddon provider: Clipboard push and Clipboard query (ADR-0001).
  function clipboardProvider(env) {
    var mode = requireDep(env, "mode", "string");
    var max = requireDep(env, "max", "number");
    var prompt = requireDep(env, "prompt", "function");
    var notify = requireDep(env, "notify", "function");
    var clipboard = env.clipboard;

    return {
      readText: function (sel) {
        // Reject (do not resolve '') so ClipboardAddon does not inject an
        // empty OSC 52 reply into the Session. Deny means no read.
        if (mode !== "read-write" || sel !== "c") {
          return Promise.reject(new Error("clipboard query disabled"));
        }
        return Promise.resolve(prompt()).then(function (ok) {
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
          notify("clipboard: refused (" + n + " B > 64 KiB)");
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

  return {
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
