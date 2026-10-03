#!/usr/bin/env node
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const root = path.join(path.dirname(fileURLToPath(import.meta.url)), "../..");
const sessions = require(path.join(root, "src/muxboard/static/muxboard/sessions.js"));

let failed = 0;

function check(name, pred, detail) {
  const ok = !!pred;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  " + detail : ""}`);
  if (!ok) failed++;
}

// A fetch that records calls and answers with one canned response.
function fakeFetch(answer) {
  const calls = [];
  const fetch = (url, init) => {
    calls.push({ url, init, body: new URLSearchParams(init && init.body) });
    if (answer.reject) return Promise.reject(answer.reject);
    return Promise.resolve({
      ok: answer.status >= 200 && answer.status < 300,
      status: answer.status,
      json() {
        return "json" in answer
          ? Promise.resolve(answer.json)
          : Promise.reject(new SyntaxError("Unexpected token '<'"));
      },
    });
  };
  fetch.calls = calls;
  return fetch;
}

const TARGET = { host: "web 1", user: "al/ice", name: "work" };

function client(fetch) {
  return sessions.client({ fetch, base: "/mux" });
}

// Kill
async function killCases() {
  {
    const fetch = fakeFetch({ status: 200, json: { ok: true } });
    const out = await client(fetch).kill(TARGET);
    const c = fetch.calls[0];
    check(
      "Kill: POSTs to the kill route with host key and tmux user encoded",
      fetch.calls.length === 1 &&
        c.url === "/mux/api/web%201/al%2Fice/kill" &&
        c.init.method === "POST"
    );
    check(
      "Kill: same-origin form body",
      c.init.credentials === "same-origin" &&
        c.init.headers["Content-Type"] === "application/x-www-form-urlencoded"
    );
    check(
      "Kill: echoes the Session name as confirm",
      c.body.get("name") === "work" && c.body.get("confirm") === "work"
    );
    check("Kill: success resolves ok", out.ok === true && !("error" in out));
  }
  {
    const fetch = fakeFetch({ status: 400, json: { ok: false, error: "no session" } });
    const out = await client(fetch).kill(TARGET);
    check("Kill: refusal reports the server's JSON error", out.ok === false && out.error === "no session");
  }
  {
    const fetch = fakeFetch({ status: 403 });
    const out = await client(fetch).kill(TARGET);
    check("Kill: non-JSON refusal reports HTTP status", out.ok === false && out.error === "HTTP 403");
  }
  {
    const fetch = fakeFetch({ reject: new TypeError("Failed to fetch") });
    const out = await client(fetch).kill(TARGET);
    check(
      "Kill: network failure resolves with the error's text",
      out.ok === false && out.error === "TypeError: Failed to fetch"
    );
  }
  {
    const fetch = fakeFetch({ status: 200, json: { ok: false } });
    const out = await client(fetch).kill(TARGET);
    check("Kill: JSON refusal without an error still fails", out.ok === false && out.error === "failed");
  }
  {
    const fetch = fakeFetch({ status: 500, json: null });
    const out = await client(fetch).kill(TARGET);
    check("Kill: a null JSON body still resolves to a failure", out.ok === false && out.error === "failed");
  }
}

// Create
async function createCases() {
  {
    const fetch = fakeFetch({ status: 200, json: { ok: true, name: "work" } });
    const out = await client(fetch).create(TARGET, "htop");
    const c = fetch.calls[0];
    check(
      "Create: POSTs name and command to the create route",
      c.url === "/mux/api/web%201/al%2Fice/create" &&
        c.init.method === "POST" &&
        c.init.credentials === "same-origin" &&
        c.init.headers["Content-Type"] === "application/x-www-form-urlencoded" &&
        c.body.get("name") === "work" &&
        c.body.get("command") === "htop"
    );
    check("Create: success resolves ok with the created name", out.ok === true && out.name === "work");
  }
  {
    const fetch = fakeFetch({ status: 200, json: { ok: true, name: "work" } });
    await client(fetch).create(TARGET, "");
    await client(fetch).create(TARGET);
    check(
      "Create: an empty command is not sent",
      fetch.calls.length === 2 && fetch.calls.every((c) => !c.body.has("command"))
    );
  }
  for (const name of ["", "a b", "x".repeat(65), "naïve", "a.b"]) {
    const fetch = fakeFetch({ status: 200, json: { ok: true } });
    const out = await client(fetch).create({ ...TARGET, name });
    check(
      `Create: rejects name ${JSON.stringify(name.slice(0, 12))} without fetching`,
      fetch.calls.length === 0 &&
        out.ok === false &&
        out.error === "Name must be 1-64 chars of [A-Za-z0-9_-]."
    );
  }
  check(
    "Create: the name rule is checkable before a request",
    sessions.nameError("a b") === "Name must be 1-64 chars of [A-Za-z0-9_-]." &&
      sessions.nameError("ok_name-1") === null
  );
  {
    const fetch = fakeFetch({ status: 200, json: { ok: true, name: "x" } });
    const out = await client(fetch).create({ ...TARGET, name: "A_z-9".padEnd(64, "q") });
    check("Create: accepts a 64-char name", fetch.calls.length === 1 && out.ok === true);
  }
  {
    const out = await client(fakeFetch({ status: 401 })).create(TARGET, "");
    check("Create: non-JSON refusal reports HTTP status", out.ok === false && out.error === "HTTP 401");
  }
  {
    const out = await client(
      fakeFetch({ status: 400, json: { ok: false, error: "duplicate session" } })
    ).create(TARGET, "");
    check("Create: refusal reports the server's JSON error", out.error === "duplicate session");
  }
  {
    const out = await client(fakeFetch({ reject: new Error("offline") })).create(TARGET, "");
    check("Create: network failure resolves with the error's text", out.error === "Error: offline");
  }
}

function missingDeps() {
  let threw = 0;
  for (const fn of [
    () => sessions.client({ base: "/mux" }),
    () => sessions.client({ fetch: () => {} }),
    () => sessions.client(null),
  ]) {
    try { fn(); } catch (e) { threw++; }
  }
  check("Missing dependencies fail loudly", threw === 3);
}

missingDeps();
await killCases();
await createCases();

process.exit(failed ? 1 : 0);
