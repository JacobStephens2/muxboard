#!/usr/bin/env node
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");
const dashboard = require(path.join(root, "src/muxboard/static/muxboard/dashboard.js"));

let failed = 0;

function check(name, pred, detail) {
  const ok = !!pred;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failed++;
}

const KEY = "muxboard-new-session";
const TARGET = { host: "web 1", user: "al/ice", name: "work" };

// A session requests client that records calls and answers with canned
// outcomes, or holds the answer until release() when hold is set.
function fakeSessions(outcomes) {
  const o = outcomes || {};
  const s = {
    calls: [],
    pending: [],
    hold: false,
    answer(value) {
      if (!s.hold) return Promise.resolve(value);
      return new Promise((resolve) => s.pending.push(() => resolve(value)));
    },
    release() { s.pending.splice(0).forEach((fn) => fn()); },
    kill(target) {
      s.calls.push(["kill", target]);
      return s.answer(o.kill || { ok: true });
    },
    create(target, command) {
      s.calls.push(["create", target, command]);
      return s.answer(o.create || { ok: true, name: target.name });
    },
    refresh() {
      s.calls.push(["refresh"]);
      return s.answer(o.refresh || { ok: true });
    },
  };
  return s;
}

function fakeStorage(initial, opts) {
  const o = opts || {};
  const items = new Map(Object.entries(initial || {}));
  return {
    items,
    getItem(k) {
      if (o.throwGet) throw new Error("SecurityError");
      return items.has(k) ? items.get(k) : null;
    },
    setItem(k, v) {
      if (o.throwSet) throw new Error("QuotaExceededError");
      items.set(k, String(v));
    },
    removeItem(k) {
      if (o.throwRemove) throw new Error("SecurityError");
      items.delete(k);
    },
  };
}

function fakeView() {
  const v = {
    kills: [],
    creates: [],
    refreshes: [],
    kill(state) { v.kills.push(state); },
    create(state) { v.creates.push(state); },
    refresh(state) { v.refreshes.push(state); },
    get lastKill() { return v.kills[v.kills.length - 1]; },
    get lastCreate() { return v.creates[v.creates.length - 1]; },
    get lastRefresh() { return v.refreshes[v.refreshes.length - 1]; },
  };
  return v;
}

function board(over) {
  const o = over || {};
  const env = {
    sessions: "sessions" in o ? o.sessions : fakeSessions(o.outcomes),
    storage: "storage" in o ? o.storage : fakeStorage(),
    reload: () => { env.reloads++; env.order.push("reload"); },
    notify: (msg) => { env.notes.push(msg); },
    view: "view" in o ? o.view : fakeView(),
    reloads: 0,
    notes: [],
    order: [],
  };
  if (env.view && env.view.kill) {
    const origKill = env.view.kill;
    env.view.kill = (state) => { env.order.push(state.open ? "kill:open" : "kill:closed"); origKill(state); };
  }
  env.board = dashboard.controller(env);
  return env;
}

// Kill

async function killCases() {
  {
    const env = board();
    env.board.openKill(TARGET);
    const s = env.view.lastKill;
    check(
      "Kill: opening renders the dialog for the target",
      s.open === true &&
        s.target.host === "web 1" && s.target.user === "al/ice" && s.target.name === "work"
    );
    check("Kill: the confirm field opens empty", s.confirm === "");
    check(
      "Kill: opening shows no error and an idle button",
      s.error === null && s.busy === false && s.goLabel === "Kill session"
    );
  }
  for (const typed of ["", "wor", "Work", "work2"]) {
    const env = board();
    env.board.openKill(TARGET);
    await env.board.confirmKill(typed);
    check(
      `Kill: confirming with ${JSON.stringify(typed)} asks for the name and sends nothing`,
      env.sessions.calls.length === 0 &&
        env.view.lastKill.open === true &&
        env.view.lastKill.error === "Type the session name (work) to confirm." &&
        env.view.lastKill.busy === false &&
        env.reloads === 0
    );
  }
  {
    const env = board();
    env.board.openKill(TARGET);
    env.sessions.hold = true;
    const done = env.board.confirmKill("  work ");
    const busy = env.view.lastKill;
    check(
      "Kill: the typed name, trimmed, sends the kill",
      env.sessions.calls.length === 1 &&
        env.sessions.calls[0][0] === "kill" &&
        env.sessions.calls[0][1].host === "web 1" &&
        env.sessions.calls[0][1].user === "al/ice" &&
        env.sessions.calls[0][1].name === "work"
    );
    check(
      "Kill: the button is busy while the request runs",
      busy.busy === true && busy.goLabel === "Killing..." && busy.error === null
    );
    env.board.confirmKill("work");
    check("Kill: a second confirm while busy sends nothing", env.sessions.calls.length === 1);
    env.sessions.release();
    await done;
    check("Kill: success closes the dialog, then reloads", env.order.slice(-2).join() === "kill:closed,reload");
  }
  {
    const env = board({ outcomes: { kill: { ok: false, error: "no session" } } });
    env.board.openKill(TARGET);
    await env.board.confirmKill("work");
    const s = env.view.lastKill;
    check(
      "Kill: failure renders the server's error with Retry kill",
      s.open === true && s.error === "no session" && s.busy === false && s.goLabel === "Retry kill"
    );
    check("Kill: failure does not reload", env.reloads === 0);
    check("Kill: failure keeps what the operator typed", s.confirm === "work");
    await env.board.confirmKill("nope");
    check(
      "Kill: a mismatch after a failure keeps Retry kill",
      env.view.lastKill.goLabel === "Retry kill" &&
        env.view.lastKill.error === "Type the session name (work) to confirm." &&
        env.sessions.calls.length === 1
    );
  }
  {
    const env = board();
    env.board.openKill(TARGET);
    env.board.cancelKill();
    check("Kill: cancel closes the dialog", env.view.lastKill.open === false);
    await env.board.confirmKill("work");
    check("Kill: cancel forgets the target", env.sessions.calls.length === 0 && env.reloads === 0);
  }
  {
    const env = board({ outcomes: { kill: { ok: false, error: "late" } } });
    env.board.openKill(TARGET);
    env.sessions.hold = true;
    const done = env.board.confirmKill("work");
    env.board.cancelKill();
    const closed = env.view.kills.length;
    env.sessions.release();
    await done;
    check("Kill: a failure after cancel renders nothing", env.view.kills.length === closed);
  }
  {
    const env = board();
    env.board.openKill(TARGET);
    env.sessions.hold = true;
    const done = env.board.confirmKill("work");
    env.board.cancelKill();
    env.sessions.release();
    await done;
    check("Kill: a success after cancel still reloads", env.reloads === 1);
  }
  {
    const env = board();
    env.board.openKill(TARGET);
    await env.board.confirmKill("work");
    env.board.openKill({ host: "h", user: "u", name: "other" });
    const s = env.view.lastKill;
    check(
      "Kill: reopening starts clean for the new target",
      s.target.name === "other" && s.confirm === "" && s.error === null && s.goLabel === "Kill session"
    );
  }
}

// Create

async function createCases() {
  {
    const env = board();
    env.board.openCreate({ host: "web 1", user: "al/ice" });
    const s = env.view.lastCreate;
    check(
      "Create: opening renders an empty dialog for the host and user",
      s.open === true &&
        s.target.host === "web 1" && s.target.user === "al/ice" &&
        s.name === "" && s.command === "" &&
        s.error === null && s.busy === false && s.goLabel === "Create"
    );
  }
  for (const name of ["", "a b", "x".repeat(65), "a.b"]) {
    const env = board();
    env.board.openCreate({ host: "web 1", user: "al/ice" });
    await env.board.submitCreate(name, "htop");
    check(
      `Create: invalid name ${JSON.stringify(name.slice(0, 12))} shows the name rule and sends nothing`,
      env.sessions.calls.length === 0 &&
        env.view.lastCreate.error === "Name must be 1-64 chars of [A-Za-z0-9_-]." &&
        env.view.lastCreate.busy === false
    );
  }
  {
    const env = board({ outcomes: { create: { ok: true, name: "work-1" } } });
    env.board.openCreate({ host: "web 1", user: "al/ice" });
    env.sessions.hold = true;
    const done = env.board.submitCreate(" work ", " htop ");
    const busy = env.view.lastCreate;
    const c = env.sessions.calls[0];
    check(
      "Create: a valid name sends create with the trimmed name and command",
      env.sessions.calls.length === 1 &&
        c[0] === "create" &&
        c[1].host === "web 1" && c[1].user === "al/ice" && c[1].name === "work" &&
        c[2] === "htop"
    );
    check(
      "Create: the button is busy while the request runs",
      busy.busy === true && busy.goLabel === "Creating..." && busy.error === null
    );
    env.board.submitCreate("work", "");
    check("Create: a second submit while busy sends nothing", env.sessions.calls.length === 1);
    env.sessions.release();
    await done;
    const stored = JSON.parse(env.storage.items.get(KEY));
    check(
      "Create: success stores the target under the server's name",
      stored.host === "web 1" && stored.user === "al/ice" && stored.name === "work-1"
    );
    check(
      "Create: success closes the dialog and reloads",
      env.view.lastCreate.open === false && env.reloads === 1
    );
  }
  {
    const env = board({ storage: fakeStorage({}, { throwSet: true }) });
    env.board.openCreate({ host: "web 1", user: "al/ice" });
    await env.board.submitCreate("work", "");
    check("Create: a throwing storage does not stop the reload", env.reloads === 1);
  }
  {
    const env = board({ outcomes: { create: { ok: false, error: "duplicate session" } } });
    env.board.openCreate({ host: "web 1", user: "al/ice" });
    await env.board.submitCreate("work", "htop");
    const s = env.view.lastCreate;
    check(
      "Create: failure renders the error with Retry create",
      s.open === true && s.error === "duplicate session" && s.busy === false && s.goLabel === "Retry create"
    );
    check(
      "Create: failure keeps the typed name and command",
      s.name === "work" && s.command === "htop"
    );
    check("Create: failure stores nothing and does not reload", !env.storage.items.has(KEY) && env.reloads === 0);
  }
  {
    const env = board();
    env.board.openCreate({ host: "web 1", user: "al/ice" });
    env.board.cancelCreate();
    check("Create: cancel closes the dialog", env.view.lastCreate.open === false);
    await env.board.submitCreate("work", "");
    check("Create: cancel forgets the target", env.sessions.calls.length === 0);
  }
}

// Just-created highlight

function justCreatedCases() {
  {
    const storage = fakeStorage({ [KEY]: JSON.stringify(TARGET) });
    const env = board({ storage });
    const got = env.board.justCreated();
    check(
      "Just created: hands back the stored target",
      got && got.host === "web 1" && got.user === "al/ice" && got.name === "work"
    );
    check("Just created: reading clears it", !storage.items.has(KEY) && env.board.justCreated() === null);
  }
  check("Just created: nothing stored gives nothing", board().board.justCreated() === null);
  for (const [label, raw] of [
    ["malformed JSON", "{not json"],
    ["a non-object", "42"],
    ["null", "null"],
    ["a missing name", JSON.stringify({ host: "h", user: "u" })],
    ["an empty host", JSON.stringify({ host: "", user: "u", name: "n" })],
    ["a non-string user", JSON.stringify({ host: "h", user: 7, name: "n" })],
  ]) {
    const storage = fakeStorage({ [KEY]: raw });
    const got = board({ storage }).board.justCreated();
    check(`Just created: ${label} gives nothing and is cleared`, got === null && !storage.items.has(KEY));
  }
  for (const opt of ["throwGet", "throwRemove"]) {
    const storage = fakeStorage({ [KEY]: JSON.stringify(TARGET) }, { [opt]: true });
    check(`Just created: a storage that throws (${opt}) gives nothing`, board({ storage }).board.justCreated() === null);
  }
}

// Refresh

async function refreshCases() {
  {
    const env = board();
    env.sessions.hold = true;
    const done = env.board.refresh();
    check(
      "Refresh: the button is busy while the request runs",
      env.view.lastRefresh.busy === true && env.view.lastRefresh.label === "Refreshing..."
    );
    check("Refresh: sends the refresh request", env.sessions.calls.length === 1 && env.sessions.calls[0][0] === "refresh");
    env.board.refresh();
    check("Refresh: a second refresh while busy sends nothing", env.sessions.calls.length === 1);
    env.sessions.release();
    await done;
    check("Refresh: success reloads", env.reloads === 1 && env.notes.length === 0);
  }
  {
    const env = board({ outcomes: { refresh: { ok: false, error: "HTTP 401" } } });
    await env.board.refresh();
    check(
      "Refresh: failure clears busy and notifies",
      env.view.lastRefresh.busy === false &&
        env.view.lastRefresh.label === "Refresh" &&
        env.notes.length === 1 && env.notes[0] === "Refresh failed."
    );
    check("Refresh: failure does not reload", env.reloads === 0);
  }
}

// Relative time

function relativeCases() {
  const now = 1_000_000;
  const cases = [
    [0, "0s ago"],
    [59, "59s ago"],
    [60, "1m ago"],
    [3599, "59m ago"],
    [3600, "1h00m ago"],
    [3660 + 540, "1h10m ago"],
    [86399, "23h59m ago"],
    [86400, "1d ago"],
    [3 * 86400 + 5, "3d ago"],
  ];
  for (const [ago, want] of cases) {
    const got = dashboard.formatRelative(now - ago, now);
    check(`Relative: ${ago}s is ${want}`, got === want, got);
  }
  check("Relative: a future timestamp reads 0s ago", dashboard.formatRelative(now + 30, now) === "0s ago");
}

function missingDeps() {
  const cases = [
    ["sessions", () => board({ sessions: null })],
    ["sessions", () => board({ sessions: undefined })],
    ["sessions.kill", () => board({ sessions: { create() {}, refresh() {} } })],
    ["sessions.create", () => board({ sessions: { kill() {}, refresh() {} } })],
    ["sessions.refresh", () => board({ sessions: { kill() {}, create() {} } })],
    ["storage", () => board({ storage: null })],
    ["storage", () => board({ storage: undefined })],
    ["storage.getItem", () => board({ storage: { setItem() {}, removeItem() {} } })],
    ["storage.setItem", () => board({ storage: { getItem() {}, removeItem() {} } })],
    ["storage.removeItem", () => board({ storage: { getItem() {}, setItem() {} } })],
    ["view", () => board({ view: null })],
    ["view.kill", () => board({ view: { create() {}, refresh() {} } })],
    ["view.create", () => board({ view: { kill() {}, refresh() {} } })],
    ["view.refresh", () => board({ view: { kill() {}, create() {} } })],
    ["reload", () => dashboard.controller({ ...board(), reload: undefined })],
    ["notify", () => dashboard.controller({ ...board(), notify: "x" })],
    ["sessions", () => dashboard.controller(null)],
  ];
  for (const [name, fn] of cases) {
    let err = null;
    try { fn(); } catch (e) { err = e; }
    check(
      `Missing dependency ${name} throws a TypeError naming it`,
      err instanceof TypeError && err.message.startsWith("mbDashboard: " + name + " "),
      err ? err.message : "no throw"
    );
  }
}

missingDeps();
relativeCases();
justCreatedCases();
await killCases();
await createCases();
await refreshCases();

process.exit(failed ? 1 : 0);
