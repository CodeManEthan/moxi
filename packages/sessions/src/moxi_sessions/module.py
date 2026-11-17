"""The announcement of Moxi's sessions module, which moxi.modules reads.

It offers interfaces 2 to 5, the read API in `moxi_sessions.api` (interface 3 is 2
plus `retention()`; interface 4 is 3 plus the list of sessions, `sessions_list`;
interface 5 is 4 plus `session_facts`, which names sessions asked for by key), the verb
`moxi sessions`, a setup that prints how long each harness keeps its transcripts, and,
with the `ui` extra, a page in the browser UI shell that lists the sessions and shows one
session's last turns, and a block other pages may place for that view. With the
`secrets` extra, `moxi sessions read` redacts what it prints. With the `turns` extra, turns
enforces sessions' transcript-read guard, off until the install's question or its switch
turns it on.
"""

INTERFACES = frozenset({2, 3, 4, 5})
API = "moxi_sessions.api"
CLI = "moxi_sessions.cli:main"
SUMMARY = ("Read each harness's own session records in one shape: find a session and its "
           "subagents, list a root's sources, read records forward or back.")
OPTIONAL = {
    "ui": "The sessions page in the browser UI shell: the list of sessions with its "
          "filters and find field, one session's last turns and its subagents, and the "
          "window block other pages place.",
    "secrets": "Redaction for `moxi sessions read`: each message and tool call line passes "
               "through secrets' credentials and broad sets before it is printed. Without "
               "it the read prints as written and says so.",
    "turns": "The transcript-read guard: turns refuses an agent's Read, Grep, Glob or shell "
             "read of a harness transcript and points it to `moxi sessions read`, once "
             "guard.transcript_read is on.",
}
GATED = {}
INTERNAL_ENV = {}
SETTINGS = "settings.toml"
#: The install question: the transcript-read guard's switch, asked as
#: "sessions: <text>? [y/N]".
QUESTIONS = {
    "guard.transcript_read": "the transcript-read guard (refuse an agent's Read, Grep, "
                             "Glob or shell read of a harness transcript and point it to "
                             "moxi sessions read; turns enforces it)",
}
#: Prints each harness's transcript retention; asks nothing and changes nothing.
SETUP = "moxi_sessions.setup:setup"
SEGMENT = None
# The announcement's version, and the optional names it declares. sessions keeps no state
# of its own, so it declares no store; it runs no process, so no daemon, timer or health
# check. Its doctor rows check each harness's root, report each harness's retention and
# say whether the transcript-read guard is on and enforced.
ANNOUNCES = 1
STORES = {}
DOCTOR = "moxi_sessions.doctor:rows"
#: sessions' page server, which the browser UI shell starts. Nothing here imports it.
PAGE = "moxi_sessions.page:PAGE"
#: The page, which lists the sessions at the empty route and shows one session at the
#: route that names it (`<harness>/<session>`, or `claude/<session>/<agent>` for a Claude
#: Code subagent), and the window block, that one session's view placed by attributes.
COMPONENTS = {
    "page": {"script": "page.js", "title": "Sessions", "contract": 1, "place": "page",
             "group": "daily", "order": 20,
             "doc": "The list of sessions, and one session's last turns and its subagents"},
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
