#!/usr/bin/env bash
set -euo pipefail

UPSTREAM_DIR="${1:-$(pwd)/.scratch/swarm-forge-upstream}"
EXIT_MODE="${2:-exit-zero}"

if [[ ! -x "$UPSTREAM_DIR/swarmforge/scripts/swarmforge.sh" ]]; then
  echo "usage: $0 <swarm-forge-checkout> [exit-zero|exit-nonzero|crash]" >&2
  exit 2
fi

case "$EXIT_MODE" in
  exit-zero | exit-nonzero | crash | shell-crash) ;;
  *)
    echo "unknown exit mode: $EXIT_MODE" >&2
    exit 2
    ;;
esac

REPRO_DIR="$(mktemp -d -t swarmforge-teardown-repro.XXXXXX)"
UNRELATED_SOCKET="$REPRO_DIR/unrelated.sock"
UNRELATED_SESSION="unrelated-control-$$"
LAUNCHER_PID=""

cleanup() {
  if [[ -n "$LAUNCHER_PID" ]] && kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    kill "$LAUNCHER_PID" 2>/dev/null || true
    wait "$LAUNCHER_PID" 2>/dev/null || true
  fi

  if [[ -f "$REPRO_DIR/project/.swarmforge/tmux-socket" ]]; then
    local swarm_socket
    swarm_socket="$(tr -d '\r\n' < "$REPRO_DIR/project/.swarmforge/tmux-socket")"
    tmux -S "$swarm_socket" kill-server 2>/dev/null || true
  fi

  tmux -S "$UNRELATED_SOCKET" kill-server 2>/dev/null || true
  if [[ "${KEEP_SWARMFORGE_REPRO:-0}" == 1 ]]; then
    echo "kept_repro_dir=$REPRO_DIR" >&2
  else
    rm -rf "$REPRO_DIR"
  fi
}
trap cleanup EXIT

mkdir -p "$REPRO_DIR/project/swarmforge/roles" "$REPRO_DIR/bin"
cp -R "$UPSTREAM_DIR/swarmforge/scripts" "$REPRO_DIR/project/swarmforge/scripts"
printf 'Read the test constitution.\n' > "$REPRO_DIR/project/swarmforge/constitution.prompt"
printf 'Stay alive until externally stopped.\n' > "$REPRO_DIR/project/swarmforge/roles/specifier.prompt"
printf 'Stay alive and simulate in-progress work.\n' > "$REPRO_DIR/project/swarmforge/roles/worker.prompt"
printf '%s\n' \
  'window specifier codex master' \
  'window worker codex none' \
  > "$REPRO_DIR/project/swarmforge/swarmforge.conf"

printf '#!/usr/bin/env bash\nset -euo pipefail\nroot=%q\n' "$REPRO_DIR" \
  > "$REPRO_DIR/bin/codex"
printf '%s\n' \
  'role="${SWARMFORGE_ROLE:?}"' \
  'printf "%s\n" "$$" > "$root/$role.pid"' \
  'printf "started\n" > "$root/$role.state"' \
  'if [[ "$role" == "specifier" ]]; then' \
  '  while [[ ! -f "$root/trigger" ]]; do sleep 0.02; done' \
  '  mode="$(< "$root/trigger")"' \
  '  case "$mode" in' \
  '    exit-zero) exit 0 ;;' \
  '    exit-nonzero) exit 23 ;;' \
  '    crash) kill -KILL "$$" ;;' \
  '    shell-crash) kill -KILL "$PPID"; kill -KILL "$$" ;;' \
  '  esac' \
  'else' \
  '  trap '\''printf "terminated\n" > "$root/worker.state"; exit 0'\'' HUP TERM INT EXIT' \
  '  while true; do' \
  '    date +%s%N > "$root/worker.heartbeat"' \
  '    sleep 0.02' \
  '  done' \
  'fi' \
  >> "$REPRO_DIR/bin/codex"
chmod +x "$REPRO_DIR/bin/codex"
cp "$REPRO_DIR/bin/codex" "$REPRO_DIR/project/swarmforge/scripts/codex"

git -C "$REPRO_DIR/project" init -q
git -C "$REPRO_DIR/project" config user.email repro@example.invalid
git -C "$REPRO_DIR/project" config user.name 'SwarmForge Repro'
git -C "$REPRO_DIR/project" add swarmforge
git -C "$REPRO_DIR/project" commit -qm 'Minimal two-role repro fixture'

"$REPRO_DIR/project/swarmforge/scripts/swarmforge.bb" --test-parse "$REPRO_DIR/project" >/dev/null
SWARM_SOCKET="$(tr -d '\r\n' < "$REPRO_DIR/project/.swarmforge/tmux-socket")"
tmux -S "$SWARM_SOCKET" -f /dev/null new-session -d -s bootstrap 'sleep 120'
tmux -S "$SWARM_SOCKET" set-option -g default-shell "$(command -v zsh)"
tmux -S "$SWARM_SOCKET" set-option -g exit-empty off
tmux -S "$SWARM_SOCKET" kill-session -t bootstrap

tmux -S "$UNRELATED_SOCKET" new-session -d -s "$UNRELATED_SESSION" 'sleep 120'

PATH="$REPRO_DIR/bin:$PATH" \
SWARMFORGE_TERMINAL=none \
SWARMFORGE_PREVENT_SLEEP=0 \
SWARMFORGE_AGENT_START_DELAY_MS=0 \
script -qefc "$REPRO_DIR/project/swarmforge/scripts/swarmforge.sh $REPRO_DIR/project" "$REPRO_DIR/launcher.log" \
  >/dev/null 2>&1 &
LAUNCHER_PID=$!

for _ in $(seq 1 250); do
  if [[ -f "$REPRO_DIR/specifier.pid" && -f "$REPRO_DIR/worker.pid" && -f "$REPRO_DIR/project/.swarmforge/tmux-socket" ]]; then
    break
  fi
  sleep 0.02
done

if [[ ! -f "$REPRO_DIR/specifier.pid" || ! -f "$REPRO_DIR/worker.pid" ]]; then
  echo "FAIL: agents did not start" >&2
  sed -n '1,160p' "$REPRO_DIR/launcher.log" >&2 || true
  exit 1
fi

WORKER_PID="$(tr -d '\r\n' < "$REPRO_DIR/worker.pid")"

tmux -S "$SWARM_SOCKET" has-session -t swarmforge-specifier
tmux -S "$SWARM_SOCKET" has-session -t swarmforge-worker
kill -0 "$WORKER_PID"

printf '%s\n' "$EXIT_MODE" > "$REPRO_DIR/trigger"

for _ in $(seq 1 250); do
  if ! tmux -S "$SWARM_SOCKET" has-session -t swarmforge-worker 2>/dev/null; then
    break
  fi
  sleep 0.02
done

SWARM_SPECIFIER_ALIVE=yes
SWARM_WORKER_ALIVE=yes
WORKER_PROCESS_ALIVE=yes
UNRELATED_ALIVE=no
tmux -S "$SWARM_SOCKET" has-session -t swarmforge-specifier 2>/dev/null || SWARM_SPECIFIER_ALIVE=no
tmux -S "$SWARM_SOCKET" has-session -t swarmforge-worker 2>/dev/null || SWARM_WORKER_ALIVE=no
kill -0 "$WORKER_PID" 2>/dev/null || WORKER_PROCESS_ALIVE=no
tmux -S "$UNRELATED_SOCKET" has-session -t "$UNRELATED_SESSION" 2>/dev/null && UNRELATED_ALIVE=yes

printf 'upstream_commit=%s\n' "$(git -C "$UPSTREAM_DIR" rev-parse HEAD)"
printf 'trigger=%s\n' "$EXIT_MODE"
printf 'tmux_default_shell=%s\n' "$(tmux -S "$SWARM_SOCKET" show-options -gv default-shell)"
printf 'specifier_session_alive=%s\n' "$SWARM_SPECIFIER_ALIVE"
printf 'worker_session_alive=%s\n' "$SWARM_WORKER_ALIVE"
printf 'worker_process_alive=%s\n' "$WORKER_PROCESS_ALIVE"
printf 'unrelated_socket_session_alive=%s\n' "$UNRELATED_ALIVE"

if [[ "$EXIT_MODE" != shell-crash &&
      "$SWARM_SPECIFIER_ALIVE" == no &&
      "$SWARM_WORKER_ALIVE" == no &&
      "$WORKER_PROCESS_ALIVE" == no &&
      "$UNRELATED_ALIVE" == yes ]]; then
  echo 'RED: first-agent termination destroyed the active peer session'
  exit 1
fi

if [[ "$EXIT_MODE" == shell-crash &&
      "$SWARM_WORKER_ALIVE" == yes &&
      "$WORKER_PROCESS_ALIVE" == yes &&
      "$UNRELATED_ALIVE" == yes ]]; then
  echo 'GREEN: parent-shell death did not trigger peer cleanup'
  exit 0
fi

echo 'INCONCLUSIVE: observed state did not match the expected boundary'
exit 2
