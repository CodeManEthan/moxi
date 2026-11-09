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

### Marking an exception

A line the naming check refuses on purpose carries `# noqa: <code> -- <why>`. In a
`settings.toml` the mark goes on the key's `[table]` line. The codes are the naming
check's own: `MX1` units and `MX4` verbs and switch keys. The check refuses a bare
`# noqa`, and a mark for one of these codes without its `-- <why>`. A console script's
name is held by contract check 7, which takes no mark.
