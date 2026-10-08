# DRAFT - not filed. Intended for github.com/unclebob/swarm-forge

Status: awaiting cross-family agent review. Do not post as-is.
Written 2026-08-12. Companion draft: `swarm-forge-issue-1-teardown.md`.
Could go as a one-line PR instead of an issue.

---

# swarm-cleanup.sh is zsh-only, so teardown silently does nothing without zsh

Tested against `main` at `9acd54d2239fef7e41ddacd8fd30dfb0e69672fe`, on Ubuntu 24.04.3 LTS with bash 5.2 and zsh 5.9. The file below is byte-identical to upstream:

```text
6db2b3f02903f857e2d837ee1c54ca27f161e66afd45ff1683f9bad204a0debd  swarmforge/scripts/swarm-cleanup.sh
```

`swarmforge/scripts/swarm-cleanup.sh` is `#!/usr/bin/env zsh` and uses a zsh-only number glob at line 30:

```zsh
if [[ "$daemon_pid" == <-> ]]; then
```

`<->` matches a run of digits in zsh. bash has no such operator and cannot parse the line:

```
$ bash -n swarmforge/scripts/swarm-cleanup.sh
line 30: unexpected argument `<' to conditional binary operator
line 30: syntax error near `<-'
line 30: `    if [[ "$daemon_pid" == <-> ]]; then'

$ zsh -n swarmforge/scripts/swarm-cleanup.sh
$ echo $?
0
```

## Why it matters

Because it is a parse error rather than a runtime one, nothing in the script runs when it is executed by bash. Not the daemon stop, not the `kill-session` loop, not the terminal-window close. Teardown fails whole rather than partially, and it fails quietly: the launcher fires it as `nohup ... >/dev/null 2>&1 &!`, so the error goes nowhere a person will see it. The symptom is a swarm that never cleans up and a handoff daemon that keeps running, with no message explaining why.

This sits on a project whose other entry points do not need zsh: `swarm` is `#!/usr/bin/env bash`, and the launcher itself is babashka. So zsh is an undeclared dependency of exactly one script.

How often that script lands on a host without zsh, from Debian/Ubuntu packaging metadata rather than impression:

```
zsh    Priority: optional
bash   Priority: required, Essential: yes
```

Priority decides what a default install contains: `required`, `important`, and `standard` are installed, `optional` is not. Nothing outside zsh's own ecosystem pulls it in either - the packages that `Depends: zsh` are `zsh-common`, `zsh-syntax-highlighting`, `zsh-autosuggestions`, `zplug`, `antigen`, and similar. A host has zsh because somebody asked for it.

On this droplet, somebody did:

```
$ apt-mark showmanual | grep -x zsh
zsh
$ grep zsh /var/log/apt/history.log
Commandline: apt-get install -y zsh
```

Which is why I did not hit this myself - teardown ran here. I found it reading the script while investigating a separate teardown problem.

The population most likely to be affected is fresh server installs, cloud VMs, CI runners, and the `debian`/`ubuntu` container images, none of which carry zsh. That is also the population running long-lived agents somewhere other than a laptop. macOS is the opposite case: zsh has been the default login shell since Catalina, so on SwarmForge's home platform this is invisible, which may be why it has gone unnoticed.

## Fix

The guard only needs "is this string all digits", which bash and zsh both express:

```zsh
if [[ "$daemon_pid" =~ ^[0-9]+$ ]]; then
```

With that one line changed, `bash -n` accepts the file.

That is not the same as making the teardown bash-safe, though, and the difference is worth knowing before anyone changes the shebang. `swarm-cleanup.sh` sources `swarm-terminal-adapter.sh`, which is also `#!/usr/bin/env zsh` and uses another zsh-only expansion at line 6:

```zsh
normalize_terminal_backend() {
  local backend="${1:l}"
```

`${1:l}` lowercases in zsh. bash parses it without complaint and returns the string **unchanged**:

```
$ bash -c 'x=ITerm; echo "${x:l}"'
ITerm
$ zsh -c 'x=ITerm; echo "${x:l}"'
iterm
```

So under bash the backend name never lowercases, `normalize_terminal_backend` falls through its `case` to the default branch, and `load_terminal_backend` reports `Unknown terminal backend 'ITerm'`. Silent misbehavior rather than a clean failure, which is the worse of the two.

Two coherent options, and I do not want to pick for you:

- **Keep zsh, declare it.** Add it to the README next to babashka and tmux, and let the launcher check for it up front and refuse to start rather than discovering it at teardown. The teardown currently runs as `nohup ... >/dev/null 2>&1 &!`, so its output goes nowhere a person will see - that redirect is what turns a dependency problem into a silent one.
- **Make both scripts bash-clean.** `<->` becomes `=~ ^[0-9]+$` and `${1:l}` becomes `${1,,}` (bash 4+) or a `tr` call, then both shebangs can move to bash. Wider reach, but it needs a pass over the adapters for anything else I have not spotted.

Happy to send either as a PR - say which you prefer.

---

## Reviewer notes (strip before posting)

- `bash -n` / `zsh -n` output above is verbatim from this host (bash 5.2, zsh 5.9, Ubuntu 24.04.3 LTS).
- The claim "nothing in the script runs" follows from it being a parse error, which `-n` demonstrates directly.
- Resolved: the "not installed by default" claim now rests on `Priority: optional` vs bash's `Priority: required / Essential: yes`, on the reverse-depends list containing only zsh's own ecosystem, and on this host's own `apt-get install -y zsh` history. Not verified by booting a clean image or pulling a base container, so the draft claims "not part of a default install" rather than "absent from every server".
- Verified after the first pass: `swarm-terminal-adapter.sh` carries a second zsh-ism, `${1:l}` at line 6, which bash silently no-ops rather than erroring (output quoted in the draft). `bash -n` passes on that file, so a parse check alone would have missed it. This is why the draft no longer says "just move the shebang to bash".
- Not audited: `terminal-adapters/*.sh` for further zsh-isms. Grepped only for `${...:l}`, `${...:u}`, and `<->`.
