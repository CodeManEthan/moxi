"""The announcement of Moxi's sessions module, which moxi.modules reads.

It offers interface 2, the read API in `moxi_sessions.api`, the verb `moxi sessions`, and,
with the `ui` extra, a page in the browser UI shell that shows one session's last turns
and a block other pages may place for the same view.
"""

INTERFACES = frozenset({2})
API = "moxi_sessions.api"
CLI = "moxi_sessions.cli:main"
SUMMARY = ("Read each harness's own session records in one shape: find a session and its "
           "subagents, list a root's sources, read records forward or back.")
OPTIONAL = {
    "ui": "The sessions page in the browser UI shell: one session's last turns and its "
          "subagents, by harness and id, and the window block other pages place.",
}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
SETUP = None
SEGMENT = None
# The announcement's version, and the optional names it declares. sessions keeps no state
# of its own, so it declares no store; it runs no process, so no daemon, timer or health
# check. Its doctor rows check each harness's root.
ANNOUNCES = 1
STORES = {}
DOCTOR = "moxi_sessions.doctor:rows"
#: sessions' page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_sessions.page:PAGE"
#: The page, whose route names the session (`<harness>/<session>`, or
#: `claude/<session>/<agent>` for a Claude Code subagent; at the empty route it asks for
#: the harness and id), and the window block, the same view placed by attributes.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Sessions", "contract": 1, "place": "page",
             "doc": "One session's last turns and its subagents, by harness and session id"},
    "window": {"script": "window.js", "title": "Session", "contract": 1, "place": "block",
               "doc": "One session's last turns, with a link that opens it in the sessions "
                      "page",
               "attributes": {
                   "harness": {"type": "enum", "values": ["claude", "codex", "opencode"],
                               "required": True, "doc": "the harness that wrote the session"},
                   "session": {"type": "string", "required": True,
                               "doc": "the harness's own session id (a Codex thread id)"},
                   "agent": {"type": "string",
                             "doc": "a Claude Code subagent's id, under its parent session"},
               }},
}
