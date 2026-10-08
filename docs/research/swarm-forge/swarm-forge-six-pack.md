# SwarmForge six-pack: primary-source research

Source: `github.com/unclebob/swarm-forge`, cloned and read directly.
Pinned commits at time of reading (2026-08-12): `main` = `9acd54d2239fef7e41ddacd8fd30dfb0e69672fe`, `six-pack` = `59803dadb38e0e09d5357d749452036e4a82ae60`.
Citations are `<branch>:<path>:<line>`. Every behavioral claim marked "verified" was reproduced by actually running the swarm on this Ubuntu host (tmux 3.4, zsh 5.9 present, babashka 1.13.219 present) against a scratch project.

Branches: `main`, `six-pack`, `four-pack`, `two-pack`, `squad`, `adversaries` (`git branch -r`).

## 0. The split that matters: six-pack is a config overlay, not code

`six-pack` carries 13 files and **no executable logic at all**:

```
swarm
swarmforge/swarmforge.conf
swarmforge/constitution.prompt
swarmforge/constitution/articles/{project,local-engineering,local-workflow}.prompt
swarmforge/roles/{specifier,coder,cleaner,architect,hardender,QA}.prompt
```

All machinery lives on `main` under `swarmforge/scripts/` (`main:swarmforge/scripts/swarmforge.bb`, 592 lines, is the launcher). `six-pack:swarm:16-27` downloads the `main` tarball at first run and copies `swarmforge/scripts` into place. So "reading six-pack" means reading `main`'s scripts plus `six-pack`'s prompts. Anyone vendoring six-pack alone gets prompts and nothing that runs.

`six-pack:.gitignore:6` ignores `swarmforge/scripts/`, confirming the scripts are runtime-fetched, not vendored.

---

## 1. The six roles

Topology from `six-pack:swarmforge/swarmforge.conf:5-10`, verbatim:

```
window specifier codex master
window coder codex coder
window cleaner codex cleaner batch
window architect codex architect batch
window hardender codex hardender batch
window QA codex QA batch
```

Five columns: `window <role> <agent> <worktree> [task|batch] [extra-cli-args]` (`main:swarmforge/scripts/swarmforge.bb:149-158`). All six default to the `codex` CLI. The specifier runs in `master` (the main checkout, no worktree). The four later roles are `batch` mode; specifier and coder are `task` mode.

Chain, stated in the protocol doc: `specifier -> coder -> cleaner -> architect -> hardender -> QA` (`main:swarmforge/handoff-protocol.md:171-173`).

### specifier (`six-pack:swarmforge/roles/specifier.prompt`)
Owns Gherkin specs, acceptance criteria, and the end-to-end QA suite spec. It is the only role that talks to the human and the only one that gates a handoff on human approval:

> "Do not commit or notify coder until the user explicitly approves the handoff. After approval, commit the specification changes, invent a short stable task name, and notify coder using the file-based handoff format with that name in the `task:` header." (`:36`)

> "When QA notifies you that the job is complete, merge the changes and ask the user for the next feature to add." (`:37`)

Its six-phase feature workflow (`:23-29`) ends with "Ask the user for approval to hand off to the coder." The end-to-end constraint is strict: "End-to-end means the QA suite operates at the user interface and does not use an API into the project." (`:18`)

Notably it is exempt from testing: `six-pack:swarmforge/constitution/articles/local-engineering.prompt:4` says "Every agent **except the specifier** must run unit tests and acceptance tests before handoff."

### coder (`six-pack:swarmforge/roles/coder.prompt`)
Owns implementation of approved behavior slices, TDD-first:

> "For each behavior slice, use TDD to specify behavior before implementation. First write focused unit tests that express the requested observable behavior and would fail for a plausible wrong implementation. Then write only enough production code to pass those tests." (`:18`)

Must stand up the acceptance pipeline from `github.com/unclebob/Acceptance-Pipeline-Specification` at startup (`:9-13`) and must not reimplement `gherkin-parser`. Explicit non-ownership: no mutation, no CRAP, no DRY, no Gherkin acceptance mutation, ignore the QA suite (`:24-26`). Hands off to cleaner (`:29`).

### cleaner (`six-pack:swarmforge/roles/cleaner.prompt`)
Owns behavior-preserving *local* cleanup, explicitly bounded below architecture:

> "Split functions or files that mix unrelated local responsibilities, but leave high-level dependency direction and architectural boundary decisions to the architect." (`:11`)

Carries two hard numeric gates, the only quantitative thresholds in the whole six-pack:

> "Run the language CRAP tool first and reduce CRAP to 6 or below. Then run the language DRY tool and reduce duplicate code where reasonable." (`:21`)

> "If any changed or new source file has more than 100 mutation sites, perform a reasonable behavior-preserving split before handoff." (`:23`)

It *counts* mutation sites but must not run mutation tests (`:22`, `:27`). Hands off to architect (`:34`).

### architect (`six-pack:swarmforge/roles/architect.prompt`)
The longest prompt (3625 bytes). Owns module structure and dependency direction, restating Clean Architecture as operational rules:

> "Treat high-level modules as far from IO and low-level modules as near IO. Manage dependencies so they point from low-level modules toward high-level modules." (`:13-14`)

> "Design boundaries that maximize testable high-level modules and minimize environmentally unsuitable adapter shells." (`:17`)

Four named review phases (`:25-28`): UI/Core Separation, Dependency Rule, Information Hiding And Encapsulation, Local Code Quality. It also owns property testing, including building a framework if none fits:

> "Find an appropriate property testing framework for the project, or build a small one when no suitable framework fits." (`:32`)

Hands off to hardender (`:42`).

### hardender (`six-pack:swarmforge/roles/hardender.prompt`)
Owns mutation hardening. Note the spelling: the role is `hardender`, not `hardener`, everywhere including the filename.

> "Use mutation to cover the uncovered and kill survivors." (`:10`)

> "Run the language mutation tool one file at a time in sequence. Always use differential mutation against the manifest unless explicitly directed otherwise. Time is of the essence during mutation work..." (`:15-17`)

Fixed verification order at handoff:

> "run the language mutation tool, then soft Gherkin acceptance mutation (`--level soft`), then the language CRAP tool, then the language DRY tool unless directed otherwise. Fix any issues each tool finds before running the next one." (`:30`)

A rare bit of judgment permitted: "If Gherkin mutation exposes a no-op step, consider removing that step from the Gherkin rather than adding example columns only to assert the no-op." (`:27`). Hands off to QA (`:31`).

### QA (`six-pack:swarmforge/roles/QA.prompt`)
Final independent verification. Converts the specifier's prose QA procedures into executable scripts and runs them only through the UI:

> "Run the end-to-end QA suite through the user interface only; do not use an API into the project for end-to-end verification." (`:16`)

> "You may add command-line arguments or UI commands to expose hard-to-test logic, provided those affordances operate at the user interface and do not create a private project API for QA." (`:18`)

Stop-the-line clause: "If the QA suite contradicts the Gherkin or unit tests, stop and ask for clarification before changing behavior." (`:19`)

Terminal broadcast: "When verification passes, commit any QA-owned changes and notify the specifier, coder, cleaner, architect, and hardender that QA is complete using the file-based handoff format with `priority: 00`." (`:28`)

### Constitution layering
`six-pack:swarmforge/constitution.prompt` is 4 lines: "This file takes precedence over article files. Read and obey every file in `swarmforge/constitution/articles/`."

Shared articles on `main` (`main:swarmforge/constitution/articles/{engineering,handoffs,workflow}.prompt`) carry the handoff rules, worktree discipline, and language tool table. Six-pack adds three local articles. The QA-broadcast exception is the interesting one:

> "A QA handoff does not interrupt current work. If a QA wake-up arrives while working, ignore it..." and "When a helper-delivered task is from QA, merge the sender commit identified by the printed `PAYLOAD` (`merge_and_process QA <commit>`)... Do not send a `git_handoff` downstream." (`six-pack:swarmforge/constitution/articles/local-workflow.prompt:4-5`)

**Verified defect:** the three shared articles are never installed. `six-pack:swarm:23-26` copies them into `swarmforge/scripts/shared-articles/`, and nothing in `main:swarmforge/scripts/` ever references `shared-articles` (grep across the whole scripts dir returns nothing). On my run, `swarmforge/constitution/articles/` contained only the three six-pack-local files. Agents therefore never read `handoffs.prompt` - the authoritative document telling them how to send a handoff. This is open issue #32.

---

## 2. The handoff protocol, end to end

Design doc: `main:swarmforge/handoff-protocol.md` (586 lines). Implementation: `main:swarmforge/scripts/swarm_handoff.bb`, `handoffd.bb`, `ready_for_next*.bb`, `done_with_current*.bb`. Every `.sh` in `swarmforge/scripts/` is a 5-line `#!/usr/bin/env zsh` shim that execs `bb <same-name>.bb` (e.g. `main:swarmforge/scripts/swarm_handoff.sh:1-5`).

### `swarm_handoff.sh <draft-file>` - the outbound gate

Takes exactly one argument, a draft file of headers only (`main:swarmforge/scripts/swarm_handoff.bb:316-321`). Sender role comes from `$SWARMFORGE_ROLE`, not from the draft (`:73-76`).

What it **rejects** (all verified by running it):

| Rejection | Cite |
|---|---|
| Any non-blank line after the first blank line: "draft handoffs may contain headers only; payloads are generated by swarm_handoff.sh." | `:104-106` |
| Line not matching `field: value` | `:111-113` |
| Reserved header written by agent: `id from role recipient created_at enqueued_at dequeued_at completed_at` | `:21`, `:122-124` |
| Unknown header (allowed set is exactly `type to priority task commit message`) | `:22`, `:126-128` |
| Duplicate header | `:130-132` |
| `type` not in `{git_handoff, note}` | `:23`, `:199-200` |
| `priority` not matching `[0-9][0-9]` | `:89-90`, `:201-202` |
| Missing `type`, `to`, or `priority` | `:196-198` |
| Recipient with `_` in the name, empty recipient, duplicate recipient, recipient not in `roles.tsv` | `:145-153` |
| `commit` not exactly 10 hex chars | `:207-208` |
| `commit` resolving to zero or >1 git objects, or to a non-commit object | `:157-171` |
| `task` missing or >80 chars on a `git_handoff` | `:214-217` |
| `message` missing or >80 chars on a `note` | `:227-230` |
| Cross-type header leakage (`commit`/`task` on a note, `message` on a git_handoff) | `:218-221`, `:231-232` |
| Unknown sender role | `:323-324` |

Exit codes: `2` for validation failure with a printed `HANDOFF INVALID: <path>` block plus the usage text (`:305-313`, `:328-330`); `1` for missing draft / unset role / unknown project root. On success it prints `HANDOFF QUEUED: <path>` and **deletes the draft** (`:335-336`).

Reproduced output for a deliberately broken draft:

```
HANDOFF INVALID: tmp/h1.txt

Errors:
- Line 4: header 'from' is reserved and must not be written by agents.
- Line 5: unknown header 'foo'.
- Header 'type' must be one of git_handoff or note; got 'gitmerge'.
- Header 'priority' must be two digits from 00 to 99; got 'urgent'.
- Unknown recipient role 'bogus'.
- Duplicate recipient 'cleaner'.
...
```

Note a rough edge: when `type` is invalid, the per-field allow-list check (`:181-194`) falls through to `false` for *every* header, so a single bad `type` produces a cascade of "Header 'to' is not allowed for type 'gitmerge'" noise.

### Message types

Only two (`:23`). The body is **generated**, never supplied:

```clojure
"git_handoff" (str "Re-read your role and constitution.\n\nmerge_and_process " sender " " canonical-commit)
"note"        (str "Re-read your role and constitution.\n\n" note-message)
```
(`main:swarmforge/scripts/swarm_handoff.bb:265-268`)

**`merge_and_process` is not a program.** Grepping all six branches finds it only in generated strings, the protocol doc, tests, and `six-pack:.../local-workflow.prompt:5`. There is no executable, no function, no alias. It is a natural-language instruction spelled like a shell command. That is open issue #29, where a Codex agent piped it to Bash and stopped on `command not found`.

`note` is deliberately discouraged: "Agents should not send `note` handoffs unless the user, role prompt, or constitution explicitly directs them to send one." (`main:swarmforge/handoff-protocol.md:194-198`, repeated at `main:swarmforge/constitution/articles/handoffs.prompt:8-12`).

### On-disk state

Per **worktree** (each role owns its own tree), created by `main:swarmforge/scripts/swarmforge.bb:261-264`:

```
.swarmforge/handoffs/
  sequence            # 6-digit counter, plain text
  sequence.lock/      # a DIRECTORY used as the mutex
  outbox/tmp/         # staging for atomic rename
  outbox/*.handoff    # daemon polls here
  sent/               # after successful delivery
  failed/             # + a sibling <file>.error
  inbox/new/          # daemon writes here
  inbox/in_process/   # single file, or batch_<ts>_<nnnnnn>/ dir
  inbox/completed/
```

Filename: `<priority>_<timestamp>_<sequence>_from_<sender>_to_<r1>_<r2>.handoff` (`swarm_handoff.bb:278`). Timestamp is `yyyyMMdd'T'HHmmss'Z'` UTC (`:85-87`). Verified example: `50_20260812T134329Z_000001_from_coder_to_cleaner.handoff`.

File format is RFC822-ish: header lines, one blank line, opaque body. Verified delivered file:

```
id: 20260812T134329Z_000001_from_coder
from: coder
to: cleaner
recipient: cleaner
priority: 50
type: git_handoff
role: coder
commit: f132de03bf
created_at: 2026-08-12T13:43:29.806786254Z
enqueued_at: 2026-08-12T13:43:30.777095870Z
task: demo-task

Re-read your role and constitution.

merge_and_process coder f132de03bf
```

(Cosmetic bug: `task` lands *after* `created_at` because `handoffd.bb:71-72`'s `preferred` header order lists `commit` but omits `task`. Harmless - `header-field` only scans up to the first blank line.)

**Locking is exactly one mechanism**: `fs/create-dir` on `<state>/handoffs/sequence.lock` as an atomic test-and-set, spinning every 50 ms, released in a `finally` (`swarm_handoff.bb:237-263`; duplicated at `handoff_lib.bb:138-158`). Nothing else in the system takes a lock. Inbox transitions rely on `fs/move` (rename) plus explicit "does the target already exist" pre-checks that `fail!` with `AMBIGUOUS_TASK_STATE` (`ready_for_next_task.bb:114-116`, `done_with_current_task.bb:89-90`). Note the lock directory is never reclaimed if `bb` is SIGKILLed mid-write - a stale `sequence.lock` deadlocks that worktree's outbound path forever.

### `handoffd.bb` - the delivery daemon

Started by the launcher (`swarmforge.bb:388-399`), polls every 1000 ms (`handoffd.bb:9`, `:194-196`). Per cycle it walks every role's `outbox/` (not `outbox/tmp/`, since it filters to regular `.handoff` files directly in `outbox/`, `:146-152`).

For each file (`deliver!`, `:123-144`):
1. Parse headers/body on the first `\n\n` (`:58-68`).
2. **Reject** if the `to` header is missing -> `fail!` writes `<path>.error` and moves the file to `failed/` (`:127-128`, `:117-121`).
3. **Throw** on an unknown recipient, which the caller catches and also routes to `failed/` (`:132-134`, `:171-178`).
4. For each recipient, add `recipient` + `enqueued_at` and write to that worktree's `inbox/new/<same filename>`, **skipping the write if the target already exists** (`:138-139`) - that is the entire dedup story.
5. `notify!` the recipient's tmux session.
6. Move the original to the sender's `sent/`, with a timestamp-prefixed rename on collision (`:107-115`, `:141-143`).

That is the complete validation surface of the daemon. The design doc is explicit that this is intentional: "The daemon does not perform a second full validation pass on outbox files; `swarm_handoff.sh` is the validation boundary." (`main:swarmforge/handoff-protocol.md:584-585`).

`notify!` (`:94-105`) is the fragile part - it types a literal sentence into the recipient's TUI and presses Enter twice:

```clojure
(sh "tmux" "-S" socket "send-keys" "-t" session "-l" wake-message)  ; 150ms
(sh "tmux" "-S" socket "send-keys" "-t" session "C-m")              ; 50ms
(sh "tmux" "-S" socket "send-keys" "-t" session "C-j")
```

with `wake-message` = `"You have new handoff mail. If idle, run ready_for_next.sh."` (`:10-11`). The target is a **bare session name**, so tmux resolves it to that session's current window's active pane. A non-zero exit throws, which aborts delivery for that file - but exit 0 only proves the keystrokes were injected, not that the agent's composer accepted them. That is open issue #34.

Lifecycle files: `.swarmforge/daemon/{handoffd.pid, handoffd.log, stop}` (`:21-27`). Shutdown is `stop` file or SIGTERM (`:154-155`, `:191`); `stop_handoff_daemon.bb:19-39` writes the stop file, sends TERM, waits 5 s polling at 100 ms, then SIGKILLs.

### `ready_for_next.sh` / `done_with_current.sh` - pure dispatchers

Both are ~66-line twins that read `$SWARMFORGE_ROLE`, look up field 7 of `.swarmforge/roles.tsv`, and `process/exec` into the task or batch variant (`ready_for_next.bb:60-64`, `done_with_current.bb:60-64`). Neither touches the queue. An unrecognized mode exits 2 with `INVALID_RECEIVE_MODE`.

`ready_for_next_task.bb` (task mode): if `in_process/` holds a batch dir -> exit 2 `TASK_IN_PROCESS_IS_BATCH`; if >1 in-process file -> exit 2 `AMBIGUOUS_TASK_STATE`; if exactly 1 -> reprint it (idempotent resume); else move the lexicographically first `new/*.handoff` to `in_process/`, stamp `dequeued_at`, print. Empty inbox prints `NO_TASK` and exits 0 (`:97-118`).

`ready_for_next_batch.bb` (batch mode): same guards inverted, then it reads the priority of the first queued file and sweeps **every** `new/` file with that same priority into `in_process/batch_<yyyyMMdd'T'HHmmss'Z'>_<nnnnnn>/` (`:134-146`). Only equal priority is batched; a lower-priority file waits.

`done_with_current_task.bb`: requires exactly one in-process file, stamps `completed_at`, moves to `completed/`, prints `COMPLETED: <path>`, then `exec`s `ready_for_next_task.sh` so the next task appears in the same output (`:68-93`). `done_with_current_batch.bb` does the same per file plus `COMPLETED_BATCH:` and deletes the now-empty batch dir (`:86-104`).

Verified full round trip as the batch-mode cleaner:

```
BATCH: .../in_process/batch_20260812T134342Z_000001
COUNT: 1
PRIORITY: 50

BATCH_ITEM: 1
TASK: .../batch_.../50_20260812T134329Z_000001_from_coder_to_cleaner.handoff
FROM: coder
TYPE: git_handoff
PRIORITY: 50
TASK_NAME: demo-task
PAYLOAD:
Re-read your role and constitution.

merge_and_process coder f132de03bf
```

and calling the wrong helper is correctly refused: `done_with_current_task.sh` -> `CURRENT_WORK_IS_BATCH: use done_with_current.sh.` exit 2.

Header lifecycle ownership (`main:swarmforge/handoff-protocol.md:517-523`): `swarm_handoff.sh` writes `id/from/to/priority/type/created_at`; `handoffd` writes `recipient/enqueued_at`; `ready_for_next_*` writes `dequeued_at`; `done_with_current_*` writes `completed_at`.

---

## 3. Worktrees: who merges, and when

Layout, verified on a real run:

```
proj/                        master              <- specifier (worktree "master")
proj/.worktrees/coder        swarmforge-coder
proj/.worktrees/cleaner      swarmforge-cleaner
proj/.worktrees/architect    swarmforge-architect
proj/.worktrees/hardender    swarmforge-hardender
proj/.worktrees/QA           swarmforge-QA
```

Created by `swarmforge.bb:251-259`: `git worktree add --force -B swarmforge-<name> <path> HEAD`, skipped when the configured worktree is `master` or `none` (`:256`). Branch name is always `swarmforge-` + the *worktree* column, not the role. `.worktrees/` is force-added to both `.gitignore` and `.git/info/exclude` (`:107-119`).

Each worktree also gets a private copy of the scripts and the runtime TSVs (`sync-worktree-scripts!`, `:270-286`) so agents never reach back into the master checkout.

**Work does not "move between worktrees" as files.** It moves as commits. The only transport is: role A commits on its own branch -> sends a `git_handoff` carrying a canonicalized 10-char SHA -> role B receives `merge_and_process A <sha>` and merges that SHA into its own branch.

**So the merging is done by the receiving agent, by hand, with no tooling and no defined strategy.** There is no merge script, no conflict policy, no `--no-ff` convention, nothing. The constitution forbids looking around: "Do not inspect, diff, merge, or base work on another branch unless that branch is specifically named in a handoff or explicit user instruction." (`main:swarmforge/constitution/articles/workflow.prompt:7`).

Conflicts are avoided structurally rather than resolved: the pipeline is strictly linear and each stage merges the immediately preceding stage's commit before doing its own work, so at steady state only one role has uncommitted work on any given task. Two mechanisms break that:

- **The QA terminal broadcast.** QA sends one `priority: 00` handoff to all five other roles (`six-pack:swarmforge/roles/QA.prompt:28`). Each merges and stops - "recipients do not re-forward that handoff down the chain" (`main:swarmforge/handoff-protocol.md:175-179`). This is the sync point that puts every branch back on the same commit.
- **The specifier's final merge into master.** "When QA notifies you that the job is complete, merge the changes and ask the user for the next feature" (`six-pack:swarmforge/roles/specifier.prompt:37`). The specifier runs in the `master` checkout, so this is the only path by which work reaches the trunk.

Note this means **the human is in the loop exactly twice per feature**: approving the spec-to-coder handoff, and choosing the next feature. Everything between is unattended.

Nothing prevents the specifier from accepting the next feature while a previous task is still in flight downstream - and `.gitignore`/`info/exclude` deliberately keep `.swarmforge/` out of git so handoff state is never merged (`workflow.prompt`, `handoffs.prompt:47`: "Do not hand-edit, merge, stage, or commit handoff runtime state").

---

## 4. Startup, config keys, terminal backends, and the headless question

### What `./swarm` does

`six-pack:swarm:16-27`: if `swarmforge/scripts` **or** `swarmforge/scripts/shared-articles` is missing, curl the `main` tarball (`SWARMFORGE_SCRIPTS_URL` / `SWARMFORGE_SCRIPTS_BRANCH` override it), copy `swarmforge/scripts`, stage `swarmforge/constitution/articles/*` into `swarmforge/scripts/shared-articles/`. Then `exec swarmforge/scripts/swarmforge.sh` -> `bb swarmforge.bb`.

`run-main!` (`main:swarmforge/scripts/swarmforge.bb:505-550`), in order:
1. Require `tmux`, `git`, `bb` on PATH (`:506-508`); missing -> exit 1.
2. Probe tmux `base-index` / `pane-base-index` on the project socket, spawning a throwaway `swarmforge-probe-<pid>` session if the server isn't up yet (`:87-97`). This is the fix for closed issues #4/#16 - it now uses `show-options -gwqv`, since `show-window-options -gqv` errors on modern tmux.
3. `git init` + `git branch -M master` + initial commit if there's no `.git` (`:121-127`).
4. Add `.swarmforge/` and `.worktrees/` to `.git/info/exclude` (`:115-119`).
5. Parse and validate `swarmforge.conf` (see below).
6. Require every configured agent CLI on PATH (`:292-294`).
7. Create state dirs; verify all 24 required helper scripts and all 5 terminal adapters exist **and are executable** (`:217-240`).
8. Create worktrees, create handoff dirs.
9. Kill any pre-existing `swarmforge-<role>` sessions (`:520-523`).
10. Create one tmux session per role; sync scripts into each worktree; start `handoffd`.
11. Launch agents with a `SWARMFORGE_AGENT_START_DELAY_MS` (default 1500) gap (`:535-539`).
12. Open terminal surfaces, or fall back.

### Every `swarmforge.conf` key

There is exactly **one directive**, `window` (`:159-160` rejects anything else). The grammar is positional, minimum 4 fields (`:146-148`):

```
window <role> <agent> <worktree> [task|batch] [extra-cli-args...]
```

| Position | Rules | Cite |
|---|---|---|
| `window` | the only accepted directive | `:159-160` |
| `<role>` | no underscores; must be unique; must have `swarmforge/roles/<role>.prompt` | `:161-164`, `:173-174` |
| `<agent>` | lowercased; must be one of `claude`, `codex`, `copilot`, `grok`; must be on PATH | `:169-170`, `:292-294` |
| `<worktree>` | no `/`, not `.` or `..`; unique unless `none`/`master`; `none`/`master` mean the main checkout | `:165-168`, `:175-177` |
| `[task\|batch]` | optional 5th field, default `task` | `:151-153`, `:171-172` |
| `[extra-cli-args...]` | everything after, joined with spaces, appended to the agent command | `:154-158`, `:306-308` |

Empty config -> exit 1 (`:191-192`). Blank lines and `#` comments skipped (`:144`).

There are **no other config keys anywhere**. Terminal selection, sleep inhibition, and start delay are environment variables, not config:

| Env var | Effect | Cite |
|---|---|---|
| `SWARMFORGE_TERMINAL` | overrides backend detection | `:52-54` |
| `SWARMFORGE_TERMINAL_BACKEND` | used by `swarm-cleanup.sh` / `close-swarm` | `swarm-cleanup.sh:11`, `close-swarm:71-73` |
| `SWARMFORGE_AGENT_START_DELAY_MS` | inter-agent launch delay, default 1500 | `:535` |
| `SWARMFORGE_PREVENT_SLEEP` | `"0"` disables the sleep inhibitor | `:375` |
| `SWARMFORGE_SCRIPTS_BRANCH` / `_URL` | bootstrap source | `six-pack:swarm:5-6` |

Per-role agent flags go in the conf tail. `grok` gets special handling: `--always-approve` / `--yolo` / `--permission-mode bypassPermissions` in the tail promotes it to `--permission-mode bypassPermissions`, otherwise it gets `acceptEdits` (`:310-321`).

### Every terminal backend

Five adapter files, all required to exist and be executable (`:229-230`, `:237-240`):

| Backend | Aliases | Opens? | Trackable? | Mechanism |
|---|---|---|---|---|
| `terminal-app` | `terminal`, `terminal.app` | yes | yes | AppleScript, macOS Terminal.app |
| `iterm2` | `iterm`, `iterm.app` | yes | yes | AppleScript, `com.googlecode.iterm2` |
| `ghostty` | (none - passes through the default case) | yes | yes | AppleScript, Ghostty surface configuration |
| `windows-terminal` | `windows`, `wt` | yes | **no** | `wt.exe -w new --title ... wsl.exe -e bash -lc` |
| `none` | `current`, `fallback` | **no** | no | fall back to attaching the current shell |

Aliases at `:44-50` (mirrored in zsh at `main:swarmforge/scripts/swarm-terminal-adapter.sh:5-25`). Note `ghostty` is not in the alias table; it works only because the default case returns the lowercased string unchanged.

Auto-detection (`:52-60`): `SWARMFORGE_TERMINAL` wins; else `osascript` present -> `iterm2` if `$TERM_PROGRAM == iTerm.app` else `terminal-app`; else `wt.exe` present -> `windows-terminal`; else **`none`**. On headless Linux that lands on `none` with no configuration at all.

### THE TMUX NAMING (the load-bearing answer)

Constants at `main:swarmforge/scripts/swarmforge.bb:8-9`:

```clojure
(def session-prefix "swarmforge")
(def agent-window "swarm")
```

- **Socket**: `/tmp/swarmforge-$UID/<CRC32-of-absolute-working-dir>.sock` (`:461-465`). `UID` is read via `System/getenv`, and `UID` is a bash *shell* variable that is **not exported**, so in practice it falls back to `System/getProperty "user.name"`. Verified path on this host: `/tmp/swarmforge-jacob/1087912805.sock`. The socket path is also written to `.swarmforge/tmux-socket` (`:246`), which is the reliable way to read it.
- **Session name**: `swarmforge-<role>`, role case preserved verbatim from the conf (`session-name-for-role`, `:68-69`).
- **Window name**: the *display name*, which is `str/capitalize` applied to each hyphen/underscore-split word (`display-name-for-role`, `:62-66`). Clojure's `str/capitalize` **lowercases the remainder**, so role `QA` becomes window `Qa`.
- Window creation: `new-session -d -s <session> -n swarm`, then `rename-window` to the display name, then `set-window-option allow-rename off` (`:296-299`). The literal name `swarm` exists only for a few milliseconds.
- Agent launch target: `<session>:<display-name>.<pane-base-index>` (`tmux-agent-target`, `:74-75`, used at `:356-358`).
- Daemon wake-up target: the **bare session name**, no window or pane (`handoffd.bb:95-99`).

Verified live for six-pack:

```
$ tmux -S /tmp/swarmforge-jacob/1087912805.sock list-windows -a \
    -F '#{session_name} | win #{window_index} | name=#{window_name}'
swarmforge-QA        | win 0 | name=Qa
swarmforge-architect | win 0 | name=Architect
swarmforge-cleaner   | win 0 | name=Cleaner
swarmforge-coder     | win 0 | name=Coder
swarmforge-hardender | win 0 | name=Hardender
swarmforge-specifier | win 0 | name=Specifier
```

Six sessions, one window each, one pane each. Window indices honor the user's `base-index`; here 0.

### Is there a headless tmux-only mode?

**Almost, but not a real one.** `SWARMFORGE_TERMINAL=none` (or `current` / `fallback`) selects `none.sh`, whose `terminal_backend_can_open_sessions` returns 1 (`main:swarmforge/scripts/terminal-adapters/none.sh:7-9`). `open-terminal-surfaces!` then takes the else branch (`swarmforge.bb:451-453`):

```clojure
(println (str yellow "No terminal backend found; attaching current shell to '" ... "' instead."))
(sh "tmux" "-S" (:tmux-socket ctx) "attach-session" "-t" (-> ctx :roles first :session))
```

So the sessions **are** created detached and the daemon **is** started before this line; only the final `attach-session` needs a TTY. On a headless run the attach fails harmlessly and the swarm is already up - verified: all six sessions and windows existed after the launcher exited. In effect, "headless tmux-only" is `SWARMFORGE_TERMINAL=none` plus tolerating the failed attach, and you then attach manually with the socket from `.swarmforge/tmux-socket`. There is no `detach`, `--no-attach`, or `headless` flag, and no config key for any of this.

Issue #41 (open, unmerged) proposes exactly the missing piece: a `tmux-grid` adapter selected with `SWARMFORGE_TERMINAL=tmux-grid` that builds one viewer session of panes each running `tmux attach-session -t swarmforge-<role>`. It correctly notes that `join-pane` would destroy the role sessions and hence the daemon's delivery address.

---

## 5. Known failure modes and open issues

From `gh issue list --repo unclebob/swarm-forge --state all --limit 60`, plus what I reproduced.

**Open, substantive:**

- **#34 - a single missed wake-up deadlocks the swarm permanently.** `notify!` is fire-and-forget keystroke injection whose success check is the `tmux send-keys` exit code, and `poll-once!` never looks at `inbox/new/`. Reporter's six-pack ran three hours then froze at QA for four more with the handoff sitting undelivered-to-the-agent in `inbox/new/`. The receive rules forbid polling ("If it prints `NO_TASK`, stop waiting for work"), so the agent side cannot self-heal. Workaround is typing anything into the stalled pane. Proposed fix: re-notify when an `inbox/new/` file's `enqueued_at` is older than ~60 s.
- **#32 - shared constitution articles are never installed** (reproduced here). Staged into `swarmforge/scripts/shared-articles/` but nothing copies them into `swarmforge/constitution/articles/`. Regression introduced by the shell-to-Babashka port `9afcb6f`, which dropped the installer and both call sites. Effect: agents run without `handoffs.prompt`. Two-pack fails loudly ("Send a git_handoff" reads as a command); six-pack degrades quietly because its prompts say "using the file-based handoff format".
- **#29 - `merge_and_process` reads as a shell command.** Reproduced upstream against exactly the commits I read (`main@9acd54d223`, `six-pack@59803dadb3`). Codex ran it in Bash, got `command not found`, stopped. Two downstream-fork reports (`gabadi/swarm-forge#44`, `#76`) show the softer version: agents improvise inconsistent merges, or skip the merge entirely.
- **#41 - no single surface at six roles.** Six overlapping OS windows at cascading offsets; nothing to put on a second monitor or screenshot.
- **#39 / #46** - PRs offered for OpenCode and Mistral Vibe backends, unmerged. #37's comments give a working 2-line OpenCode patch (`swarmforge.bb` lines ~170 and ~340).
- **#27** - request to integrate Beads for task tracking: "File and JSON handover works great but it's hard to track and manage tasks and bugs that way."
- **#37** - local-model usage. A commenter reports local models work but "on my M1 Pro it's too slow to run 4 of them at the same time."

**Closed, but the shape is instructive:**

- **#2** - "Coder occasionally finishes a slice without firing notify-agent.sh." Once in ~30 sub-slices. The agent believed it was done; the reviewer never learned there was work. Silent, with no anomaly in any log. The whole protocol still rests on the agent choosing to call the helper.
- **#16 / #4** - `pane-base-index` misread caused `can't find pane: 0` for anyone with `set -g pane-base-index 1`. Fixed in `49ce3d6`; SwarmForge's private tmux server still loads the user's `~/.tmux.conf`.
- **#20** - the previous logbook-based transport corrupted the receiver's state and looped infinitely. The file-queue redesign exists because of this class of bug.

**Reproduced here, not filed upstream:**

- **`systemd-inhibit` failure silently kills the handoff daemon on headless Linux.** `sleep-inhibitor-prefix` (`swarmforge.bb:374-386`) prepends `systemd-inhibit --what=sleep:idle --who=SwarmForge --why=...` whenever `systemctl is-system-running` returns `running` **or `degraded`**. On a headless box with no polkit session, `systemd-inhibit` exits 1 with `Failed to inhibit: Access denied` and never execs `handoffd.bb`. `start-handoff-daemon!` (`:388-399`) checks nothing and prints `Started handoff daemon with OS sleep prevention.` My run produced exactly that message, an empty `.swarmforge/daemon/` with no `handoffd.pid`, no daemon process, and a log containing only `Failed to inhibit: Access denied`. All six agents launch and nothing is ever delivered. Fix: `SWARMFORGE_PREVENT_SLEEP=0 ./swarm`.
- **Stale `sequence.lock` is unrecoverable.** `swarm_handoff.bb:242-251` spins forever on `fs/create-dir`. A SIGKILL between create and the `finally` leaves the directory behind and wedges that worktree's outbound path with no timeout and no diagnostic.

---

## 6. Honest assessment

**What the design assumes about agents.** Four things, all load-bearing, none enforced.

1. *That the agent reliably calls the helper at the end of the work.* The entire pipeline advances only because an agent chooses to run `swarm_handoff.sh` and then `done_with_current.sh`. Issue #2 is that assumption failing once in thirty. There is no supervisor, no timeout, no "role X has been idle with a committed dirty tree for 40 minutes" check. Compare this to the outbound path, which is validated to the character - the strictness is entirely on the *format* of the message and entirely absent on *whether a message is sent at all*.
2. *That the agent correctly interprets prose instructions that look like code.* `merge_and_process <sender> <sha>` is the load-bearing verb of the whole system and it is not implemented. Issue #29 documents an agent treating it as a command; the fork issues document agents improvising three different merge strategies for it. Uncle Bob's own doc says "The high-level merge strategy can remain flexible" - which in a five-way parallel-branch topology is a strong assumption about agent judgment on conflicts.
3. *That the agent can meaningfully hold and execute a 2-4 KB role prompt containing 15-30 distinct imperatives.* The architect prompt has 25 bullets; the hardender's handoff step specifies a four-tool sequence with fix-before-proceed semantics. Nothing verifies any of it ran. The role boundaries ("do not run mutation", "ignore the QA suite") are honor-system, and the whole value proposition of six-pack over four-pack is that those boundaries hold.
4. *That the tools named in the constitution exist.* `engineering.prompt:8-10` names `mutate4go`/`crap4go`/`dry4go`, `clj-mutate`/`crap4clj`/`dry4clj`, `mutate4java`/`crap4java`/`dry4java` - Go, Clojure, and Java only. There is no row for Python, TypeScript, Rust, or anything else. The cleaner's "reduce CRAP to 6 or below" is unactionable outside those three languages, and the agent will improvise a substitute or silently skip the gate.

**What it assumes about task shape.** The topology is a strict linear pipeline with a single terminal broadcast. That fits precisely one shape of work: *one feature at a time, specified up front in Gherkin, decomposed into behavior slices that a single coder finishes before anyone else touches anything.* It does not fit:

- **Parallel features.** Nothing serializes the specifier. Two features in flight means two independent commit chains converging on five branches with hand-merges and no conflict policy.
- **Exploratory or ill-specified work.** The specifier must produce deterministic Gherkin before the coder starts. Anything where you learn the requirement by building is outside the model.
- **Bug fixes.** There is no path for one. The only entry point is "specifier writes a feature spec and asks the user for approval."
- **Long-tail latency.** Six sequential agent stages per feature, with the hardender running per-file mutation testing. Issue #34's reporter measured three hours for five stages on one feature. The economics only work if the quality delta from six specialists beats one agent doing all six things at ~1/6 the wall time and ~1/6 the token spend. Nothing in the repo measures that.
- **Any batch bigger than one.** `ready_for_next_batch.bb:134-136` sweeps all *equal-priority* queued items into one batch and hands the agent N tasks in one context. At N=5 that is five merges and five review passes in a single context window with no per-item completion, and `done_with_current_batch.bb` completes them atomically - partial batch failure has no representation.

**Where it actually breaks.** In descending order of likelihood:

1. **Wake-up loss** (#34). At-most-once notification into a TUI composer, no reconciliation loop, receive rules that forbid polling. This is a structural at-most-once transport pretending to be at-least-once, and the failure is a silent permanent deadlock with every process healthy.
2. **Missing constitution** (#32). Shipped broken today: the file that defines the protocol is staged and never installed.
3. **Merge semantics.** Five long-lived divergent branches, hand-merged by LLMs, with the constitution forbidding them from looking at any branch not named in a handoff. It works because the pipeline is serial; the moment it isn't, there is no mechanism.
4. **Silent gate skipping.** Nothing checks that the cleaner actually got CRAP under 6, that the hardender actually killed the survivors, or that QA actually ran the suite through the UI. The observability story is "watch six terminal windows."
5. **No process supervision.** No liveness check, no stall detection, no restart, no metrics. `swarm-window-watchdog.bb` watches *terminal windows*, not agents, and is disabled for the exact backends a headless run would use.

**What is genuinely good, to be fair.** The outbound validation gate is excellent - strict, exhaustive, with repair-oriented error messages, and it makes the message format un-improvisable. Making the filesystem the queue with location-as-state and audit timestamps in headers is the right call for something that must survive agent crashes. Worktree-per-role is correct isolation. The batch/task receive-mode distinction is a real insight about which roles benefit from seeing several units at once. And the honesty of `handoff-protocol.md:492-494` - "Tmux wake-ups are intentionally lossy" - is the right instinct; the bug is that nothing else compensates for the looseness it deliberately accepted.

**Bottom line.** Six-pack is a well-specified *protocol* around an unspecified *actor*. The parts that are code (validation, queueing, worktrees) are careful. The parts that carry the actual engineering value (merge, cleanup thresholds, mutation hardening, UI-only QA) are English prose handed to a language model with no verification that any of it happened. It is worth studying for the file-queue design and the role decomposition. Treat the quality claims as unmeasured.

---

## 7. No license: what that means

Confirmed absent, not merely unnoticed:
- `git ls-tree -r --name-only <branch> | grep -i licen` returns nothing on `main`, `six-pack`, `four-pack`, `two-pack`, `squad`, `adversaries`.
- `git log --all --diff-filter=A -- 'LICENSE*' 'COPYING*'` returns nothing - no license file has ever existed in the history.
- `gh api repos/unclebob/swarm-forge --jq .license` returns `null`.
- The README contains no license or grant-of-rights section.

**Default legal position.** Under the Berne Convention and 17 U.S.C. §102, the work is copyrighted the moment it is fixed; publishing it publicly grants nothing. With no license, **all rights are reserved**. GitHub's own Terms of Service (section D.5, "License Grant to Other Users") grant exactly two things to everyone who can see a public repo: the right to **view** it, and the right to **fork** it within GitHub. Nothing else. GitHub's `choosealicense.com/no-permission/` states it directly: no one may reproduce, distribute, or create derivative works.

Concretely:

- **Reuse in your own project: not permitted.** Copying `handoffd.bb`, the helper scripts, the role prompts, or the adapter contract into your codebase is reproduction and derivative-work creation, neither of which is granted. This holds whether or not you keep attribution, and whether the project is internal, commercial, or open source.
- **Vendoring: not permitted, and the design makes it worse.** `six-pack:swarm:16-27` downloads the `main` tarball at runtime rather than vendoring it. Running that is fine (GitHub grants you a view). Committing the result into your repo redistributes it. The 231 forks are covered by the GitHub-internal forking right; taking a fork's contents outside GitHub is not.
- **Blog post quoting: almost certainly fine, but stay inside fair use.** Short excerpts for commentary, criticism, review, and teaching are squarely the §107 use case: purpose is transformative and educational, the amounts are small, and quoting a role prompt to critique it does not substitute for the original. Practical guardrails: quote lines and short blocks, never whole files; cite branch, path, and line; keep your analysis longer than the quoted material; do not reproduce all six role prompts in full, since collectively that *is* the artifact.
- **Describing it is entirely unrestricted.** Copyright covers expression, not ideas. The topology, the six-role decomposition, the file-queue protocol, the header lifecycle, the batch/task distinction, and the `swarmforge-<role>` naming are facts about a system. You can write them up, diagram them, and implement the same architecture independently without touching the license question at all. If you want the pattern in your own tooling, reimplement from the description rather than from the source.
- **PRs are in an odd spot.** GitHub ToS D.6 says contributions are offered under the repo's license terms - and there aren't any. Contributors #39/#46 have open PRs regardless.
- **This can change without notice.** The owner can add a license at any time, and it would apply going forward. Re-check `gh api repos/unclebob/swarm-forge --jq .license` before publishing anything that depends on the status.

I am not a lawyer; this is a reading of the repo state and the applicable published terms, not legal advice.

---

## 8. Headless Linux blockers (Ubuntu, tmux 3.4)

Ranked by severity. Note: on *this* host both `zsh` (5.9) and `bb` (1.13.219) turned out to be installed, contrary to the task premise, which is how I was able to run the swarm end to end. The zsh dependencies below are real and would block a host without it.

**Hard blockers**

1. **`&!` in the cleanup command breaks under bash - verified fatal.** `swarmforge.bb:343-349` appends, for role index 0 only, a background-and-disown suffix using zsh's `&!` operator. That string is delivered with `tmux send-keys` into the pane's shell, which is tmux's `default-shell`, i.e. `$SHELL`. On this host `$SHELL=/bin/bash`, and the pane produced:

   ```
   -bash: !: event not found
   ```

   Bash history expansion rejects the entire command line, so **the specifier - the first role, the cleanup owner, and the entry point of the whole chain - never launches its agent at all.** The other five roles launched fine, because the suffix is only appended at index 0. Fix: set `tmux set -g default-shell /usr/bin/zsh` before starting, or patch `&!` to `& disown` / `&`.

2. **`zsh` is required unconditionally, even with `SWARMFORGE_TERMINAL=none`.** Two places hardcode it: `swarmforge.bb:409` returns `["zsh" "-c" script]` for every adapter call, and `swarm-window-watchdog.bb:45` does the same. Every `swarmforge/scripts/*.sh` is `#!/usr/bin/env zsh`, including `swarm_handoff.sh`, `ready_for_next.sh`, and `done_with_current.sh` - the three scripts agents call constantly. `swarm-cleanup.sh:30` uses the zsh numeric glob `[[ "$daemon_pid" == <-> ]]` and `swarm-terminal-adapter.sh:6` uses zsh's `${1:l}` lowercasing; both are bash syntax errors. `zsh` is listed as a prerequisite in the README but is not checked by `check-dependency!` (`:506-508` checks only tmux, git, bb).

3. **`systemd-inhibit` failure silently kills the handoff daemon - verified.** See §5. `SWARMFORGE_PREVENT_SLEEP=0` is mandatory on a headless box. Without it the swarm looks healthy and delivers nothing.

**Not blockers, contrary to expectation**

4. **macOS-only code is properly isolated.** All AppleScript lives in `terminal-adapters/{terminal-app,iterm2,ghostty}.sh`, which are only sourced when that backend is selected. Detection (`:52-60`) falls through to `none` when `osascript` and `wt.exe` are both absent, which is exactly the headless Linux case. The five adapter files must merely *exist and be executable* (`:237-240`) - they are never sourced on Linux.

5. **The failed `attach-session` is harmless.** It is the last statement of `run-main!` (`:453`); sessions, worktrees, script sync, and the daemon are all up before it runs.

**Operational gotcha**

6. **Codex prompts for directory trust on first run in each worktree.** Captured from the `swarmforge-QA` pane: `Do you trust the contents of this directory? / 1. Yes, continue / 2. No, quit`. Six worktrees means six interactive prompts that no automation answers. Pre-trust each worktree or configure the CLI's trust store before launching unattended.

**Minimum install for this host**

```sh
sudo apt-get install -y zsh tmux git curl        # tmux 3.4 is fine
# babashka:
bash < <(curl -s https://raw.githubusercontent.com/babashka/babashka/master/install)
# at least one agent CLI on PATH: codex | claude | copilot | grok

BRANCH=six-pack
curl -L "https://github.com/unclebob/swarm-forge/archive/refs/heads/${BRANCH}.tar.gz" \
  | tar -xz --strip-components=1

# REQUIRED on headless Linux, or handoffd dies silently:
export SWARMFORGE_PREVENT_SLEEP=0
export SWARMFORGE_TERMINAL=none

# REQUIRED, or the specifier never starts (the `&!` bug):
tmux set -g default-shell /usr/bin/zsh    # or patch swarmforge.bb:349

# RECOMMENDED, or agents run without the handoff protocol (issue #32):
cp -n swarmforge/scripts/shared-articles/*.prompt swarmforge/constitution/articles/

./swarm     # attach fails harmlessly; sessions are already up

# then attach:
tmux -S "$(cat .swarmforge/tmux-socket)" attach -t swarmforge-specifier
```

**Naming reference for downstream tooling**

- socket: read `.swarmforge/tmux-socket`; format `/tmp/swarmforge-<user>/<crc32-of-abs-project-path>.sock`
- sessions: `swarmforge-specifier`, `swarmforge-coder`, `swarmforge-cleaner`, `swarmforge-architect`, `swarmforge-hardender`, `swarmforge-QA` (role case preserved)
- windows: `Specifier`, `Coder`, `Cleaner`, `Architect`, `Hardender`, `Qa` (capitalize-first, lowercase-rest - `QA` becomes `Qa`)
- one window and one pane per session; window index follows the user's tmux `base-index`
- role/session/worktree mapping is machine-readable in `.swarmforge/roles.tsv`, tab-separated:
  `role \t worktree-name \t worktree-path \t session \t display-name \t agent \t receive-mode`
