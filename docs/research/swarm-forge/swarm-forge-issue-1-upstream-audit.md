# SwarmForge teardown issue: upstream audit

Audited 2026-08-13 against primary sources only. Upstream pins:

- `unclebob/swarm-forge` `main`: [`9acd54d2239fef7e41ddacd8fd30dfb0e69672fe`](https://github.com/unclebob/swarm-forge/commit/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe) (2026-07-10)
- `unclebob/swarm-forge` `six-pack`: [`59803dadb38e0e09d5357d749452036e4a82ae60`](https://github.com/unclebob/swarm-forge/tree/59803dadb38e0e09d5357d749452036e4a82ae60)
- tmux 3.4 source: [`9ae69c3795ab5ef6b4d760f6398cd9281151f632`](https://github.com/tmux/tmux/tree/9ae69c3795ab5ef6b4d760f6398cd9281151f632)

Test host: Ubuntu, tmux 3.4, zsh 5.9, Bash 5.2.21, Babashka 1.13.219.

## Verdict

The core failure mode is confirmed: when the first configured agent command returns control to its still-running parent shell, the launcher starts cleanup unconditionally; cleanup receives every configured session and kills each one without checking worker state. The six-pack's first row is the specifier, so it is the cleanup owner there ([six-pack config, lines 5–10](https://github.com/unclebob/swarm-forge/blob/59803dadb38e0e09d5357d749452036e4a82ae60/swarmforge/swarmforge.conf#L5-L10); [launcher, lines 323–349](https://github.com/unclebob/swarm-forge/blob/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe/swarmforge/scripts/swarmforge.bb#L323-L349); [cleanup, lines 24–39](https://github.com/unclebob/swarm-forge/blob/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe/swarmforge/scripts/swarm-cleanup.sh#L24-L39)). There is no worker-idle or in-progress guard in this path.

However, the draft should **not be filed as written**. It misquotes current source, treats a documented shutdown convention as though it were wholly accidental, overstates which failures can reach the suffix, and proposes a tmux signal that does not measure agent output. A narrower issue about an **unexpected cleanup-owner CLI death destroying active workers** is well supported.

## What upstream confirms

1. **The coupling is deliberate and longstanding.** `git blame` attributes the unconditional launcher suffix to the Babashka port commit [`9afcb6f`](https://github.com/unclebob/swarm-forge/commit/9afcb6fdadc11456e8122a69815d257e37b7c3fa); the preceding zsh launcher had the same design. Upstream calls row 0 the “cleanup window” and explicitly documents closing it as the intentional whole-swarm shutdown path ([README, lines 81–87](https://github.com/unclebob/swarm-forge/blob/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe/README.md#L81-L87), [lines 329–335](https://github.com/unclebob/swarm-forge/blob/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe/README.md#L329-L335)). This makes typed `/exit` debatable, not the best defect case.

2. **Unexpected foreground-process death still reaches cleanup when the shell survives.** The generated list is `agent; exit_code=$?; ... cleanup ...; exit $exit_code`, so clean exit, nonzero exit, or a signal killing only the foreground agent all let the parent shell continue. zsh documents that `;` continues with the next sublist and `&!` backgrounds and immediately disowns the cleanup job ([zsh shell grammar](https://zsh.sourceforge.io/Doc/Release/Shell-Grammar.html#Simple-Commands-_0026-Pipelines), [zsh jobs](https://zsh.sourceforge.io/Doc/Release/Jobs-_0026-Signals.html#Jobs)); GNU `nohup` makes that job ignore hangups ([GNU Coreutils](https://www.gnu.org/software/coreutils/manual/html_node/nohup-invocation.html)). This does **not** cover killing the shell, pane, tmux session, or server: those can prevent the suffix from executing. “OOM-killed” should therefore be conditional: *if the OOM killer selects only the agent CLI and leaves its shell alive*.

3. **The blast radius is scoped as claimed.** The launcher derives a project-specific socket from the project path and passes an explicit session list ([launcher, lines 455–485](https://github.com/unclebob/swarm-forge/blob/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe/swarmforge/scripts/swarmforge.bb#L455-L485)). tmux specifies that `kill-session` destroys the target session and no other sessions ([tmux manual](https://man.openbsd.org/tmux.1#kill-session)). The cleanup loop uses `kill-session`, not `kill-server`. When its last listed session is removed, “no server running” is the expected result for that socket.

4. **An explicit teardown entry point now exists.** Current `main` has `close-swarm`, added specifically to stop daemon and sessions “without relying on the cleanup window” ([commit](https://github.com/unclebob/swarm-forge/commit/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe), [script](https://github.com/unclebob/swarm-forge/blob/9acd54d2239fef7e41ddacd8fd30dfb0e69672fe/close-swarm#L1-L75)). That strengthens the design case for separating explicit teardown from agent-process lifetime. Caveat: the six-pack wrapper copies only `swarmforge/scripts/` and shared articles, so this top-level command is not installed into a normal six-pack checkout ([wrapper, lines 16–26](https://github.com/unclebob/swarm-forge/blob/59803dadb38e0e09d5357d749452036e4a82ae60/swarm#L16-L26)).

## Reproduction performed against the pin

I reproduced the mechanism with current `swarm-cleanup.sh`, three tmux sessions on one scratch socket (`owner`, `worker`, `verifier`), and an unrelated session on a second socket. In zsh 5.9, I ran the exact upstream suffix after `sleep 120`, sent `SIGKILL` to that foreground `sleep` only, and observed:

```text
before: owner,verifier,worker
after foreground SIGKILL: target_server_gone=yes
unrelated_control_alive=yes
```

The test deliberately substitutes `sleep` for an agent CLI: it proves shell/cleanup/tmux mechanics, not loss of a real model turn. The draft's live six-agent evidence remains valuable, but it should include the installed script revision or checksum and the tmux `default-shell`.

## Corrections required in the draft

### 1. Quote the actual source

Current upstream ends the cleanup command with:

```text
>/dev/null 2>&1 &!; exit $exit_code
```

not `& disown`. No published upstream commit containing `disown` was found across the fetched refs. `&!` is zsh syntax for a background job that is immediately disowned. tmux creates the role panes with its configured login shell, selected through `default-shell` ([tmux manual](https://man.openbsd.org/tmux.1#default-shell)); merely having zsh installed does not prove that shell is zsh.

This distinction is observable: an interactive Bash 5.2 pane with default history expansion rejected the upstream line as `bash: !: event not found`; after `set +H`, the crash teardown reproduced. State `tmux show-options -gv default-shell` in the issue and test an unmodified checkout. Do not use the draft's `bash -c` example as the primary proof of a zsh-interpreted launcher line.

### 2. Narrow “anything that ends the CLI”

Use: “Any termination of the cleanup-owner agent process that returns control to its still-live parent shell runs cleanup, regardless of exit status.” This accurately covers `/exit`, an ordinary crash, and a signal directed only at the CLI. It excludes loss of the shell/session/server.

### 3. Reframe expected versus defective behavior

Lead with the crash case, not `/exit`. The upstream README explicitly defines the first configured role as the shutdown owner, so a maintainer can reasonably classify deliberate exit as shutdown. The adversarially strong property is: **an unplanned failure of one agent is indistinguishable from an explicit operator request to close the swarm, and active workers are not consulted.** Use “cleanup owner” generically; add that six-pack assigns that role to `specifier`.

### 4. Remove the `session_activity` proposal

`#{session_activity}` is not pane-output activity. In tmux 3.4 it returns `session.activity_time` ([format implementation](https://github.com/tmux/tmux/blob/9ae69c3795ab5ef6b4d760f6398cd9281151f632/format.c#L2644-L2650)); that field is updated for client attachment/input ([session update](https://github.com/tmux/tmux/blob/9ae69c3795ab5ef6b4d760f6398cd9281151f632/server-client.c#L1868-L1877)). Pane output instead updates `window.activity_time` ([input path](https://github.com/tmux/tmux/blob/9ae69c3795ab5ef6b4d760f6398cd9281151f632/input.c#L967-L976)). In a live tmux 3.4 test, a detached pane printing every 200 ms left `session_activity` unchanged while `window_activity` advanced. The proposed guard could therefore mark an actively generating worker idle—the opposite failure from the draft's spinner concern. `window_activity` is closer, but remains a heuristic, not a busy-state protocol.

### 5. Bound the loss claims

A clean worktree after teardown proves that the interrupted turn produced no project-file changes. It does not prove that every CLI wrote no cache, transcript, or recovery state anywhere. Prefer “the observed turn left no worktree changes; any in-memory-only progress was lost” over “discarded whatever they had not yet written to disk” as a universal claim.

### 6. Qualify SSH detach

The draft's result is correct under normal tmux defaults: `destroy-unattached` defaults to off, so detaching a client leaves the session alive ([tmux manual](https://man.openbsd.org/tmux.1#destroy-unattached)). Phrase this as the tested/default configuration, not an unconditional property of every user tmux configuration.

## Recommended filing shape

Suggested title: **Unexpected exit of the cleanup-owner agent kills every swarm session, including active workers**

The issue should include:

- exact SwarmForge commit plus `sha256sum swarmforge/scripts/{swarmforge.bb,swarm-cleanup.sh}` (the runnable wrapper caches scripts instead of overwriting them);
- `tmux -V`, `zsh --version`, and `tmux show-options -gv default-shell`;
- an unmodified six-pack reproduction;
- the existing live `/exit` observation, labelled as the easy operator path;
- a second, decisive reproduction that sends `SIGKILL` to the specifier **agent process only**, leaving its parent shell and tmux session alive;
- before/after session lists and a separate-socket control session;
- the desired invariant: only an explicit shutdown action (`close-swarm`, a dedicated command, or equivalent) may destroy peer sessions; cleanup-owner agent failure must leave workers alive or enter a recoverable state.

No open upstream issue or PR with this teardown/crash subject was present in the repository's issue/PR list at audit time.
