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
function provider(opts) {
  const notes = [];
  const prompts = [];
  const clip = "clipboard" in opts ? opts.clipboard : fakeClipboard(opts.clipText || "");
  const p = attach.clipboardProvider({
    mode: opts.mode,
    max: opts.max == null ? 65536 : opts.max,
    prompt: () => {
      prompts.push(1);
      return Promise.resolve(!!opts.allow);
    },
    clipboard: clip,
    notify: (m) => notes.push(m),
  });
  return { p, notes, prompts, clip };
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
        notes[0] === "clipboard: refused (9 B > 64 KiB)",
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
      mode: "write", max: 10, prompt: () => Promise.resolve(true),
      clipboard: undefined, notify: (m) => notes.push(m),
    });
    await p.writeText("c", "x");
    check(
      "Push: missing clipboard is noticed as a write failure",
      notes.length === 1 && notes[0] === "clipboard: write failed"
    );
  }
  {
    const { p, prompts, clip } = provider({ mode: "write", allow: true });
    const r = await rejects(p.readText("c"));
    check(
      "Query: write mode rejects without prompting",
      r && prompts.length === 0 && clip.reads === 0
    );
  }
  {
    const { p, prompts, clip } = provider({ mode: "read-write", allow: true });
    const r = await rejects(p.readText("p"));
    check(
      "Query: read-write with a selection other than c rejects",
      r && prompts.length === 0 && clip.reads === 0
    );
  }
  {
    const { p, prompts, clip } = provider({ mode: "read-write", allow: false, clipText: "secret" });
    const r = await rejects(p.readText("c"));
    check(
      "Query: Deny rejects without reading",
      r && prompts.length === 1 && clip.reads === 0
    );
  }
  {
    const { p, prompts } = provider({ mode: "read-write", allow: true, clipText: "secret" });
    const got = await p.readText("c");
    check("Query: Allow returns the clipboard text", got === "secret" && prompts.length === 1);
  }
  {
    const { p } = provider({ mode: "read-write", allow: true, clipText: "" });
    const r = await rejects(p.readText("c"));
    check("Query: an empty clipboard rejects rather than resolve ''", r);
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
    () => attach.clipboardProvider({ mode: "write", max: 1, prompt: () => {} }),
  ]) {
    try { fn(); } catch (e) { threw++; }
  }
  check("Missing dependencies fail loudly", threw === 3);
}

chordCases();
wireCases();
missingDeps();
await copyCases();
await clipboardCases();
await blobCase();

process.exit(failed ? 1 : 0);
