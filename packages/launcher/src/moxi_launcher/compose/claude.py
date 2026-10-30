"""The Claude Code composer: a launch's values in, its argv and stamped environment out.

The mode follows from the target: `interactive` (`claude`) in this terminal or a slot,
`print` (`claude -p`) headless. A headless launch with a comms identity needs comms'
headless wrapper flags, and is refused until comms takes them.

Without comms (`print` adds `-p` after `claude`):

    claude [<opening prompt>] --session-id <U> --append-system-prompt-file <F>
      [--agent R] [--model M] [--effort E] [--tools T] [--allowedTools ...]
      [--disallowedTools ...] [--plugin-dir D ...] [<other launch additions>]
      [--mcp-config ...] [--add-dir ...] [-n NAME] [--remote-control]
      --permission-mode <P> [extra flags]

With comms and an identity, comms' wrapper runs claude, with the claude argv after `--`:

    moxi comms claude --name N [--scope S] [--director D ...] --session-id <U>
      [--native-name L] -- [<opening prompt>] --append-system-prompt-file <F> ...

The claude argv then carries neither `-n` (the native name goes to `--native-name`) nor
turns' launch additions, which the wrapper asks for itself.

**No positional word follows a flag.** The opening prompt comes right after the command
(and `-p`), before every flag, and begins with its header line, so no flag placed later
can take it as a value. `--remote-control`, whose value is optional, goes out as
`--remote-control=<name>` and is followed by `--permission-mode`, so no bare token ever
follows it.
"""

from __future__ import annotations

from collections.abc import Mapping

from .. import catalog
from . import common
from .common import Composition, Context, NoBrief, NotYet, PromptFile

HARNESS = "claude"
#: Where the launch additions go among the rows.
ADDITIONS = "<launch additions>"
#: The rows in the order they are composed. `resume` is not offered by part 1.
ORDER = ("session_id", "append_system_prompt_file", "agent", "model", "effort", "tools",
         "allowed_tools", "disallowed_tools", "plugin_dir", ADDITIONS, "mcp_config",
         "add_dir", "native_name", "remote_control", "permission_mode")
#: Rows the comms wrapper sets itself: they leave the claude argv.
WRAPPER_ROWS = frozenset({"session_id", "native_name"})


def comms_identity(values: Mapping[str, object], ctx: Context) -> str | None:
    """The launch's comms name, when it has one and comms is installed."""
    identity = values.get("identity")
    return str(identity) if identity and "comms" in ctx.modules else None


def mode(values: Mapping[str, object], ctx: Context) -> str:
    """The launch's mode: `print` headless, else `interactive`; the resolver's when the
    context carries it."""
    if ctx.mode is not None:
        if ctx.mode not in catalog.MODES[HARNESS]:
            raise ValueError(f"claude has no mode {ctx.mode!r}")
        return ctx.mode
    if (values.get("target") or "terminal") != "headless":
        return "interactive"
    if comms_identity(values, ctx):
        raise NotYet("identity", "a headless Claude launch with an identity needs comms' "
                     "headless wrapper flags, which this comms does not take yet",
                     "leave the identity out, or launch in this terminal or a slot")
    return "print"


def compose(values: Mapping[str, object], ctx: Context) -> Composition:
    """Compose one Claude launch. Pure: reads only `values` and `ctx`."""
    values = common.with_defaults(HARNESS, values)
    if catalog.is_set(values.get("resume")):
        raise catalog.Refused("resume", "resume is not offered by this launcher yet",
                              "leave resume out")
    if not ctx.session_id:
        raise ValueError("a Claude launch needs its session id")
    mode_ = mode(values, ctx)
    identity = comms_identity(values, ctx)
    warnings = []
    if values.get("identity") and not identity:
        warnings.append(f"identity {values['identity']} is ignored: comms is not installed")
    scope = (str(values.get("scope") or "") or None) if identity else None
    turns = "turns" in ctx.modules
    cwd = common.cwd_of(values)

    text = common.first_turn(ctx.actor, brief=ctx.brief,
                             handoff=values.get("handoff") or None,
                             handoff_text=ctx.handoff_text)
    on_argv = mode_ == "print" or not turns
    prompt, first = (text, None) if on_argv else (None, text)
    if mode_ == "print" and prompt is None:
        raise NoBrief("brief", "a headless launch needs a brief or a handoff to run",
                      "give it --brief, --brief-file or --handoff")

    block = common.identity_block(
        session_id=ctx.session_id, cwd=cwd, posture=str(values.get("posture") or ""),
        project_key=ctx.project_key, identity=identity, scope=scope,
        handoff_dir=ctx.handoff_dir,
        agent_role=common.agent_role(str(values.get("agent") or ""), ctx.role_found))
    prompt_file = PromptFile(common.prompt_path(ctx.runs_dir, ctx.request_id),
                             common.seat_text(block, ctx.instructions))

    owned = {"session_id": ctx.session_id,
             "append_system_prompt_file": prompt_file.path}
    fit = catalog.fit(HARNESS, mode_, {**values, **owned}, ctx.chosen)
    additions = ctx.additions if (turns and not identity) else None
    extra = [str(token) for token in values.get(catalog.EXTRA) or ()]
    catalog.refuse_passthrough(HARNESS, extra)

    rows = catalog.load(HARNESS).rows
    flags: list[str] = []
    for name in ORDER:
        if name == ADDITIONS:
            flags += list(additions.argv) if additions is not None else []
        elif name in fit.applied and not (identity and name in WRAPPER_ROWS):
            flags += rows[name].tokens(mode_, fit.applied[name], cwd=cwd)
    tail = ([prompt] if prompt is not None else []) + flags + extra

    if identity:
        argv = [ctx.moxi, "comms", HARNESS, "--name", identity]
        if scope:
            argv += ["--scope", scope]
        for director in values.get("director") or ():
            argv += ["--director", str(director)]
        argv += ["--session-id", ctx.session_id]
        if values.get("native_name"):
            argv += ["--native-name", str(values["native_name"])]
        argv += ["--", *tail]
    else:
        argv = [HARNESS] + (["-p"] if mode_ == "print" else []) + tail

    if additions is not None:
        warnings += list(additions.notes)
    env = common.stamps(ctx.request_id, turns=turns, additions=additions,
                        warn_at=str(values.get("warn_at") or "") or None,
                        notes_dir=str(values.get("notes_dir") or "") or None)
    recorded = common.recorded(HARNESS, values, mode_, fit.not_applied)
    common.check_sizes(argv, values=recorded, brief=ctx.brief, env=env, prompt=prompt,
                       windows=ctx.windows)
    return Composition(harness=HARNESS, mode=mode_,
                       target=str(values.get("target") or "terminal"), argv=tuple(argv),
                       env=env, values=recorded, prompt=prompt, first=first,
                       prompt_file=prompt_file, not_applied=fit.not_applied,
                       warnings=tuple(warnings))


__all__ = ["ADDITIONS", "HARNESS", "ORDER", "WRAPPER_ROWS", "comms_identity", "compose",
           "mode"]
