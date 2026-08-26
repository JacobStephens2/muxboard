#!/usr/bin/env node
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");
const wrap = require(path.join(root, "src/muxboard/static/muxboard/wrap-url.js"));

const COLS = 176;
let failed = 0;

function check(name, pred, detail) {
  const ok = !!pred;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failed++;
}

function fixture() {
  const prefix = "https://example.com/cai/oauth/authorize?code=true&pad=";
  const total = COLS + COLS + 98;
  const full = prefix + "A".repeat(total - prefix.length);
  const lines = [
    { text: full.slice(0, COLS), isWrapped: false },
    { text: full.slice(COLS, COLS * 2), isWrapped: false },
    { text: full.slice(COLS * 2), isWrapped: false },
  ];
  return { full, lines };
}

const { full, lines } = fixture();

const fromFirst = wrap.linksOnLine(lines, 0, COLS, function () {});
check(
  "hard wrap: Link from line 0 is the full URL",
  fromFirst.length === 1 && fromFirst[0].text === full,
  `got ${fromFirst.length} link(s) len=${fromFirst[0] ? fromFirst[0].text.length : 0}`
);

const fromMiddle = wrap.linksOnLine(lines, 1, COLS, function () {});
check(
  "hard wrap: Link from continuation line is the full URL",
  fromMiddle.length === 1 && fromMiddle[0].text === full
);

const fromLast = wrap.linksOnLine(lines, 2, COLS, function () {});
check(
  "hard wrap: Link from last fragment is the full URL",
  fromLast.length === 1 && fromLast[0].text === full
);

const two = [lines[0], lines[1]];
const twoFull = lines[0].text + lines[1].text;
const twoLinks = wrap.linksOnLine(two, 0, COLS, function () {});
check(
  "hard wrap: two lines (minimised) still reconstruct",
  twoLinks.length === 1 && twoLinks[0].text === twoFull
);

const soft = [
  { text: full.slice(0, COLS), isWrapped: false },
  { text: full.slice(COLS, COLS * 2), isWrapped: true },
  { text: full.slice(COLS * 2), isWrapped: true },
];
const softLinks = wrap.linksOnLine(soft, 0, COLS, function () {});
check(
  "soft wrap: still reconstructs (no regression)",
  softLinks.length === 1 && softLinks[0].text === full
);

const sel = lines.map((l) => l.text).join("\n");
check(
  "Copy: hard-wrapped Selection becomes a navigable URL",
  wrap.copyText(sel) === full,
  `out len=${wrap.copyText(sel).length}`
);

check(
  "Copy: non-URL Selection keeps newlines",
  wrap.copyText("hello\nworld") === "hello\nworld"
);

check(
  "Copy: URL plus prose (spaces) is not glued",
  wrap.copyText(sel + "\nHold Shift while selecting") ===
    sel + "\nHold Shift while selecting"
);

const shortPrev = [
  { text: "https://example.com/short", isWrapped: false },
  { text: "not-a-continuation-because-prev-did-not-fill-cols", isWrapped: false },
];
const shortLinks = wrap.linksOnLine(shortPrev, 0, COLS, function () {});
check(
  "does not join when previous row is shorter than cols",
  shortLinks.length === 1 && shortLinks[0].text === "https://example.com/short"
);

process.exit(failed ? 1 : 0);
