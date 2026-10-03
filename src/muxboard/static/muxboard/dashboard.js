/* Dashboard client: the Dashboard's Kill gate, create, refresh, the
 * just-created highlight and relative times.
 *
 * The page builds the DOM and hands this module view callbacks. Everything
 * the controller needs from the browser (the session requests client,
 * storage, reload, notify, view callbacks) is passed in, so Node tests
 * drive it with fakes. The name rule comes from sessions.js, which the page
 * loads first.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./sessions.js"));
  } else {
    root.mbDashboard = factory(root.mbSessions);
  }
})(typeof self !== "undefined" ? self : this, function (sessionsModule) {
  "use strict";

  if (!sessionsModule) throw new Error("mbDashboard: sessions.js must load first");

  var NEW_SESSION_KEY = "muxboard-new-session";

  function requireDep(env, name, type) {
    if (!env || typeof env[name] !== type || env[name] === null) {
      throw new TypeError("mbDashboard: " + name + " must be a " + type);
    }
    return env[name];
  }

  // "42s ago", "5m ago", "3h07m ago", "2d ago". A future time reads 0s ago.
  function formatRelative(unix, now) {
    var d = now - unix;
    if (d < 0) d = 0;
    if (d < 60) return d + "s ago";
    if (d < 3600) return Math.floor(d / 60) + "m ago";
    if (d < 86400) {
      var h = Math.floor(d / 3600);
      var m = Math.floor((d % 3600) / 60);
      return h + "h" + (m < 10 ? "0" : "") + m + "m ago";
    }
    return Math.floor(d / 86400) + "d ago";
  }

  function isName(v) {
    return typeof v === "string" && v !== "";
  }

  // Dashboard controller: Kill, create, refresh and the just-created
  // target. Each method that sends a request resolves once its outcome is
  // rendered.
  function controller(env) {
    var sessions = requireDep(env, "sessions", "object");
    requireDep(sessions, "kill", "function");
    requireDep(sessions, "create", "function");
    requireDep(sessions, "refresh", "function");
    var storage = requireDep(env, "storage", "object");
    requireDep(storage, "getItem", "function");
    requireDep(storage, "setItem", "function");
    requireDep(storage, "removeItem", "function");
    var reload = requireDep(env, "reload", "function");
    var notify = requireDep(env, "notify", "function");
    var view = requireDep(env, "view", "object");
    requireDep(view, "kill", "function");
    requireDep(view, "create", "function");
    requireDep(view, "refresh", "function");

    // Each dialog's state is null while closed. A request outcome renders
    // only into the dialog that sent it, not one cancelled or reopened.
    var killState = null;
    var createState = null;
    var refreshing = false;

    function copy(state) {
      var out = {};
      Object.keys(state).forEach(function (k) { out[k] = state[k]; });
      return out;
    }

    function renderKill() {
      view.kill(killState ? copy(killState) : {
        open: false, target: null, confirm: "", error: null, busy: false, goLabel: "Kill session",
      });
    }

    function renderCreate() {
      view.create(createState ? copy(createState) : {
        open: false, target: null, name: "", command: "", error: null, busy: false, goLabel: "Create",
      });
    }

    // ---------- Kill ----------

    function openKill(target) {
      killState = {
        open: true,
        target: { host: target.host, user: target.user, name: target.name },
        confirm: "",
        error: null,
        busy: false,
        goLabel: "Kill session",
      };
      renderKill();
    }

    function cancelKill() {
      killState = null;
      renderKill();
    }

    // Kill: the operator types the Session name before anything is sent.
    function confirmKill(typed) {
      var state = killState;
      if (!state || state.busy) return Promise.resolve();
      state.confirm = typed || "";
      if (state.confirm.trim() !== state.target.name) {
        state.error = "Type the session name (" + state.target.name + ") to confirm.";
        renderKill();
        return Promise.resolve();
      }
      state.error = null;
      state.busy = true;
      state.goLabel = "Killing...";
      renderKill();
      return sessions.kill(state.target).then(function (out) {
        if (!out.ok) {
          if (killState !== state) return;
          state.error = out.error;
          state.busy = false;
          state.goLabel = "Retry kill";
          renderKill();
          return;
        }
        if (killState === state) cancelKill();
        reload();
      });
    }

    // ---------- create ----------

    function openCreate(target) {
      createState = {
        open: true,
        target: { host: target.host, user: target.user },
        name: "",
        command: "",
        error: null,
        busy: false,
        goLabel: "Create",
      };
      renderCreate();
    }

    function cancelCreate() {
      createState = null;
      renderCreate();
    }

    function rememberCreated(target) {
      try {
        storage.setItem(NEW_SESSION_KEY, JSON.stringify(target));
      } catch (e) {
        // Storage can be unavailable in some browser modes; creation
        // still succeeded, so the highlight is best-effort.
      }
    }

    function submitCreate(name, command) {
      var state = createState;
      if (!state || state.busy) return Promise.resolve();
      state.name = (name || "").trim();
      state.command = (command || "").trim();
      var invalid = sessionsModule.nameError(state.name);
      if (invalid) {
        state.error = invalid;
        renderCreate();
        return Promise.resolve();
      }
      var target = { host: state.target.host, user: state.target.user, name: state.name };
      state.error = null;
      state.busy = true;
      state.goLabel = "Creating...";
      renderCreate();
      return sessions.create(target, state.command).then(function (out) {
        if (!out.ok) {
          if (createState !== state) return;
          state.error = out.error;
          state.busy = false;
          state.goLabel = "Retry create";
          renderCreate();
          return;
        }
        rememberCreated({ host: target.host, user: target.user, name: out.name });
        if (createState === state) cancelCreate();
        reload();
      });
    }

    // The target create stored before the last reload, handed back once;
    // null when there is none or it cannot be read.
    function justCreated() {
      var raw;
      try {
        raw = storage.getItem(NEW_SESSION_KEY);
        if (raw) storage.removeItem(NEW_SESSION_KEY);
      } catch (e) {
        return null;
      }
      if (!raw) return null;
      var want;
      try {
        want = JSON.parse(raw);
      } catch (e) {
        return null;
      }
      if (!want || !isName(want.host) || !isName(want.user) || !isName(want.name)) return null;
      return { host: want.host, user: want.user, name: want.name };
    }

    // ---------- refresh ----------

    function refresh() {
      if (refreshing) return Promise.resolve();
      refreshing = true;
      view.refresh({ busy: true, label: "Refreshing..." });
      return sessions.refresh().then(function (out) {
        if (out.ok) {
          reload();
          return;
        }
        refreshing = false;
        view.refresh({ busy: false, label: "Refresh" });
        notify("Refresh failed.");
      });
    }

    return {
      openKill: openKill,
      confirmKill: confirmKill,
      cancelKill: cancelKill,
      openCreate: openCreate,
      submitCreate: submitCreate,
      cancelCreate: cancelCreate,
      refresh: refresh,
      justCreated: justCreated,
    };
  }

  return { controller: controller, formatRelative: formatRelative };
});
