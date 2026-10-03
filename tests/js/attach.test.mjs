#!/usr/bin/env node
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");
const attach = require(path.join(root, "src/muxboard/static/muxboard/attach.js"));

let failed = 0;

function check(name, pred, detail) {
  const ok = !!pred;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failed++;
}

function fakeOpen() {
  const calls = [];
  const open = (url, target, features) => calls.push({ url, target, features });
  open.calls = calls;
  return open;
}

function fakeClipboard(initial, opts) {
  const o = opts || {};
  const clip = {
    text: initial || "",
    writes: [],
    reads: 0,
    writeText(t) {
      if (o.failWrite) return Promise.reject(new Error("denied"));
      clip.writes.push(t);
      clip.text = t;
      return Promise.resolve();
    },
    readText() {
      clip.reads++;
      return Promise.resolve(clip.text);
    },
  };
  return clip;
}

function fakeTerm(rows, cols, selection) {
  return {
    cols,
    buffer: {
      active: {
        length: rows.length,
        getLine(i) {
          const r = rows[i];
          if (!r) return undefined;
          return {
            isWrapped: !!r.isWrapped,
            length: cols,
            translateToString() { return r.text; },
          };
        },
      },
    },
    getSelection() { return selection || ""; },
  };
}

function links(platform, open, title) {
  return attach.links({ platform, open, title: title || (() => {}) });
}

const URL_OK = "https://example.com/path";

// Link: OSC 8 handler
{
  const open = fakeOpen();
  links("Linux x86_64", open).handler.activate({ ctrlKey: true }, URL_OK);
  check(
    "Link: Ctrl+click on Linux opens noopener,noreferrer",
    open.calls.length === 1 &&
      open.calls[0].url === URL_OK &&
      open.calls[0].target === "_blank" &&
      open.calls[0].features === "noopener,noreferrer"
  );
}
{
  const open = fakeOpen();
  links("MacIntel", open).handler.activate({ metaKey: true }, URL_OK);
  check("Link: Cmd+click on macOS opens", open.calls.length === 1);
}
{
  const open = fakeOpen();
  links("iPhone", open).handler.activate({ metaKey: true }, URL_OK);
  check("Link: Cmd+click on iOS opens", open.calls.length === 1);
}
{
  const open = fakeOpen();
  links("MacIntel", open).handler.activate({ ctrlKey: true }, URL_OK);
  check("Link: Ctrl+click on macOS does not open", open.calls.length === 0);
}
{
  const open = fakeOpen();
  links("Linux x86_64", open).handler.activate({ metaKey: true }, URL_OK);
  check("Link: Meta+click on Linux does not open", open.calls.length === 0);
}
{
  const open = fakeOpen();
  const l = links("Linux x86_64", open);
  l.handler.activate({}, URL_OK);
  l.handler.activate(undefined, URL_OK);
  check("Link: no modifier does not open", open.calls.length === 0);
}
for (const uri of [
  "javascript:alert(1)",
  "file:///etc/passwd",
  "data:text/html,<b>x</b>",
  "not a url",
  "",
]) {
  const open = fakeOpen();
  links("Linux x86_64", open).handler.activate({ ctrlKey: true }, uri);
  check(`Link: ${JSON.stringify(uri)} never opens`, open.calls.length === 0);
}
{
  const open = fakeOpen();
  links("Linux x86_64", open).handler.activate({ ctrlKey: true }, "http://example.com");
  check(
    "Link: http opens with the normalised href",
    open.calls.length === 1 && open.calls[0].url === "http://example.com/"
  );
}
{
  const titles = [];
  const l = links("Linux x86_64", fakeOpen(), (t) => titles.push(t));
  l.handler.hover({}, URL_OK);
  l.handler.leave();
  check(
    "Link: OSC 8 hover sets the title and leave clears it",
    titles.length === 2 && titles[0] === URL_OK && titles[1] === ""
  );
  check(
    "Link: OSC 8 handler disallows non-http protocols",
    l.handler.allowNonHttpProtocols === false
  );
}

// Link: regex provider over a fake hard-wrapped buffer
{
  const COLS = 40;
  const full = "https://example.com/oauth?code=" + "A".repeat(COLS * 2 - 10);
  const rows = [
    { text: "Open this:" },
    { text: full.slice(0, COLS) },
    { text: full.slice(COLS, COLS * 2) },
    { text: full.slice(COLS * 2) },
  ];
  const term = fakeTerm(rows, COLS);
  const open = fakeOpen();
  const titles = [];
  const l = links("Linux x86_64", open, (t) => titles.push(t));
  let got = "unset";
  // xterm rows are 1-based; row 3 is the middle fragment.
  l.provider(term).provideLinks(3, (ls) => { got = ls; });
  check(
    "Link provider: hard-wrapped buffer yields the full URL",
    Array.isArray(got) && got.length === 1 && got[0].text === full,
    Array.isArray(got) ? `len=${got[0] && got[0].text.length}` : String(got)
  );
  if (Array.isArray(got) && got.length === 1) {
    got[0].activate({}, got[0].text);
    check("Link provider: no modifier does not open", open.calls.length === 0);
    got[0].activate({ ctrlKey: true }, got[0].text);
    check(
      "Link provider: Ctrl+click opens the full URL",
      open.calls.length === 1 && open.calls[0].url === full
    );
    got[0].hover({}, got[0].text);
    got[0].leave();
    check(
      "Link provider: hover and leave set and clear the title",
      titles.length === 2 && titles[0] === full && titles[1] === ""
    );
  }
  let none = "unset";
  l.provider(term).provideLinks(1, (ls) => { none = ls; });
  check("Link provider: a row with no URL yields undefined", none === undefined);
}

// Copy
async function copyCases() {
  {
    const clip = fakeClipboard();
    const out = await attach.copy(fakeTerm([], 80, ""), clip);
    check("Copy: empty Selection is no write", out === "empty" && clip.writes.length === 0);
  }
  {
    const COLS = 30;
    const full = "https://example.com/x?y=" + "B".repeat(COLS + 10);
    const sel = full.slice(0, COLS) + "\n" + full.slice(COLS);
    const clip = fakeClipboard();
    const out = await attach.copy(fakeTerm([], COLS, sel), clip);
    check(
      "Copy: hard-wrapped URL Selection is written joined",
      out === "copied" && clip.writes.length === 1 && clip.writes[0] === full
    );
  }
  {
    const clip = fakeClipboard();
    const out = await attach.copy(fakeTerm([], 80, "hello\nworld"), clip);
    check(
      "Copy: prose keeps its newlines",
      out === "copied" && clip.writes[0] === "hello\nworld"
    );
  }
  {
    const clip = fakeClipboard("", { failWrite: true });
    const out = await attach.copy(fakeTerm([], 80, "text"), clip);
    check("Copy: clipboard rejection is failed", out === "failed");
  }
  {
    const a = await attach.copy(fakeTerm([], 80, "text"), undefined);
    const b = await attach.copy(fakeTerm([], 80, "text"), {});
    check("Copy: missing clipboard is failed", a === "failed" && b === "failed");
  }
}

// Chord
function chordCases() {
  const kd = (o) => Object.assign({ type: "keydown", key: "c" }, o);
  check("Chord: Ctrl+C with a Selection copies", attach.isCopyChord(kd({ ctrlKey: true }), true));
  check("Chord: Cmd+C with a Selection copies", attach.isCopyChord(kd({ metaKey: true }), true));
  check("Chord: Ctrl+C with Caps Lock (C) copies", attach.isCopyChord(kd({ ctrlKey: true, key: "C" }), true));
  check(
    "Chord: Ctrl+C without a Selection goes to the Session",
    !attach.isCopyChord(kd({ ctrlKey: true }), false)
  );
  check(
    "Chord: Ctrl+Shift+C goes to the Session",
    !attach.isCopyChord(kd({ ctrlKey: true, shiftKey: true }), true)
  );
  check(
    "Chord: Ctrl+Alt+C goes to the Session",
    !attach.isCopyChord(kd({ ctrlKey: true, altKey: true }), true)
  );
  check(
    "Chord: keyup goes to the Session",
    !attach.isCopyChord(kd({ ctrlKey: true, type: "keyup" }), true)
  );
  check("Chord: plain c goes to the Session", !attach.isCopyChord(kd({}), true));
  check(
    "Chord: Ctrl+V goes to the Session",
    !attach.isCopyChord(kd({ ctrlKey: true, key: "v" }), true)
  );
}

// Clipboard push and query

// Listeners keyed by event type; dispatch calls each with a fake event.
function fakeTarget() {
  const listeners = {};
  return {
    listeners,
    addEventListener(type, fn) {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
    },
    dispatch(type) {
      for (const fn of (listeners[type] || []).slice()) fn({ preventDefault() {} });
    },
    count() {
      return Object.values(listeners).reduce((n, fns) => n + fns.length, 0);
    },
  };
}

// The Attach page's Clipboard query dialog: showModal opens, close fires
// "close" as the browser does (Escape included).
function fakeDialog() {
  const allow = fakeTarget();
  const deny = fakeTarget();
  const dlg = Object.assign(fakeTarget(), {
    open: false,
    shows: 0,
    allow,
    deny,
    showModal() {
      dlg.shows++;
      dlg.open = true;
    },
    close() {
      if (!dlg.open) return;
      dlg.open = false;
      dlg.dispatch("close");
    },
    querySelector(sel) {
      if (sel === "[data-mb-clip-allow]") return allow;
      if (sel === "[data-mb-clip-deny]") return deny;
      return null;
    },
    listenerCount() {
      return dlg.count() + allow.count() + deny.count();
    },
  });
  return dlg;
}

function provider(opts) {
  const notes = [];
  const clip = "clipboard" in opts ? opts.clipboard : fakeClipboard(opts.clipText || "");
  const dialog = "dialog" in opts ? opts.dialog : fakeDialog();
  const p = attach.clipboardProvider({
    mode: opts.mode,
    max: opts.max == null ? 65536 : opts.max,
    dialog: dialog,
    clipboard: clip,
    notify: (m) => notes.push(m),
  });
  return { p, notes, clip, dialog };
}

// Let a pending readText run up to (or past) its prompt.
function tick() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function settled(promise) {
  return promise.then(
    (value) => ({ ok: true, value }),
    (error) => ({ ok: false, error })
  );
}

async function rejects(promise) {
  try {
    await promise;
    return false;
  } catch (e) {
    return true;
  }
}

async function clipboardCases() {
  {
    const { p, notes, clip } = provider({ mode: "write" });
    await p.writeText("p", "x");
    await p.writeText("s", "x");
    check(
      "Push: selection other than c is ignored",
      clip.writes.length === 0 && notes.length === 0
    );
  }
  {
    const { p, notes, clip } = provider({ mode: "write", max: 8 });
    await p.writeText("c", "12345678");
    check(
      "Push: payload at the cap is written and noticed with its byte count",
      clip.writes.length === 1 &&
        clip.writes[0] === "12345678" &&
        notes.length === 1 &&
        notes[0] === "clipboard: 8 B from session",
      JSON.stringify(notes)
    );
  }
  {
    // Three chars, nine UTF-8 bytes.
    const { p, notes, clip } = provider({ mode: "write", max: 8 });
    await p.writeText("c", "€€€");
    check(
      "Push: over the cap in multi-byte UTF-8 is refused with no write",
      clip.writes.length === 0 &&
        notes.length === 1 &&
        notes[0] === "clipboard: refused (9 B > 8 B)",
      JSON.stringify(notes)
    );
  }
  {
    const { p, notes } = provider({
      mode: "write",
      clipboard: fakeClipboard("", { failWrite: true }),
    });
    await p.writeText("c", "x");
    check(
      "Push: write failure is noticed",
      notes.length === 1 && notes[0] === "clipboard: write failed"
    );
  }
  {
    const notes = [];
    const p = attach.clipboardProvider({
      mode: "write", max: 10, clipboard: undefined, notify: (m) => notes.push(m),
    });
    await p.writeText("c", "x");
    check(
      "Push: missing clipboard is noticed as a write failure",
      notes.length === 1 && notes[0] === "clipboard: write failed"
    );
  }
  {
    const { p, notes } = provider({ mode: "write", max: 65536 });
    await p.writeText("c", "x".repeat(65537));
    check(
      "Push: refusal at a whole-KiB cap names it in KiB",
      notes.length === 1 && notes[0] === "clipboard: refused (65537 B > 64 KiB)",
      JSON.stringify(notes)
    );
  }
  {
    const { p, notes } = provider({ mode: "write", max: 1000 });
    await p.writeText("c", "x".repeat(1001));
    check(
      "Push: refusal at a cap that is not whole KiB names it in bytes",
      notes.length === 1 && notes[0] === "clipboard: refused (1001 B > 1000 B)",
      JSON.stringify(notes)
    );
  }
  {
    const { p, clip, dialog } = provider({ mode: "write", clipText: "secret" });
    const r = await rejects(p.readText("c"));
    check(
      "Query: write mode rejects without prompting",
      r && dialog.shows === 0 && clip.reads === 0
    );
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const r = await rejects(p.readText("p"));
    check(
      "Query: read-write with a selection other than c rejects",
      r && dialog.shows === 0 && clip.reads === 0
    );
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const pending = settled(p.readText("c"));
    await tick();
    const shown = dialog.open && dialog.shows === 1;
    dialog.allow.dispatch("click");
    const r = await pending;
    check(
      "Query: Allow opens the dialog once and reads the clipboard once",
      shown && r.ok && r.value === "secret" && clip.reads === 1,
      JSON.stringify({ shown, reads: clip.reads })
    );
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const pending = settled(p.readText("c"));
    await tick();
    dialog.deny.dispatch("click");
    const r = await pending;
    check("Query: Deny rejects without reading", !r.ok && clip.reads === 0 && !dialog.open);
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const pending = settled(p.readText("c"));
    await tick();
    dialog.close();
    const r = await pending;
    check("Query: closing the dialog (Escape) rejects without reading", !r.ok && clip.reads === 0);
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const pending = settled(p.readText("c"));
    await tick();
    dialog.allow.dispatch("click");
    await pending;
    const left = dialog.listenerCount();
    dialog.allow.dispatch("click");
    dialog.close();
    await tick();
    check(
      "Query: a settled prompt is closed and leaves no listeners",
      !dialog.open && left === 0 && clip.reads === 1,
      JSON.stringify({ left, reads: clip.reads })
    );
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const first = settled(p.readText("c"));
    await tick();
    const second = await settled(p.readText("c"));
    const shows = dialog.shows;
    dialog.allow.dispatch("click");
    const r = await first;
    check(
      "Query: a query while a prompt is open is denied; the open prompt still answers",
      !second.ok && shows === 1 && r.ok && r.value === "secret" && clip.reads === 1,
      JSON.stringify({ shows, reads: clip.reads })
    );
  }
  {
    const { p, clip, dialog } = provider({ mode: "read-write", clipText: "secret" });
    const first = settled(p.readText("c"));
    await tick();
    dialog.deny.dispatch("click");
    await first;
    const next = settled(p.readText("c"));
    await tick();
    const shows = dialog.shows;
    dialog.allow.dispatch("click");
    const r = await next;
    check(
      "Query: after a prompt settles the next query prompts again",
      shows === 2 && r.ok && clip.reads === 1,
      JSON.stringify({ shows, reads: clip.reads })
    );
  }
  {
    let built = true;
    let r = false;
    let clip;
    try {
      ({ p: built, clip } = provider({ mode: "read-write", clipText: "secret", dialog: null }));
      r = await rejects(built.readText("c"));
    } catch (e) {
      built = false;
    }
    check("Query: no dialog rejects with no read", built && r && clip.reads === 0);
  }
  {
    const dialog = fakeDialog();
    delete dialog.showModal;
    let built = true;
    let r = false;
    let clip;
    try {
      ({ p: built, clip } = provider({ mode: "read-write", clipText: "secret", dialog }));
      r = await rejects(built.readText("c"));
    } catch (e) {
      built = false;
    }
    check(
      "Query: a dialog without showModal rejects with no read",
      built && r && clip.reads === 0 && dialog.listenerCount() === 0
    );
  }
  {
    const { p, dialog } = provider({ mode: "read-write", clipText: "" });
    const pending = settled(p.readText("c"));
    await tick();
    dialog.allow.dispatch("click");
    const r = await pending;
    check("Query: an empty clipboard rejects rather than resolve ''", !r.ok);
  }
}

// Wire
function wireCases() {
  check(
    "Wire: input encodes",
    attach.encodeInput("ls\r") === JSON.stringify({ type: "input", data: "ls\r" })
  );
  check(
    "Wire: resize encodes",
    attach.encodeResize(120, 40) === JSON.stringify({ type: "resize", cols: 120, rows: 40 })
  );
  check("Wire: ping encodes", attach.encodePing() === JSON.stringify({ type: "ping" }));

  const buf = new Uint8Array([104, 105]).buffer;
  const bytes = attach.decode(buf);
  check(
    "Wire: ArrayBuffer decodes as bytes",
    bytes.type === "bytes" &&
      bytes.bytes instanceof Uint8Array &&
      bytes.bytes.length === 2 &&
      bytes.bytes[0] === 104
  );
  const err = attach.decode(JSON.stringify({ type: "error", message: "boom" }));
  check(
    "Wire: error frame decodes as a bridge error",
    err.type === "error" && err.message === "boom"
  );
  const otherJson = '{"type":"hello"}';
  const t1 = attach.decode(otherJson);
  check("Wire: other JSON decodes as text", t1.type === "text" && t1.text === otherJson);
  const t2 = attach.decode("plain \x1b[1mtext");
  check("Wire: non-JSON decodes as text", t2.type === "text" && t2.text === "plain \x1b[1mtext");
  const t3 = attach.decode("null");
  check("Wire: JSON null decodes as text", t3.type === "text" && t3.text === "null");
}

async function blobCase() {
  const blob = { arrayBuffer: () => Promise.resolve(new Uint8Array([1, 2, 3]).buffer) };
  const d = attach.decode(blob);
  const b = d.type === "blob" ? await d.bytes : null;
  check(
    "Wire: Blob decodes to bytes later",
    b instanceof Uint8Array && b.length === 3 && b[2] === 3
  );
  check("Wire: unknown frame decodes as null", attach.decode(42) === null);
}

function missingDeps() {
  let threw = 0;
  for (const fn of [
    () => attach.links({ open: () => {}, title: () => {} }),
    () => attach.links({ platform: "Linux", title: () => {} }),
    () => attach.clipboardProvider({ mode: "write", max: 1 }),
    () => attachSession({ sessions: null }),
    () => attachSession({ clock: { setTimeout() {}, clearTimeout() {} } }),
    () => attachSession({ location: {} }),
    () => attachSession({ target: null }),
  ]) {
    try { fn(); } catch (e) { threw++; }
  }
  check("Missing dependencies fail loudly", threw === 7);
}

// Attach session controller

function fakeClock() {
  let now = 0;
  let seq = 0;
  const timers = new Map();
  const clock = {
    setTimeout(fn, ms) { const id = ++seq; timers.set(id, { fn, at: now + ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn, ms) { const id = ++seq; timers.set(id, { fn, at: now + ms, every: ms }); return id; },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        let next = null;
        for (const [id, t] of timers) {
          if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
        }
        if (!next) break;
        const [id, t] = next;
        now = t.at;
        if (t.every) t.at += t.every; else timers.delete(id);
        t.fn();
      }
      now = end;
    },
  };
  return clock;
}

function fakeSocket() {
  const handlers = {};
  const sock = {
    url: null,
    binaryType: "blob",
    sent: [],
    closed: 0,
    addEventListener(type, fn) { (handlers[type] = handlers[type] || []).push(fn); },
    send(data) { sock.sent.push(JSON.parse(data)); },
    close() { sock.closed++; },
    emit(type, ev) { (handlers[type] || []).forEach((fn) => fn(ev || {})); },
  };
  return sock;
}

function fakeSessionTerm(selection) {
  const t = {
    cols: 80,
    rows: 24,
    selection: selection || "",
    written: [],
    focused: 0,
    dataFn: null,
    selFn: null,
    keyFn: null,
    write(d) { t.written.push(d); },
    writeln(d) { t.written.push(d + "\n"); },
    focus() { t.focused++; },
    onData(fn) { t.dataFn = fn; },
    onSelectionChange(fn) { t.selFn = fn; },
    attachCustomKeyEventHandler(fn) { t.keyFn = fn; },
    hasSelection() { return !!t.selection; },
    getSelection() { return t.selection; },
  };
  return t;
}

function fakeView() {
  const v = {
    statuses: [],
    busy: [],
    errors: [],
    killedCalls: 0,
    copyStates: [],
    status(state, text) { v.statuses.push([state, text]); },
    killBusy(b) { v.busy.push(b); },
    killError(msg) { v.errors.push(msg); },
    killed() { v.killedCalls++; },
    copyButton(state) { v.copyStates.push(state); },
    get last() { return v.statuses[v.statuses.length - 1]; },
  };
  return v;
}

function fakeSessions(outcome) {
  const s = {
    calls: [],
    kill(target) { s.calls.push(target); return Promise.resolve(outcome || { ok: true }); },
  };
  return s;
}

function attachSession(over) {
  const o = over || {};
  const env = {
    target: "target" in o ? o.target : { host: "web 1", user: "al/ice", name: "my work" },
    base: "/mux",
    location: o.location || { protocol: "https:", host: "board.example:8443" },
    socket: (url) => { env.sock = fakeSocket(); env.sock.url = url; return env.sock; },
    term: o.term || fakeSessionTerm(),
    fit: () => { env.fits++; },
    clipboard: o.clipboard || fakeClipboard(),
    sessions: "sessions" in o ? o.sessions : fakeSessions(),
    confirm: (msg) => { env.confirms.push(msg); return o.confirm !== false; },
    closeWindow: () => { env.order.push("closeWindow"); },
    timers: o.clock || fakeClock(),
    view: o.view || fakeView(),
    fits: 0,
    confirms: [],
    order: [],
  };
  const origKilled = env.view.killed;
  env.view.killed = () => { env.order.push("killed"); origKilled(); };
  env.session = attach.controller(env);
  return env;
}

function lifecycleCases() {
  {
    const env = attachSession();
    env.session.start();
    check(
      "Attach: socket URL is wss on https, segments encoded",
      env.sock.url === "wss://board.example:8443/mux/ws/web%201/al%2Fice/my%20work",
      env.sock.url
    );
    check("Attach: socket receives ArrayBuffer frames", env.sock.binaryType === "arraybuffer");
    check(
      "Attach: status starts connecting",
      env.view.last[0] === "connecting" && env.view.last[1] === "connecting..."
    );
  }
  {
    const env = attachSession({ location: { protocol: "http:", host: "localhost:5000" } });
    env.session.start();
    check("Attach: socket URL is ws on http", env.sock.url.startsWith("ws://localhost:5000/mux/ws/"));
  }
  {
    const env = attachSession();
    env.session.start();
    env.sock.emit("open");
    check("Attach: open reports connected", env.view.last.join() === "open,connected");
    check(
      "Attach: open sends a fitted resize and focuses the terminal",
      env.sock.sent.length === 1 &&
        env.sock.sent[0].type === "resize" &&
        env.sock.sent[0].cols === 80 &&
        env.sock.sent[0].rows === 24 &&
        env.fits === 1 &&
        env.term.focused === 1
    );
    env.sock.emit("close", { code: 1006 });
    check("Attach: close reports disconnected with code", env.view.last.join() === "closed,disconnected (1006)");
    check(
      "Attach: close writes the bridge closed line",
      env.term.written.some((w) => w.includes("[bridge closed]"))
    );
  }
  {
    const env = attachSession();
    env.session.start();
    env.sock.emit("close", { code: 0 });
    check("Attach: close without code reports disconnected", env.view.last.join() === "closed,disconnected");
    env.sock.emit("error");
    check("Attach: error reports connection error", env.view.last.join() === "error,connection error");
  }
}

async function frameCases() {
  const env = attachSession();
  env.session.start();
  env.sock.emit("open");
  env.sock.emit("message", { data: new Uint8Array([104, 105]).buffer });
  env.sock.emit("message", { data: "plain" });
  env.sock.emit("message", { data: JSON.stringify({ type: "error", message: "boom" }) });
  env.sock.emit("message", { data: { arrayBuffer: () => Promise.resolve(new Uint8Array([7]).buffer) } });
  await new Promise((r) => setImmediate(r));
  const w = env.term.written;
  check("Attach: bytes frames are written", w[0] instanceof Uint8Array && w[0][0] === 104);
  check("Attach: text frames are written", w[1] === "plain");
  check(
    "Attach: bridge error frames are written in red",
    typeof w[2] === "string" && w[2].includes("\x1b[31m[bridge error] boom")
  );
  check("Attach: Blob frames are written", w[3] instanceof Uint8Array && w[3][0] === 7);
}

function inputCases() {
  {
    const env = attachSession();
    env.session.start();
    env.term.dataFn("x");
    check("Attach: input before open is dropped", env.sock.sent.length === 0);
    env.sock.emit("open");
    env.term.dataFn("ls\r");
    const last = env.sock.sent[env.sock.sent.length - 1];
    check("Attach: input while connected is sent", last.type === "input" && last.data === "ls\r");
    env.sock.emit("close", { code: 1000 });
    const n = env.sock.sent.length;
    env.term.dataFn("y");
    check("Attach: input after close is dropped", env.sock.sent.length === n);
  }
  {
    const clock = fakeClock();
    const env = attachSession({ clock });
    env.session.start();
    env.sock.emit("open");
    const n = env.sock.sent.length;
    env.session.resize();
    clock.advance(50);
    env.session.resize();
    clock.advance(79);
    check("Attach: resize is debounced", env.sock.sent.length === n);
    clock.advance(1);
    check(
      "Attach: one resize is sent 80 ms after the last",
      env.sock.sent.length === n + 1 && env.sock.sent[n].type === "resize"
    );
    env.sock.emit("close", { code: 1000 });
    env.session.resize();
    clock.advance(80);
    check("Attach: resize while disconnected is not sent", env.sock.sent.length === n + 1);
  }
  {
    const clock = fakeClock();
    const env = attachSession({ clock });
    env.session.start();
    clock.advance(30000);
    check("Attach: no ping before connected", env.sock.sent.length === 0);
    env.sock.emit("open");
    const n = env.sock.sent.length;
    clock.advance(29999);
    check("Attach: no ping before 30 s", env.sock.sent.length === n);
    clock.advance(1);
    check("Attach: ping every 30 s while connected", env.sock.sent[n] && env.sock.sent[n].type === "ping");
    env.sock.emit("close", { code: 1000 });
    clock.advance(60000);
    check("Attach: no ping after close", env.sock.sent.length === n + 1);
  }
}

async function killCases() {
  {
    const sessions = fakeSessions();
    const env = attachSession({ sessions, confirm: false });
    env.session.start();
    env.sock.emit("open");
    await env.session.kill();
    check("Kill: asks to confirm with the Session name", env.confirms[0] === "Kill session my work?");
    check(
      "Kill: cancel does nothing",
      sessions.calls.length === 0 && env.view.busy.length === 0 && env.view.errors.length === 0
    );
  }
  {
    const sessions = fakeSessions({ ok: false, error: "HTTP 403" });
    const env = attachSession({ sessions });
    env.session.start();
    env.sock.emit("open");
    await env.session.kill();
    check(
      "Kill: clears the previous error and marks busy before the request",
      env.view.errors[0] === null && env.view.busy[0] === true
    );
    check(
      "Kill: requests the target Session",
      sessions.calls.length === 1 &&
        sessions.calls[0].host === "web 1" &&
        sessions.calls[0].user === "al/ice" &&
        sessions.calls[0].name === "my work"
    );
    check("Kill: failure shows the error", env.view.errors[1] === "HTTP 403");
    check("Kill: failure sets status kill failed", env.view.last.join() === "error,kill failed");
    check("Kill: failure re-enables Kill", env.view.busy[1] === false);
    check("Kill: failure does not close the window", env.order.length === 0);
  }
  {
    const env = attachSession();
    env.session.start();
    env.sock.emit("open");
    await env.session.kill();
    check("Kill: success closes the window, then renders killed", env.order.join() === "closeWindow,killed");
    check("Kill: success closes the socket", env.sock.closed === 1);
    check("Kill: success sets status session killed", env.view.last.join() === "closed,session killed");
    check("Kill: success leaves Kill disabled", !env.view.busy.includes(false));
    const n = env.view.statuses.length;
    env.sock.emit("close", { code: 1006 });
    env.sock.emit("error");
    check(
      "Killed: close and error change nothing",
      env.view.statuses.length === n && !env.term.written.some((w) => String(w).includes("[bridge closed]"))
    );
    env.session.flash("clipboard: 3 B from session");
    check("Killed: flash is suppressed", env.view.statuses.length === n);
    const sent = env.sock.sent.length;
    env.term.dataFn("x");
    check("Killed: input is not sent", env.sock.sent.length === sent);
  }
  {
    // Kill succeeds before the socket ever opens.
    const env = attachSession();
    env.session.start();
    await env.session.kill();
    const n = env.view.statuses.length;
    env.sock.emit("open");
    check(
      "Killed: a later open closes the socket instead of connecting",
      env.sock.closed === 2 && env.view.statuses.length === n && env.term.focused === 0
    );
  }
}

function flashCases() {
  {
    const clock = fakeClock();
    const env = attachSession({ clock });
    env.session.start();
    env.sock.emit("open");
    env.session.flash("clipboard: 3 B from session");
    check(
      "Flash: shows the message in the current state",
      env.view.last.join() === "open,clipboard: 3 B from session"
    );
    clock.advance(2000);
    env.session.flash("copy failed");
    clock.advance(2499);
    check("Flash: a new flash restarts the 2.5 s timer", env.view.last.join() === "open,copy failed");
    clock.advance(1);
    check("Flash: restores connected after 2.5 s", env.view.last.join() === "open,connected");
  }
  {
    const clock = fakeClock();
    const env = attachSession({ clock });
    env.session.start();
    env.sock.emit("open");
    env.session.flash("copy failed");
    env.sock.emit("close", { code: 1006 });
    clock.advance(2500);
    check("Flash: does not restore connected once disconnected", env.view.last.join() === "closed,disconnected (1006)");
  }
}

function lastCopy(env) {
  return env.view.copyStates[env.view.copyStates.length - 1];
}

async function copyFeedbackCases() {
  {
    const env = attachSession();
    check(
      "Copy button: disabled with nothing selected",
      lastCopy(env).disabled === true &&
        lastCopy(env).title === "nothing selected" &&
        lastCopy(env).label === "Copy"
    );
    env.term.selection = "hello";
    env.term.selFn();
    check(
      "Copy button: enabled with a Selection",
      lastCopy(env).disabled === false && lastCopy(env).title === "" && lastCopy(env).label === "Copy"
    );
  }
  {
    const clock = fakeClock();
    const clip = fakeClipboard();
    const term = fakeSessionTerm("hello");
    const env = attachSession({ clock, clipboard: clip, term });
    await env.session.copy();
    check("Copy: writes the Selection", clip.writes[0] === "hello");
    check("Copy: shows Copied", lastCopy(env).label === "Copied" && lastCopy(env).disabled === false);
    term.selection = "";
    term.selFn();
    check("Copy: Copied stays while the selection changes", lastCopy(env).label === "Copied" && lastCopy(env).disabled === true);
    clock.advance(1499);
    check("Copy: Copied lasts 1.5 s", lastCopy(env).label === "Copied");
    clock.advance(1);
    check(
      "Copy: label returns to Copy after 1.5 s",
      lastCopy(env).label === "Copy" && lastCopy(env).disabled === true && lastCopy(env).title === "nothing selected"
    );
  }
  {
    const term = fakeSessionTerm("hello");
    const env = attachSession({ term, clipboard: fakeClipboard("", { failWrite: true }) });
    env.session.start();
    env.sock.emit("open");
    await env.session.copy();
    check("Copy: failure flashes copy failed", env.view.last.join() === "open,copy failed");
  }
  {
    const term = fakeSessionTerm("");
    const env = attachSession({ term });
    const n = env.view.copyStates.length;
    await env.session.copy();
    check("Copy: empty Selection changes nothing", env.view.copyStates.length === n && env.view.statuses.length === 0);
  }
  {
    const clip = fakeClipboard();
    const term = fakeSessionTerm("hello");
    const env = attachSession({ term, clipboard: clip });
    let prevented = 0;
    const ev = { type: "keydown", ctrlKey: true, key: "c", preventDefault() { prevented++; } };
    const passed = term.keyFn(ev);
    await new Promise((r) => setImmediate(r));
    check(
      "Copy chord: copies the Selection and stops the key",
      passed === false && prevented === 1 && clip.writes[0] === "hello"
    );
    term.selection = "";
    check("Copy chord: without a Selection the key reaches the Session", term.keyFn(ev) === true);
  }
}

lifecycleCases();
inputCases();
flashCases();
chordCases();
wireCases();
missingDeps();
await copyCases();
await clipboardCases();
await blobCase();
await frameCases();
await killCases();
await copyFeedbackCases();

process.exit(failed ? 1 : 0);
