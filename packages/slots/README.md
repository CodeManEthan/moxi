# moxi-slots

Moxi's slots module keeps numbered tmux sessions, called slots, on one tmux server of its
own. You attach a terminal to a slot once, and sessions come and go in it while your
attachment stays. Other code, Moxi's launcher above all, starts a command in a free slot
and gets back whether it started. slots is a module of
[Moxi](https://github.com/CodeManEthan/moxi), a harness for running many AI coding-agent
sessions as one system.

## What it needs

- Linux, or WSL2 (which is Linux). slots also has macOS code (launchd supervises the
  server there), which the repository's CI tests without gating and nobody runs yet:
  treat macOS as untested. On native Windows the package installs, but every command
  says where slots runs and exits 3, no daemon is installed, and a launcher treats slots
  as absent.
- Python 3.12 or later, and [uv](https://docs.astral.sh/uv/).
- tmux 3.2 or later. `moxi install slots` looks `tmux` up on your PATH and records its
  absolute path, which every slots process then uses.
- A supervisor for the server: the systemd user manager on Linux, launchd on macOS. Where
  there is none (WSL2 without systemd, a container), the server starts on first use and
  nothing restarts it; on WSL2 it dies with the WSL VM. `moxi doctor slots` says which.

## Install

```sh
uv tool install moxi
moxi install slots
```

The first line installs `moxi`, the core. The repository lists the other modules.

## What it does outside its own folders

- It installs a user unit that runs its tmux server (`moxi-slots-server`). The server
  reads your usual tmux config, or the file in the `tmux_conf` setting, so your bindings
  work when you attach. After that it sets `exit-empty` and `exit-unattached` off and the
  global `destroy-unattached` off, since slots rely on them.
- On systemd, with linger off, the server and every slot stop at your last logout. The
  doctor warns and names `loginctl enable-linger`.
- It presses keys in Claude Code, on three screens only: the folder trust warning, the
  development channels warning, and the exit dialog shown while background shells or
  monitors run. `answer_screen` presses Up, Down and Enter there, and only after every
  phrase, the option row and the marker have matched on settled reads. It sends no other
  key or text into any pane. The screens are matched by their text, not by a version
  number; the test fixtures cover Claude Code 2.1.289 to 2.1.314. A screen that doesn't
  match is saved in slots' state directory (the last 20) and nothing is pressed.
- Uninstalling stops the server, which ends every session in every slot. `moxi uninstall
  slots` refuses while any slot is occupied or not idle, or a job runs.

## Slots

A slot is tmux session `slot-<n>`, n from 1 to 999, with an optional name. Its first
window is a shell of its own; a launch runs in one run window. A window you open yourself
is yours, and slots ignores it. A slot is `free` (no run window), `occupied` (the run
pane is live), `ended` (the run pane is dead and its last screen stays), or `missing`
(registered, but the session is gone, as after a reboot).

A **hold** reserves a slot for a holder, a word you choose, for up to a day. A held slot
takes a launch only from its holder, and a start by the holder ends the hold.

A **job** is a command that outlives its caller. It runs in its own window of session
`moxi-jobs`, and the window goes when the job ends.

## Commands

| command | what it does |
|---|---|
| `moxi slots list [--json]` | every slot: state, hold, label, attached clients, last launch, and drift between the registry and tmux |
| `moxi slots show N\|NAME [--json]` | one slot, with its run pane, its command and its exit status |
| `moxi slots create [N ...] [--count K] [--name NAME]` | makes the slots named, or the K lowest free numbers |
| `moxi slots remove N\|NAME ... [--force] [--holder H]` | kills idle slots. `--force` takes a slot that isn't idle, never an occupied or held one |
| `moxi slots remove --all-free` | removes every free or ended slot that is idle and unheld, and lists the ones skipped |
| `moxi slots rename N\|NAME NAME`, `... --clear` | names a slot, or clears its name |
| `moxi slots attach N\|NAME [--print]` | attaches this terminal, making the slot when it has no session. `--print` prints the tmux command instead |
| `moxi slots hold N\|NAME --holder H --for S` | holds a slot for S seconds, at most a day |
| `moxi slots release N\|NAME --holder H` | ends your hold |
| `moxi slots jobs [--json]` | the detached jobs |
| `moxi slots screen N\|NAME [--json]` | which known screen the slot's run pane shows, and why. It sends nothing |

Exit codes: 0 done; 1 error (tmux failed, the registry can't be read); 2 usage; 3 refused
(occupied, held, in use, missing, name taken, a system slots doesn't run on), with the
reason word and the command that fixes it; 75 the lock wasn't had in 10 s, or the server
isn't answering yet.

## Settings

`moxi config slots` shows them.

| key | default | what |
|---|---|---|
| `tmux` | `tmux` | the tmux binary. `moxi install slots` writes the absolute path it found here; an absolute path you set is kept |
| `tmux_conf` | empty | a tmux config file for the slots server. Empty reads your usual one |

## The Python API

`moxi_slots.api`, which another module reaches through
`moxi.modules.load("slots", interface=1).api`:

- Reads, which take no lock: `list_slots()` (never raises; `Listing.ok` says whether it
  could read), `show`, `job`, `jobs`, and `supported()`, which says whether slots runs
  here.
- Changes, each under the module lock: `create`, `remove`, `rename`, `hold`, `release`,
  and `attach_argv` for the attach command.
- `start(slot, argv, env=, cwd=, label=, ...)` and `respawn` place a launch in a slot,
  and `run_job(name, argv, env=, cwd=)` runs a detached one. The environment goes through
  a private file the pane's first command reads, never through tmux. Each returns once the
  command has started or hasn't. `wait_ended` waits for a run pane to end.
- `read_screen(slot, screens)` says which of `CLAUDE_TRUST_FOLDER`, `CLAUDE_DEV_CHANNELS`
  and `CLAUDE_EXIT_DIALOG` the run pane shows. `answer_screen(slot, screen, wait)`
  answers one, and is the only call that sends a key. `pane_use` reads whether a person
  is at the pane.
- Errors are `SlotsError`s: `Refused` (with a `reason` word from `REASONS`), `TryAgain`,
  `TmuxError`, `RegistryError` and `UsageError`.

Every call reaches the server through the socket under `MOXI_HOME`, so a caller gets a
server of its own by setting `MOXI_HOME`.

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/main/packages/slots/LICENSE).
