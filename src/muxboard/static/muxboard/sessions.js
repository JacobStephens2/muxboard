/* Session requests: the client half of the Kill and create routes, shared
 * by the Dashboard and the Attach page.
 *
 * fetch and the Board base path are passed in, so Node tests drive the
 * module with a fake fetch. Requests resolve to an outcome and never reject:
 * {ok: true} or {ok: false, error}.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.mbSessions = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // The create route's name rule: the message, or null for a valid name.
  function nameError(name) {
    if (/^[A-Za-z0-9_-]{1,64}$/.test(name || "")) return null;
    return "Name must be 1-64 chars of [A-Za-z0-9_-].";
  }

  function requireDep(env, name, type) {
    if (!env || typeof env[name] !== type || env[name] === null) {
      throw new TypeError("mbSessions: " + name + " must be a " + type);
    }
    return env[name];
  }

  function client(env) {
    var fetchFn = requireDep(env, "fetch", "function");
    var base = requireDep(env, "base", "string");

    function post(target, action, fields) {
      var body = new URLSearchParams();
      Object.keys(fields).forEach(function (k) { body.set(k, fields[k]); });
      var url = base + "/api/" + encodeURIComponent(target.host) + "/" +
                encodeURIComponent(target.user) + "/" + action;
      return fetchFn(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      }).then(function (r) {
        return r.json().then(function (j) {
          if (r.ok && j && j.ok) return { ok: true, body: j };
          return { ok: false, error: (j && j.error) || "failed" };
        }, function () {
          // An abort() page (401, 403, 404) is HTML, not JSON.
          return { ok: false, error: "HTTP " + r.status };
        });
      }, function (e) {
        return { ok: false, error: String(e) };
      });
    }

    // Kill: the request echoes the Session name as confirmation.
    function kill(target) {
      return post(target, "kill", { name: target.name, confirm: target.name })
        .then(function (out) { return out.ok ? { ok: true } : out; });
    }

    // Create: resolves {ok: true, name} with the name the server created.
    function create(target, command) {
      var invalid = nameError(target.name);
      if (invalid) return Promise.resolve({ ok: false, error: invalid });
      var fields = { name: target.name };
      if (command) fields.command = command;
      return post(target, "create", fields).then(function (out) {
        return out.ok ? { ok: true, name: out.body.name || target.name } : out;
      });
    }

    return { kill: kill, create: create };
  }

  return { client: client, nameError: nameError };
});
