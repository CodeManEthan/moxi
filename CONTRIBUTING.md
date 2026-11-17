# Contributing to Moxi

### Comments

1. A comment or docstring says what the code does now. It never says what the code will
   do, should do, or used to do. A plan goes in an issue; history goes in the commit.
   Naming an older form the code still accepts is fine when the comment says what the
   code does with it.
2. When you change what code does, change every comment that describes it, in the same
   commit. Search the comments for the names, flags and files you renamed or removed.
3. A name or a source file (`.py`, `.ts`, `.mjs`) a comment cites in backticks must
   exist, and so must any `--flag` a comment names. `moxi-dev check` refuses one that
   doesn't. A name from outside this repository (a Windows API, a Codex function) goes
   on a line of `.refs-allow`.
4. A list that says it is complete ("every check", "in this order") is kept complete.

### Names anyone can see: config keys, flags, verbs, scripts, parameters, tools and output

1. A number that is a duration or a size carries its unit at the end of its name:
   `_ms`, `_s`, `_minutes`, `_hours`, `_days`, `_mib`, `_chars`, `_tokens`. A flag
   spells it the same way: `--wait-s`. A name that is only the unit (`--days`) is fine.
   A duration written as text with its unit inside it (`24h`, `15m`) needs no suffix.
   A config key's `doc` line names its unit.
2. One verb per act, across every module's CLI: `remove` takes a thing away, `revoke`
   takes back a grant, `enable` and `disable` turn a thing on and off, `list` shows every
   thing of a kind, `show` shows one, `status` says how a module's own parts are running,
   `doctor` looks for problems. Don't use `delete`, `rm`, `on`, `off`, `state` or `ls` as
   verbs.
3. A daemon's or timer's console script is `moxi-<module>-<key>`, `<key>` being its name
   in `DAEMONS` or `TIMERS`.
4. No config key is named `on`, `off`, `auto`, `active`, `enable`, `disable` or
   `disabled`. Say what the key turns on (`snapshot.session_end`), or, for the switch of
   a whole part, name it `enabled` (`ceiling.enabled`). A key named `enabled` is a bool. A
   key's last name keeps one type in every module.
5. A public parameter (one a module's API exports, or one of the core's public modules)
   follows rule 1 too: `timeout_s`, not `timeout`. A name for a point in time
   (`deadline`, `since`) holds a time, never an amount: a `deadline=30.0` that means
   seconds is `timeout_s=30.0`.
6. MCP tool names, inputs and results, `--json` fields and repo spec file keys follow
   rules 1 and 2 too: `wait_s`, `age_s`, a `remove` tool. A spec file key spells its unit
   as a flag does: `push-backoff-s`. No check reads these; a reviewer does.

### Names in code

1. A bool parameter of a function or method you write is keyword-only. A call never
   passes a bare `True` or `False` by position, to any callable, a dataclass included:
   write `start(name, replace=False)`, `Acted(module, name, "start", ok=False)`.
2. One word per concept:
   - the environment mapping is `env`, never `environ` or `environment` (`os.environ`
     itself is fine);
   - a caught exception is `exc`; a handler inside another names its own `<what>_exc`;
   - a directory is `<what>_dir` (`cards_dir`, `state_dir`), never `folder` or
     `directory`; bare `dir` is a builtin, so say which directory;
   - a database connection is `conn` or `<what>_conn`;
   - an open file in `with` is `fh` or `<what>_fh`;
   - a callback that prints a line (`print`, or a stand-in for it) is `say`;
   - a parameter that says who did an act is `by`, never `actor`. A stored field keeps
     its column's name: a message's `author`, a launch row's `actor`.
3. A constant that holds an environment variable's name ends in `_ENV`. Each variable
   has one such constant, in the package that owns it: the module its `MOXI_<MODULE>_`
   prefix names, `moxi-dev` for `MOXI_TEST_`, and the core for the rest. Other code
   imports it from the owner's API, or reads it there through `moxi.modules.load` when
   the owner is optional. A module's announcement (`module.py`) and tests write the
   name itself.
4. Follow PEP 8 naming, and don't shadow a builtin (`id`, `input`, `type`, `help`). A
   name an outside protocol fixes (`visit_FunctionDef`, `do_GET`, a Win32 struct) keeps
   it, with a `noqa` and its reason.

### Names a reviewer checks

1. A name means one thing across the repository. `at` is a time; a position is
   `position`. `now` is a time. `home` is the account's home folder; Moxi's is
   `moxi_home`. `env` is a mapping. `record` is a stored row. `session` is a session id
   when it is a `str`; name the object `session_obj` or by its type.
2. A name that sounds like a read never writes. `check`, `find`, `show`, `view`, `get_`,
   `is_` and an adjective (`stale`) change nothing. A function that changes something
   says what: `revoke_ended`, `mark_stale`.
3. An event callback is `on_<event>` (`on_refused`, `on_delivered`). A bare participle
   (`refused`) is a value or a flag, never a callback.
4. A bool reads as yes or no: a verb (`replace`, `force`), a state (`stale`, `ok`) or a
   question (`is_open`, `has_page`), never a bare noun (`page`, `notes`, `timer`,
   `flag`, `value`).

### Marking an exception

A line a check refuses on purpose carries `# noqa: <code> -- <why>`. In a
`settings.toml` the mark goes on the key's `[table]` line. The codes are ruff's (`N`,
`A`, `FBT`) and the naming check's own: `MX1` units, `MX2` environment constants, `MX3`
one word per concept, `MX4` verbs and switch keys. The check refuses a bare `# noqa`,
and a mark for one of these codes without its `-- <why>`. A console script's name is
held by contract check 7, which takes no mark.
