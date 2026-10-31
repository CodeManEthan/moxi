"""The Codex composer: a launch's values in, its argv and stamped environment out.

The mode follows from the target and the links:

| | in this terminal or a slot | headless |
|---|---|---|
| turns absent | `interactive` (`codex`) | `exec` (`codex exec`) |
| turns present | `host` (turns' Codex host) | `exec` |

Until turns' host can start (its entry installed, with turns' `codex` extra), a launch
with turns runs as one without it (`interactive`, the brief as the opening prompt) and
says so in one warning line. A launch with a comms identity runs comms' Codex wrapper,
which takes the host's flags and execs the host, so it needs the host too; a headless one
is refused until comms takes its headless flags.

    interactive  codex [<opening prompt>] -c check_for_update_on_startup=false -C <cwd>
                   [-m M] -s <S> -a <A> [other rows] [extra flags]
    exec         codex exec <prompt> -c check_for_update_on_startup=false -C <cwd>
                   [-m M] -s <S> [--skip-git-repo-check] [other rows] [extra flags]
    host         moxi-turns-codex-host --cwd <cwd> [--model M] --sandbox <S>
                   [--option ...] [extra flags]
    host, comms  moxi comms codex --name N [--scope S] [--director D ...] [--model M]
                   --sandbox <S> [--option ...]

The host runs with its window, and applies turns' launch additions itself; the launcher
passes none. A Codex session under the host gets its identity block and instructions from
the launcher's `start` listener, never on the argv. Without turns, or in `exec` mode, it
gets neither, and a seat with instructions says so in one warning line; the posture line
then goes on the opening prompt's second line.

In `host` mode `add_dir` and `trust_cwd` are `--option` items whose key is a TOML dotted
key: `projects."<cwd>".trust_level` and `sandbox_workspace_write.writable_roots`. The
host reads the key as TOML does, so the quoted directory stays one part, dots and all,
and its quotes come off. Plain `codex -c` splits a key at every dot and keeps the quotes,
which is why the other modes trust the directory with an inline table instead.

comms' wrapper takes its own grammar and nothing past it, so a launch with an identity
passes no extra flags on: it is refused when it has any (`catalog.WrapperFlags`). The
wrapper builds the host's `--cwd` from its own directory, so it gets none and is started
in the launch's directory.

Extra flags go to the command line as given, in every other mode, except a flag or config
override that sets what a posture row sets, which is refused like that row so the posture
check sees every posture: the host's `--permissions PROFILE` (until a permission profile
has a row of its own), Codex's `--dangerously-bypass-approvals-and-sandbox` and
`--approve-for-me` with their hidden aliases `--yolo` and `--not-so-yolo`, and a config override keyed `sandbox_mode` or `approval_policy`,
passed on (`-c`, `--config`, `--option`) or as a `config` item (`catalog.POSTURE_FLAGS`,
`catalog.POSTURE_KEYS`). The host's own `--resume`, `--no-window`, `--options-file` and
`--instructions-file` are refused too (`catalog.HELD_FLAGS`).
"""

from __future__ import annotations

from collections.abc import Mapping

from .. import catalog
from . import common
from .common import Composition, Context, NoBrief, NotYet

HARNESS = "codex"
#: The rows in the order they are composed, per mode.
ORDER = ("update_check", "cwd", "model", "sandbox", "approval", "skip_git_check",
         "profile", "search", "add_dir", "config", "trust_cwd")
HOST_ORDER = ("cwd", "model", "sandbox", "update_check", "add_dir", "config", "trust_cwd")


def comms_identity(values: Mapping[str, object], ctx: Context) -> str | None:
    """The launch's comms name, when it has one and comms is installed."""
    identity = values.get("identity")
    return str(identity) if identity and "comms" in ctx.modules else None


def mode(values: Mapping[str, object], ctx: Context) -> tuple[str, tuple[str, ...]]:
    """The launch's mode, and the warning that goes with it, if any; the resolver's mode
    when the context carries it (the resolver gives its own warnings)."""
    if ctx.mode is not None:
        if ctx.mode not in catalog.MODES[HARNESS]:
            raise ValueError(f"codex has no mode {ctx.mode!r}")
        if ctx.mode == "host" and not ctx.host:
            raise ValueError("a Codex launch in host mode needs turns' Codex host")
        return ctx.mode, ()
    identity = comms_identity(values, ctx)
    if (values.get("target") or "terminal") == "headless":
        if identity and not ctx.comms_headless:
            raise NotYet("identity", "a headless Codex launch with an identity needs "
                         "comms' headless wrapper flags, which this comms does not take "
                         "yet", "leave the identity out, or launch in this terminal or a "
                         "slot")
        return "exec", ()
    if identity:
        if not ctx.comms_codex:
            raise NotYet("identity", "a Codex launch with an identity needs comms' Codex "
                         "wrapper (moxi comms codex), which this comms does not offer",
                         "install moxi-comms 0.2, or leave the identity out")
        if "turns" not in ctx.modules or not ctx.host:
            raise NotYet("identity", "a Codex launch with an identity runs under turns' "
                         "Codex host, which can't start here",
                         "install moxi-turns with its codex extra, or leave the identity "
                         "out")
        return "host", ()
    if "turns" in ctx.modules:
        if ctx.host:
            return "host", ()
        return "interactive", ("turns' Codex host can't start here (it is not "
                               "installed, or turns lacks its codex extra), so this Codex "
                               "launch runs without turns: its brief is the opening "
                               "prompt and the session is not reached by turns",)
    return "interactive", ()


def compose(values: Mapping[str, object], ctx: Context) -> Composition:
    """Compose one Codex launch. Pure: reads only `values` and `ctx`."""
    values = common.with_defaults(HARNESS, values)
    mode_, warned = mode(values, ctx)
    warnings = list(warned)
    identity = comms_identity(values, ctx)
    if values.get("identity") and not identity:
        warnings.append(f"identity {values['identity']} is ignored: comms is not installed")
    scope = (str(values.get("scope") or "") or None) if identity else None
    cwd = common.cwd_of(values)
    host = mode_ == "host"

    posture = str(values.get("posture") or "") or None
    text = common.first_turn(ctx.actor, brief=ctx.brief,
                             handoff=values.get("handoff") or None,
                             handoff_text=ctx.handoff_text,
                             posture=None if host else posture)
    prompt, first = (None, text) if host else (text, None)
    if mode_ == "exec" and prompt is None:
        raise NoBrief("brief", "a headless launch needs a brief or a handoff to run",
                      "give it --brief, --brief-file or --handoff")
    if not host and ctx.instructions and ctx.instructions.strip():
        warnings.append("this Codex launch can't carry the seat's instructions: a Codex "
                        "seat gets them from turns' Codex host")

    owned = {"cwd": cwd, "update_check": True,
             "skip_git_check": bool(values.get("scratch")) and mode_ == "exec"}
    extra = [str(token) for token in values.get(catalog.EXTRA) or ()]
    catalog.refuse_wrapper_flags(HARNESS, identity, extra)
    catalog.refuse_passthrough(HARNESS, extra, mode_)
    fit = catalog.fit(HARNESS, mode_, {**values, **owned}, ctx.chosen)
    not_applied = fit.not_applied
    catalog.refuse_posture_keys(HARNESS, fit.applied.get("config") or (), mode_)

    rows = catalog.load(HARNESS).rows
    flags: list[str] = []
    for name in HOST_ORDER if host else ORDER:
        if name in fit.applied and not (identity and name == "cwd"):
            flags += rows[name].tokens(mode_, fit.applied[name], cwd=cwd)

    if identity:
        argv = [ctx.moxi, "comms", HARNESS, "--name", identity]
        if scope:
            argv += ["--scope", scope]
        for director in values.get("director") or ():
            argv += ["--director", str(director)]
        argv += flags
    elif host:
        argv = [str(ctx.host)] + flags + extra
    else:
        argv = [HARNESS] + (["exec"] if mode_ == "exec" else []) + (
            [prompt] if prompt is not None else []) + flags + extra

    env = common.stamps(ctx.request_id, turns="turns" in ctx.modules,
                        warn_at=str(values.get("warn_at") or "") or None,
                        notes_dir=str(values.get("notes_dir") or "") or None,
                        granted=common.commands(values),
                        options=common.option_stamps(values))
    recorded = common.recorded(HARNESS, values, mode_, not_applied)
    common.check_sizes(argv, values=recorded, brief=ctx.brief, env=env, prompt=prompt,
                       windows=ctx.windows)
    return Composition(harness=HARNESS, mode=mode_,
                       target=str(values.get("target") or "terminal"), argv=tuple(argv),
                       env=env, values=recorded, prompt=prompt, first=first,
                       brief=ctx.brief,
                       not_applied=not_applied, warnings=tuple(warnings))


__all__ = ["HARNESS", "HOST_ORDER", "ORDER", "comms_identity", "compose", "mode"]
