"""The Codex composer: a launch's values in, its argv and stamped environment out.

The mode follows from the target and the links:

| | in this terminal or a slot | headless |
|---|---|---|
| turns absent | `interactive` (`codex`) | `exec` (`codex exec`) |
| turns present | `host` (turns' Codex host) | `exec` |

Until turns' host entry is installed, a launch with turns runs as one without it
(`interactive`, the brief as the opening prompt) and says so in one warning line. A launch
with a comms identity runs comms' Codex wrapper, which takes the host's flags; until comms
takes per-launch settings there, it is refused with that reason, and a headless one is
refused until comms takes its headless flags.

    interactive  codex [<opening prompt>] -c check_for_update_on_startup=false -C <cwd>
                   [-m M] -s <S> -a <A> [other rows] [extra flags]
    exec         codex exec <prompt> -c check_for_update_on_startup=false -C <cwd>
                   [-m M] -s <S> [--skip-git-repo-check] [other rows] [extra flags]
    host         moxi-turns-codex-host --cwd <cwd> [--model M] --sandbox <S>
                   [--option ...] [extra flags]
    host, comms  moxi comms codex --name N [--scope S] [--director D ...] [--model M]
                   --sandbox <S> [--option ...] [extra flags]

The host runs with its window, and applies turns' launch additions itself; the launcher
passes none. A Codex session under the host gets its identity block and instructions from
the launcher's `start` listener, never on the argv. Without turns, or in `exec` mode, it
gets neither, and a seat with instructions says so in one warning line; the posture line
then goes on the opening prompt's second line.

Extra flags in `host` mode go to the host's command line as given. The host's
`--permissions PROFILE` is not among them: it sets the sandbox, as the posture row
`sandbox` does, so passed on it is refused like that row, until a permission profile has
a row of its own.
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
                         "wrapper to take per-launch settings, which this comms does not "
                         "yet", "leave the identity out")
        if "turns" not in ctx.modules or not ctx.host:
            raise NotYet("identity", "a Codex launch with an identity runs under turns' "
                         "Codex host, which is not installed",
                         "install moxi-turns with its codex extra, or leave the identity "
                         "out")
        return "host", ()
    if "turns" in ctx.modules:
        if ctx.host:
            return "host", ()
        return "interactive", ("turns' Codex host is not installed, so this Codex launch "
                               "runs without turns: its brief is the opening prompt and "
                               "the session is not reached by turns",)
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
    catalog.refuse_passthrough(HARNESS, extra)
    fit = catalog.fit(HARNESS, mode_, {**values, **owned}, ctx.chosen)
    not_applied = fit.not_applied

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
        argv += flags + extra
    elif host:
        argv = [str(ctx.host)] + flags + extra
    else:
        argv = [HARNESS] + (["exec"] if mode_ == "exec" else []) + (
            [prompt] if prompt is not None else []) + flags + extra

    env = common.stamps(ctx.request_id, turns="turns" in ctx.modules,
                        warn_at=str(values.get("warn_at") or "") or None,
                        notes_dir=str(values.get("notes_dir") or "") or None)
    recorded = common.recorded(HARNESS, values, mode_, not_applied)
    common.check_sizes(argv, values=recorded, brief=ctx.brief, env=env, prompt=prompt,
                       windows=ctx.windows)
    return Composition(harness=HARNESS, mode=mode_,
                       target=str(values.get("target") or "terminal"), argv=tuple(argv),
                       env=env, values=recorded, prompt=prompt, first=first,
                       not_applied=not_applied, warnings=tuple(warnings))


__all__ = ["HARNESS", "HOST_ORDER", "ORDER", "comms_identity", "compose", "mode"]
