# DRAFT — not filed. Intended for github.com/unclebob/swarm-forge

Status: adversarially reviewed and reproduced against current upstream on 2026-08-13. Ready for maintainer review after replacing the synthetic two-role result with the real six-pack command transcript described below, if desired.

---

# Unexpected exit of the cleanup-owner agent kills every swarm session, including active workers

If the first configured agent process exits unexpectedly while its parent shell remains alive, SwarmForge immediately tears down every role session. It does not distinguish a crashed agent CLI from an explicit request to stop the swarm, and it does not check whether peer agents are mid-task.

In `six-pack`, the first configured role is the specifier. A failure of that one CLI can therefore terminate the coder, cleaner, architect, hardender, and QA processes while they are working.

I reproduced this both in a live six-pack and with a deterministic two-role harness against current upstream. The strongest defect case is an unexpected CLI failure. Upstream explicitly documents closing the first configured window as intentional whole-swarm shutdown, so typed `/exit` may reasonably be treated as expected behavior even though it follows the same path.

## Version and environment

- SwarmForge `main`: `9acd54d2239fef7e41ddacd8fd30dfb0e69672fe`
- SwarmForge `six-pack`: `59803dadb38e0e09d5357d749452036e4a82ae60`
- Ubuntu 24.04.3 LTS
- tmux 3.4
- zsh 5.9
- Babashka 1.13.219
- tmux `default-shell` during the deterministic reproduction: `/usr/bin/zsh`

Checksums of the tested upstream scripts:

```text
5c7b430e0fa2a23b95ae5016f23ea3979de0f78a155cbdcb82e96af6e4243c42  swarmforge/scripts/swarmforge.bb
6db2b3f02903f857e2d837ee1c54ca27f161e66afd45ff1683f9bad204a0debd  swarmforge/scripts/swarm-cleanup.sh
```

The script hashes matter because a runnable pack's wrapper reuses its installed `swarmforge/scripts/` directory instead of overwriting it on later launches.

## Mechanism

At current `main`, `swarmforge/scripts/swarmforge.bb:323-349` adds a cleanup suffix only to row index 0:

```clojure
(= index 0)
(str "; exit_code=$?; SWARMFORGE_TERMINAL_BACKEND=" (sq (:terminal-backend ctx))
     " nohup " (sq (str (fs/path (:script-dir ctx) "swarm-cleanup.sh")))
     " " (sq (:tmux-socket ctx))
     " " (sq (str (:window-ids-file ctx)))
     (apply str (map #(str " " (sq (:session %))) (:roles ctx)))
     " >/dev/null 2>&1 &!; exit $exit_code")
```

When the foreground agent command returns, exits nonzero, or is killed without also killing its parent shell, the shell continues to that suffix. Cleanup is unconditional; the captured exit code is re-raised but does not gate teardown.

`swarmforge/scripts/swarm-cleanup.sh:36-38` then kills every configured session it was given:

```zsh
for session in "$@"; do
  tmux -S "$TMUX_SOCKET" kill-session -t "$session" 2>/dev/null || true
done
```

There is no worker-busy or in-progress check between these two points.

This is specifically a failure of the cleanup-owner **agent process** while its parent shell survives. Killing the shell, tmux session, or server can prevent the suffix from running. An OOM event has this effect only if the OOM killer selects the agent CLI and leaves the parent shell alive.

## Deterministic reproduction

I used an isolated two-role fixture with the unmodified current launcher and cleanup scripts. The fixture substitutes a small local process for each agent backend:

- the cleanup-owner process waits for a trigger, then exits 0, exits 23, or sends `SIGKILL` to itself;
- the worker continually writes a heartbeat, making it observably active;
- both run in normal SwarmForge tmux sessions on the project socket;
- an unrelated `sleep` session runs on a separate tmux socket as a blast-radius control.

Command:

```sh
for mode in exit-zero exit-nonzero crash; do
  ./repro-swarm-forge-teardown.sh /path/to/swarm-forge "$mode"
done
```

Each mode deterministically reported:

```text
specifier_session_alive=no
worker_session_alive=no
worker_process_alive=no
unrelated_socket_session_alive=yes
RED: first-agent termination destroyed the active peer session
```

The boundary check kills the cleanup-owner's parent shell before killing the agent process:

```sh
./repro-swarm-forge-teardown.sh /path/to/swarm-forge shell-crash
```

It reported:

```text
specifier_session_alive=no
worker_session_alive=yes
worker_process_alive=yes
unrelated_socket_session_alive=yes
GREEN: parent-shell death did not trigger peer cleanup
```

This proves the launcher/shell/tmux failure mode without consuming model turns. It does not itself prove loss of model context, so I also tested the real workflow as described next.

## Live six-pack observation

On a live six-pack using the same Linux host and `SWARMFORGE_TERMINAL=none`:

1. I launched `./swarm` and confirmed all six sessions existed on the project's socket.
2. I gave the coder a real task and confirmed its UI said `esc to interrupt`.
3. I typed `/exit` in the specifier.
4. Twelve seconds later, `tmux -S <project-socket> ls` reported no server on that socket.
5. The coder worktree remained clean at its prior commit. The interrupted turn left no worktree changes; any in-memory-only progress was lost.
6. A control session on the default tmux socket remained alive.

That live run used a deliberate exit, which upstream may regard as the documented shutdown gesture. A decisive end-to-end crash reproduction would repeat it while sending `SIGKILL` to the specifier's foreground agent PID only and confirming that its shell remains alive long enough to launch cleanup. The deterministic harness above already confirms those exact mechanics on the current scripts.

## Expected behavior

An unexpected failure of the cleanup-owner agent should not be interpreted as an explicit request to destroy peer sessions. Active workers should remain alive, or the swarm should enter a recoverable state that lets the operator choose whether to reattach, restart the owner, or close the swarm.

Only an explicit shutdown action should destroy all peer sessions.

## Possible direction

Current `main` already includes a top-level `close-swarm` command whose commit message says it stops a running swarm "without relying on the cleanup window." That gives the lifecycle an explicit teardown operation.

The change I would trust most is to make whole-swarm teardown depend on an explicit shutdown action such as `close-swarm`, not on the cleanup-owner agent process returning. On unexpected agent exit, preserve the other sessions and print recovery/teardown commands. The cleanup-owner session itself can end normally.

A prompt alone does not cover crashes. An external activity heuristic is also unlikely to be reliable enough to decide whether killing an agent is safe. In particular, `#{session_activity}` is not pane-output activity in tmux 3.4: in a detached pane printing every 100 ms, `session_activity` stayed fixed while `window_activity` advanced. `window_activity` is closer, but still cannot identify whether an agent has valuable in-memory work.

## Scope and qualifications

- The kill list is scoped to the project-specific socket and explicit configured session names. The separate-socket control survived. The defect is timing/lifecycle coupling, not cross-project blast radius.
- A normal dropped SSH client leaves tmux sessions alive under the default `destroy-unattached off`; non-default tmux configuration can differ.
- The generated suffix uses zsh's `&!` syntax. The reproduction pinned tmux `default-shell` to zsh. Interactive Bash 5.2 on this host rejected the line as history expansion unless history expansion was disabled; that is a separate compatibility concern, not evidence for this teardown report.

---

## Reviewer notes — strip before posting

Primary-source audit: `swarm-forge-issue-1-upstream-audit.md`.

Reproduction harness: `repro-swarm-forge-teardown.sh`.

Remaining optional evidence upgrade before filing:

1. Run an unmodified current `six-pack` checkout whose installed scripts match the hashes above.
2. Start genuine work in one peer role.
3. Record `tmux show-options -gv default-shell` and the cleanup-owner pane shell/agent PIDs.
4. Send `SIGKILL` to the foreground agent PID only.
5. Capture the before/after session list and peer worktree status.

This would upgrade the real-agent crash case from shell-level/deterministic proof to full end-to-end proof. It is not necessary to establish the source-level bug, but it would make the report harder to dismiss as a synthetic reproduction.
