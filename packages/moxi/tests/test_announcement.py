"""The announcement's optional names, the load fast path, and the light helpers of
`moxi.modules`: `available`, `session_env`, `extensions` and `Refused`."""

import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

import moxi.modules as modules
from moxi import names
from moxi.modules import ABSENT, BROKEN, PRESENT

from conftest import ANNOUNCEMENT_DEFAULTS

#: Every name past today's ten, each with a value that keeps the contract.
EVERY_NEW_NAME = {
    "ANNOUNCES": "1",
    "DAEMONS": """{"relay": {"command": "moxi-alpha-relay", "args": ["--quiet"],
                   "doc": "the relay", "stop": "process", "upgrade": "keep",
                   "memory_high": "512M", "memory_swap_max": "0",
                   "platforms": ["linux", "darwin"], "switch": "relay.enabled",
                   "socket": {"setting": "port", "addresses": ["127.0.0.1", "::1"],
                              "start": "login"}}}""",
    "TIMERS": """{"sweep": {"command": "moxi-alpha-sweep", "doc": "the sweep",
                  "every": "5m", "switch": "sweep.enabled"},
                  "report": {"command": "moxi-alpha-report", "at": ["09:00", "17:30"],
                  "days": ["mon", "fri"], "switch": "rows.report",
                  "platforms": ["win32"]}}""",
    "UNITS_FROM": "'moxi_alpha.units:rows'",
    "HEALTH": "{'relay': 'moxi_alpha.health:relay'}",
    "DOCTOR": "'moxi_alpha.health:rows'",
    "STORES": """{"record": {"root": "state", "path": "record.db", "kind": "sqlite",
                  "private": True, "retention": "keep", "rebuildable": False},
                  "events": {"root": "data", "path": "logs/events.jsonl", "kind": "jsonl",
                  "retention": "30d", "lazy": True},
                  "blobs": {"root": "cache", "path": "blobs", "kind": "dir",
                  "retention": "2G", "rebuildable": True}}""",
    "BUDGETS": "{'idle_cpu': 0.01, 'rss': '150M', 'fds': 64, 'hook_ms': 2.0}",
    "SKILLS": "'skills'",
    "PAGE": "'moxi_alpha.page:PAGE'",
    "COMPONENTS": "{'panel': {'title': 'Alpha', 'size': [2, 1]}}",
    "RESERVED_NAMES": "['alpha-bot', 'alpha-relay']",
    "TEARDOWN": "'moxi_alpha.setup:teardown'",
    "SESSION_ENV": ("{'MOXI_ALPHA_NAME': 'the session name', 'MOXI_ALPHA_PIN_': 'pins', "
                    "'CLAUDE_EXTRA': 'stamped for the harness'}"),
    "INTERNAL_ENV": "{'MOXI_ALPHA_NAME': 'launcher stamp', 'MOXI_ALPHA_PIN_': 'pins'}",
    "CONFIG_CHECK": "'moxi_alpha.settings:check'",
    "QUESTIONS": "{'relay.enabled': 'Run the relay?', 'rows.backup': 'Back up rows?'}",
}


# --- defaults ----------------------------------------------------------------------------

def test_an_empty_announcement_loads_with_every_default(site):
    site.module("alpha", drop=tuple(ANNOUNCEMENT_DEFAULTS))
    found = modules.load("alpha")
    assert found.state == PRESENT, found.reason
    for name, default in modules.KNOWN.items():
        attr = "api_name" if name == "API" else name.lower()
        assert getattr(found, attr) == default, name


def test_known_holds_every_name_once_in_order():
    assert list(modules.KNOWN)[:11] == [
        "ANNOUNCES", "INTERFACES", "API", "CLI", "SUMMARY", "OPTIONAL", "GATED",
        "INTERNAL_ENV", "SETTINGS", "SETUP", "SEGMENT"]
    assert len(modules.KNOWN) == 26
    assert isinstance(modules.KNOWN, MappingProxyType)
    found_fields = set(modules.Found._fields)
    for name in modules.KNOWN:
        assert ("api_name" if name == "API" else name.lower()) in found_fields


def test_today_s_ten_names_load_with_every_new_field_empty(site):
    site.module("alpha")
    found = modules.load("alpha")
    assert found.state == PRESENT
    assert found.announces == 1
    for attr in ("daemons", "timers", "health", "stores", "budgets", "components",
                 "session_env", "questions"):
        assert dict(getattr(found, attr)) == {}, attr
    for attr in ("units_from", "doctor", "skills", "page", "teardown", "config_check"):
        assert getattr(found, attr) is None, attr
    assert found.reserved_names == ()


def test_a_name_this_moxi_does_not_know_is_ignored(site):
    site.module("alpha", SOMETHING_LATER="{'x': object()}")
    assert modules.load("alpha").state == PRESENT


def test_a_missing_summary_is_empty(site):
    site.module("alpha", drop=("SUMMARY",))
    found = modules.load("alpha")
    assert found.state == PRESENT and found.summary == ""


# --- every new name ----------------------------------------------------------------------

def test_a_module_with_every_new_name_loads(site):
    site.module("alpha", **EVERY_NEW_NAME)
    found = modules.load("alpha")
    assert found.state == PRESENT, found.reason
    assert found.announces == 1
    relay = found.daemons["relay"]
    assert relay["socket"]["addresses"] == ("127.0.0.1", "::1")
    assert relay["socket"]["start"] == "login"
    assert relay["args"] == ("--quiet",)
    assert set(found.timers) == {"sweep", "report"}
    assert found.timers["report"]["days"] == ("mon", "fri")
    assert found.units_from == "moxi_alpha.units:rows"
    assert dict(found.health) == {"relay": "moxi_alpha.health:relay"}
    assert found.doctor == "moxi_alpha.health:rows"
    assert found.stores["record"]["kind"] == "sqlite"
    assert found.budgets["rss"] == "150M"
    assert found.skills == "skills"
    assert found.page == "moxi_alpha.page:PAGE"
    assert found.components["panel"]["size"] == (2, 1)
    assert found.reserved_names == ("alpha-bot", "alpha-relay")
    assert found.teardown == "moxi_alpha.setup:teardown"
    assert set(found.session_env) == {"MOXI_ALPHA_NAME", "MOXI_ALPHA_PIN_", "CLAUDE_EXTRA"}
    assert found.questions["rows.backup"] == "Back up rows?"
    assert found.config_check == "moxi_alpha.settings:check"


def test_tables_are_read_only_all_the_way_down(site):
    site.module("alpha", **EVERY_NEW_NAME)
    found = modules.load("alpha")
    with pytest.raises(TypeError):
        found.daemons["new"] = {}
    with pytest.raises(TypeError):
        found.daemons["relay"]["command"] = "other"
    with pytest.raises(TypeError):
        found.components["panel"]["title"] = "x"
    with pytest.raises(AttributeError):
        found.page = "x"


def test_page_is_kept_raw_and_never_imported(site):
    site.module("alpha", PAGE="'moxi_alpha.page:PAGE'")
    found = modules.load("alpha")
    assert found.state == PRESENT and found.page == "moxi_alpha.page:PAGE"
    assert "moxi_alpha.page" not in sys.modules


def test_units_from_is_kept_as_a_string_and_never_called(site):
    site.write("moxi_alpha/units.py", "raise RuntimeError('called')\n")
    site.module("alpha", UNITS_FROM="'moxi_alpha.units:rows'")
    assert modules.load("alpha").state == PRESENT
    assert "moxi_alpha.units" not in sys.modules


# --- ANNOUNCES ---------------------------------------------------------------------------

def test_a_newer_schema_is_broken_and_says_to_upgrade(site):
    site.module("alpha", ANNOUNCES="3",
                DAEMONS="{'relay': {'command': 'x', 'a_key_from_schema_three': 1}}")
    found = modules.load("alpha")
    assert found.state == BROKEN
    assert found.reason == "announces schema 3; this moxi knows up to 2; upgrade moxi"
    assert modules.ANNOUNCES == 2


SWITCHED = ("{'relay': {'command': 'moxi-alpha-relay', 'socket': {'setting': 'port', "
            "'addresses': ['127.0.0.1'], 'start': {'login': 'gate.on'}}}}")


def test_a_switch_form_start_is_refused_under_schema_one_and_read_under_two(site):
    """The table form came with schema 2: a module that announces 1 can't mean it, so it
    is broken with the reason, never read as a fixed start."""
    site.module("alpha", DAEMONS=SWITCHED)
    found = modules.load("alpha")
    assert found.state == BROKEN
    assert found.reason == (
        "its announcement moxi_alpha.module has DAEMONS['relay'] socket start "
        "{'login': 'gate.on'}, a form that comes with ANNOUNCES = 2; the module "
        "announces 1")
    site.module("alpha", ANNOUNCES="2", DAEMONS=SWITCHED)
    sys.modules.pop("moxi_alpha.module", None)
    modules.reset()
    found = modules.load("alpha")
    assert found.state == PRESENT and found.announces == 2
    assert found.daemons["relay"]["socket"]["start"] == {"login": "gate.on"}
    assert modules.login_switch(found.daemons["relay"]) == "gate.on"


@pytest.mark.parametrize("value", ["0", "True", "'1'", "1.0", "-3"])
def test_announces_is_an_integer_of_one_or_more(site, value):
    site.module("alpha", ANNOUNCES=value)
    found = modules.load("alpha")
    assert found.state == BROKEN and "ANNOUNCES" in found.reason


# --- each name's check -------------------------------------------------------------------

DAEMON = "'command': 'moxi-alpha-relay'"
TIMER = "'command': 'moxi-alpha-sweep', 'every': '5m', 'switch': 'sweep.on'"


@pytest.mark.parametrize("names, words", [
    # daemons
    ({"DAEMONS": "['relay']"}, "DAEMONS"),
    ({"DAEMONS": "{'Relay': {%s}}" % DAEMON}, "DAEMONS name 'Relay'"),
    ({"DAEMONS": "{'relay': 'moxi-alpha-relay'}"}, "an entry is a table"),
    ({"DAEMONS": "{'relay': {%s, 'restart': 'always'}}" % DAEMON},
     "key 'restart', which this moxi does not know"),
    ({"DAEMONS": "{'relay': {'doc': 'x'}}"}, "command = None"),
    ({"DAEMONS": "{'relay': {'command': '/usr/bin/relay'}}"}, "not a path"),
    ({"DAEMONS": "{'relay': {%s, 'argv': ['/bin/x']}}" % DAEMON}, "key 'argv'"),
    ({"DAEMONS": "{'relay': {%s, 'enabled': False}}" % DAEMON}, "key 'enabled'"),
    ({"DAEMONS": "{'relay': {%s, 'args': '--quiet'}}" % DAEMON}, "args"),
    ({"DAEMONS": "{'relay': {%s, 'doc': 'two\\nlines'}}" % DAEMON}, "doc"),
    ({"DAEMONS": "{'relay': {%s, 'stop': 'tree'}}" % DAEMON}, "stop"),
    ({"DAEMONS": "{'relay': {%s, 'upgrade': 'never'}}" % DAEMON}, "upgrade"),
    ({"DAEMONS": "{'relay': {%s, 'memory_high': '512MB'}}" % DAEMON}, "memory_high"),
    ({"DAEMONS": "{'relay': {%s, 'memory_swap_max': 0}}" % DAEMON}, "memory_swap_max"),
    ({"DAEMONS": "{'relay': {%s, 'platforms': ['linux', 'freebsd']}}" % DAEMON},
     "platforms"),
    ({"DAEMONS": "{'relay': {%s, 'switch': 'not a key'}}" % DAEMON}, "switch"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port'}}}" % DAEMON}, "socket"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': []}}}"
      % DAEMON}, "socket"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['localhost']}}}" % DAEMON}, "socket"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1'], 'backlog': 5}}}" % DAEMON}, "socket"),
    # a socket's start is what starts the daemon: its sockets, or a login
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1'], 'start': 'boot'}}}" % DAEMON},
     "optionally start ('connection', the default, 'login', or {'login': '<a bool "
     "settings key>'})"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1'], 'start': None}}}" % DAEMON}, "socket"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1'], 'start': ['login']}}}" % DAEMON}, "socket"),
    # the switch form is a table of one key, login, naming a settings key
    *[({"ANNOUNCES": "2", "DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', "
                                     "'addresses': ['127.0.0.1'], 'start': %s}}}"
        % (DAEMON, start)}, "optionally start")
      for start in ("{}", "{'login': 5}", "{'login': ''}", "{'login': 'a b'}",
                    "{'boot': 'gate.on'}", "{'login': 'gate.on', 'more': 1}",
                    "{'connection': 'gate.on'}")],
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['::1', '127.0.0.1', '::1']}}}" % DAEMON}, "with ::1 given twice"),
    ({"DAEMONS": "{'relay': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['::1', '0:0::1']}}}" % DAEMON}, "with ::1 and 0:0::1 given twice"),
    # a daemon's socket units are named for it and for <name>-1 .. <name>-<n-1>
    ({"DAEMONS": "{'shell': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1', '::1']}}, 'shell-1': {%s, 'socket': {'setting': "
                 "'port', 'addresses': ['127.0.0.1']}}}" % (DAEMON, DAEMON)},
     "declares daemon 'shell-1' beside daemon 'shell'"),
    ({"DAEMONS": "{'shell': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1', '::1']}}, 'shell-1': {%s}}" % (DAEMON, DAEMON)},
     "declares daemon 'shell-1' beside daemon 'shell'"),
    ({"DAEMONS": "{'shell': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1', '::1']}}}" % DAEMON,
      "TIMERS": "{'shell-1': {%s}}" % TIMER},
     "declares timer 'shell-1' beside daemon 'shell'"),
    ({"DAEMONS": "{'shell': {%s, 'socket': {'setting': 'port', 'addresses': "
                 "['127.0.0.1', '::1', '10.0.0.1']}}, 'shell-2': {%s}}" % (DAEMON, DAEMON)},
     "its address 3"),
    # timers
    ({"TIMERS": "{'sweep': {'command': 'moxi-alpha-sweep', 'every': '5m'}}"},
     "no switch"),
    ({"TIMERS": "{'sweep': {%s, 'at': ['09:00']}}" % TIMER}, "both every and at"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's'}}"}, "neither every and at"),
    ({"TIMERS": "{'sweep': {%s, 'socket': {}}}" % TIMER}, "key 'socket'"),
    ({"TIMERS": "{'sweep': {%s, 'memory_high': '1G'}}" % TIMER}, "key 'memory_high'"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'every': '0m'}}"}, "every"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'every': '25h'}}"}, "every"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'every': '90s'}}"}, "every"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'every': '1h', "
                "'days': ['mon']}}"}, "days goes with at"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'at': []}}"}, "at"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'at': ['24:00']}}"}, "at"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'at': ['9:00']}}"}, "at"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'at': ['09:00'], "
                "'days': []}}"}, "days"),
    ({"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'at': ['09:00'], "
                "'days': ['monday']}}"}, "days"),
    ({"DAEMONS": "{'sweep': {%s}}" % DAEMON, "TIMERS": "{'sweep': {%s}}" % TIMER},
     "both a daemon and a timer"),
    # the rest
    ({"UNITS_FROM": "'moxi_alpha.units'"}, "UNITS_FROM"),
    ({"HEALTH": "{'relay': 'moxi_alpha.health:relay'}"}, "no daemon in DAEMONS"),
    ({"DAEMONS": "{'relay': {%s}}" % DAEMON, "HEALTH": "{'relay': 'nope'}"}, "HEALTH"),
    ({"DOCTOR": "['moxi_alpha.doctor:rows']"}, "DOCTOR"),
    ({"STORES": "{'record': {'root': 'home', 'path': 'r.db', 'kind': 'sqlite'}}"},
     "root"),
    ({"STORES": "{'record': {'root': 'state', 'path': '../r.db', 'kind': 'sqlite'}}"},
     "path"),
    ({"STORES": "{'record': {'root': 'state', 'path': '/r.db', 'kind': 'sqlite'}}"},
     "path"),
    ({"STORES": "{'record': {'root': 'state', 'path': 'a\\\\..\\\\r.db', "
                "'kind': 'sqlite'}}"}, "path"),
    ({"STORES": "{'record': {'root': 'state', 'path': 'r.db', 'kind': 'db'}}"}, "kind"),
    ({"STORES": "{'record': {'root': 'state', 'path': 'r.db', 'kind': 'sqlite', "
                "'private': 'yes'}}"}, "private"),
    ({"STORES": "{'record': {'root': 'state', 'path': 'r.db', 'kind': 'sqlite', "
                "'retention': '30 days'}}"}, "retention"),
    ({"STORES": "{'record': {'root': 'state', 'path': 'r.db', 'kind': 'sqlite', "
                "'owner': 'me'}}"}, "key 'owner', which this moxi does not know"),
    ({"STORES": "{'Record': {'root': 'state', 'path': 'r.db', 'kind': 'sqlite'}}"},
     "STORES name"),
    # the core's own stores in a module's state directory
    ({"STORES": "{'runs': {'root': 'state', 'path': 'runs.jsonl', 'kind': 'jsonl'}}"},
     "that path is the core's"),
    ({"STORES": "{'runs': {'root': 'state', 'path': './Runs.jsonl.1', 'kind': 'jsonl'}}"},
     "that path is the core's"),
    ({"STORES": "{'doc': {'root': 'state', 'path': 'doctor.json', 'kind': 'file'}}"},
     "that path is the core's"),
    ({"STORES": "{'logs': {'root': 'state', 'path': 'logs', 'kind': 'dir'}}"},
     "that path is the core's"),
    ({"STORES": "{'log': {'root': 'state', 'path': 'logs\\\\relay.log', "
                "'kind': 'file'}}"}, "that path is the core's"),
    ({"BUDGETS": "{'import_ms': 3}"}, "BUDGETS key 'import_ms'"),
    ({"BUDGETS": "{'idle_cpu': 0}"}, "idle_cpu"),
    ({"BUDGETS": "{'idle_cpu': 1.5}"}, "idle_cpu"),
    ({"BUDGETS": "{'rss': 150}"}, "rss"),
    ({"BUDGETS": "{'fds': 0}"}, "fds"),
    ({"BUDGETS": "{'fds': True}"}, "fds"),
    ({"BUDGETS": "{'hook_ms': -1}"}, "hook_ms"),
    ({"SKILLS": "'../skills'"}, "SKILLS"),
    ({"SKILLS": "'/abs/skills'"}, "SKILLS"),
    ({"SKILLS": "'C:skills'"}, "SKILLS"),
    ({"PAGE": "'moxi_alpha.page'"}, "PAGE"),
    ({"COMPONENTS": "{'panel': 'Alpha'}"}, "table of tables"),
    ({"RESERVED_NAMES": "['bot', '']"}, "RESERVED_NAMES"),
    ({"RESERVED_NAMES": "['bot', 'bot']"}, "no name twice"),
    ({"RESERVED_NAMES": "'bot'"}, "RESERVED_NAMES"),
    ({"TEARDOWN": "'teardown'"}, "TEARDOWN"),
    ({"CONFIG_CHECK": "'moxi_alpha.settings'"}, "CONFIG_CHECK"),
    ({"CONFIG_CHECK": "len"}, "CONFIG_CHECK"),
    ({"SESSION_ENV": "{'NOT-A-NAME': 'x'}"}, "SESSION_ENV"),
    ({"SESSION_ENV": "{'MOXI_ALPHA_NAME': 'stamped'}"},
     "under the module's own prefix MOXI_ALPHA_, and INTERNAL_ENV does not declare it"),
    ({"SESSION_ENV": "{'MOXI_ALPHA_PIN_': 'pins'}",
      "INTERNAL_ENV": "{'MOXI_ALPHA_PIN': 'one pin'}"}, "INTERNAL_ENV does not declare"),
    ({"QUESTIONS": "{'relay enabled': 'Run it?'}"}, "QUESTIONS"),
    ({"QUESTIONS": "{'relay.enabled': ''}"}, "QUESTIONS"),
    ({"QUESTIONS": "{'relay.enabled': 'Run\\nit?'}"}, "QUESTIONS"),
])
def test_a_new_name_of_the_wrong_shape_is_broken(site, names, words):
    site.module("alpha", **names)
    found = modules.load("alpha")
    assert found.state == BROKEN, names
    assert words in found.reason, found.reason


@pytest.mark.parametrize("names", [
    # shell's two socket units are named for shell and shell-1, never shell-2
    {"DAEMONS": "{'shell': {%s, 'socket': {'setting': 'port', 'addresses': "
                "['127.0.0.1', '::1']}}, 'shell-2': {%s, 'socket': {'setting': 'port', "
                "'addresses': ['127.0.0.1']}}}" % (DAEMON, DAEMON),
     "TIMERS": "{'shell-3': {%s}}" % TIMER},
    {"DAEMONS": "{'shell': {%s, 'socket': {'setting': 'port', 'addresses': "
                "['127.0.0.1']}}, 'shell-1': {%s}}" % (DAEMON, DAEMON)},
    {"SESSION_ENV": "{'MOXI_ALPHA_NAME': 'stamped'}",
     "INTERNAL_ENV": "{'MOXI_ALPHA_': 'every stamp'}"},
    {"SESSION_ENV": "{'MOXI_ALPHA_PIN_': 'pins'}",
     "INTERNAL_ENV": "{'MOXI_ALPHA_': 'every stamp'}"},
    {"SESSION_ENV": "{'MOXI_ALPHA_NAME': 'stamped'}",
     "INTERNAL_ENV": "{'MOXI_ALPHA_NAME': 'stamped'}"},
    {"SESSION_ENV": "{'CLAUDE_X': 'the harness', 'MOXI_BETA_Y': 'beta'}"},
    {"STORES": "{'record': {'root': 'runtime', 'path': 'r', 'kind': 'file', "
               "'retention': '512K'}}"},
    # the core's store names are only the core's in the state root, and only there
    {"STORES": "{'runs': {'root': 'data', 'path': 'runs.jsonl', 'kind': 'jsonl'}}"},
    {"STORES": "{'runs': {'root': 'state', 'path': 'sub/runs.jsonl', 'kind': 'jsonl'}}"},
    {"STORES": "{'logs': {'root': 'state', 'path': 'mylogs', 'kind': 'dir'}}"},
    {"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'every': '24h'}}"},
    {"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'every': '1m'}}"},
    {"TIMERS": "{'sweep': {'command': 'x', 'switch': 's', 'at': ['00:00', '23:59']}}"},
    {"BUDGETS": "{'idle_cpu': 1, 'hook_ms': 3}"},
    {"RESERVED_NAMES": "('bot',)"},
    {"DAEMONS": "{'relay': {%s, 'platforms': []}}" % DAEMON},
    # A HEALTH key may name a daemon only UNITS_FROM declares, at run time.
    {"UNITS_FROM": "'moxi_alpha.units:rows'",
     "HEALTH": "{'gateway': 'moxi_alpha.health:gateway'}"},
])
def test_these_shapes_keep_the_contract(site, names):
    site.module("alpha", **names)
    found = modules.load("alpha")
    assert found.state == PRESENT, found.reason


def test_every_bad_name_is_named_in_the_reason(site):
    site.module("alpha", PAGE="'x'", TEARDOWN="'y'")
    reason = modules.load("alpha").reason
    assert "PAGE" in reason and "TEARDOWN" in reason


def test_run_time_entries_take_their_own_forms():
    unit = modules._unit
    exe = sys.executable  # an absolute path on every system
    here = str(Path(exe).parent)
    unit("daemon", "row", {"argv": [exe, "--all"], "cwd": here, "enabled": False},
         runtime=True)
    unit("timer", "row", {"argv": [exe], "every": "1h"}, runtime=True)
    unit("timer", "row", {"command": "moxi-alpha-x", "at": ["03:00"]}, runtime=True)
    for kind, entry, words in [
        ("daemon", {"argv": ["backup"]}, "absolute path"),
        ("daemon", {"argv": []}, "absolute path"),
        ("daemon", {"argv": [exe], "command": "x"}, "both command and argv"),
        ("daemon", {"argv": [exe], "cwd": "srv"}, "cwd"),
        ("daemon", {"argv": [exe], "enabled": "no"}, "enabled"),
    ]:
        with pytest.raises(modules._Bad, match=words):
            unit(kind, "row", entry, runtime=True)
    with pytest.raises(modules._Bad, match="no switch"):
        unit("timer", "row", {"command": "x", "every": "1h"})


# --- names the core keeps ----------------------------------------------------------------

@pytest.mark.parametrize("name", ["moxi", "set", "unset", "runtime", "installs", "test"])
def test_a_name_the_core_keeps_is_broken(site, name):
    site.module(name)
    found = modules.load(name)
    assert found.state == BROKEN and "keeps for itself" in found.reason
    assert names.reserved_why(name) in found.reason


def test_a_module_named_test_is_broken_saying_why(site):
    site.module("test")
    found = modules.load("test")
    assert found.state == BROKEN
    assert found.reason == ("'test' is a name moxi keeps for itself, not a module: its "
                            "variables would start MOXI_TEST_, which moxi-dev's test tiers "
                            "use; the module needs another name")


@pytest.mark.parametrize("name", ["doctor", "uninstall", "run", "paths"])
def test_the_new_verbs_are_broken_as_module_names(site, name):
    site.module(name)
    found = modules.load(name)
    assert found.state == BROKEN and "verbs" in found.reason


# --- the load fast path and the full scan ------------------------------------------------

def test_load_reads_only_the_module_s_own_distribution(site, monkeypatch):
    import importlib.metadata

    site.module("alpha")

    def no_scan(*args, **kwargs):
        raise AssertionError("load went through importlib.metadata")

    for name in ("entry_points", "distributions", "distribution"):
        monkeypatch.setattr(importlib.metadata, name, no_scan)
    assert modules.load("alpha").state == PRESENT
    assert modules.load("beta").state == ABSENT


def test_a_module_whose_distribution_is_named_otherwise(site):
    site.module("alpha", dist="alpha-tools")
    found = modules.load("alpha")
    assert found.state == ABSENT and "moxi-alpha is not installed" in found.reason
    (found,) = modules.scan()
    assert found.state == BROKEN
    assert found.reason == ("alpha-tools 0.1.0 declares the Moxi module 'alpha'; its "
                            "distribution must be moxi-alpha")
    assert found.distribution == "alpha-tools"


def test_a_distribution_without_the_entry_is_absent(site):
    site.distribution("moxi-alpha")
    found = modules.load("alpha")
    assert found.state == ABSENT
    assert "declares no moxi.modules entry point named 'alpha'" in found.reason


def test_scan_lists_every_module_sorted_and_loads_the_good_ones(site):
    site.module("beta")
    site.module("alpha", API="'no good'")
    found = modules.scan()
    assert [(f.name, f.state) for f in found] == [("alpha", BROKEN), ("beta", PRESENT)]
    assert found[1] == modules.load("beta")


def test_scan_never_raises(site, monkeypatch):
    import importlib.metadata

    def boom(**kwargs):
        raise OSError("metadata unreadable")

    monkeypatch.setattr(importlib.metadata, "entry_points", boom)
    assert modules.scan() == []


# --- available ---------------------------------------------------------------------------

def test_available_asks_the_import_system_alone(site, monkeypatch):
    import importlib.metadata

    site.write("moxi_alpha/__init__.py", "raise RuntimeError('imported')\n")

    def no_metadata(*args, **kwargs):
        raise AssertionError("available read metadata")

    for name in ("entry_points", "distributions", "distribution"):
        monkeypatch.setattr(importlib.metadata, name, no_metadata)
    assert modules.available("alpha")
    assert "moxi_alpha" not in sys.modules
    assert not modules.available("nothere")
    assert not modules.available("Not-A-Name")
    assert not modules.available(None)


# --- session_env -------------------------------------------------------------------------

def test_session_env_is_the_union_over_present_modules(site):
    site.module("alpha", SESSION_ENV="{'MOXI_ALPHA_NAME': 'name', 'CLAUDE_X': 'x'}",
                INTERNAL_ENV="{'MOXI_ALPHA_NAME': 'name'}")
    site.module("beta", SESSION_ENV="{'CLAUDE_X': 'x', 'CODEX_': 'codex'}")
    site.module("gamma", SESSION_ENV="{'GAMMA_ONLY': 'g'}", API="'not a name'")
    site.module("delta")
    assert modules.session_env() == frozenset({"MOXI_ALPHA_NAME", "CLAUDE_X", "CODEX_"})


def test_session_env_with_no_modules_is_empty(site):
    assert modules.session_env() == frozenset()


# --- Refused ----------------------------------------------------------------------------

def test_refused_carries_its_reason():
    exc = modules.Refused("the relay still holds mail")
    assert exc.reason == "the relay still holds mail"
    assert str(exc) == "the relay still holds mail"
    assert isinstance(exc, Exception)


# --- extensions -------------------------------------------------------------------------

def _offer(site, dist: str, group: str, entries: dict[str, str]) -> None:
    info = f"{dist.replace('-', '_')}-0.1.0.dist-info"
    site.write(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.1.0\n")
    lines = "".join(f"{k} = {v}\n" for k, v in entries.items())
    site.write(f"{info}/entry_points.txt", f"[{group}]\n{lines}")


def test_extensions_lists_a_group_sorted_by_name_importing_nothing(site):
    site.write("moxi_comms/__init__.py", "")
    site.write("moxi_comms/kinds.py", "KINDS = ('mail',)\n")
    site.write("moxi_cache/__init__.py", "")
    site.write("moxi_cache/hooks.py", "def listen():\n    return 'heard'\n")
    _offer(site, "moxi-comms", "moxi.turns", {"comms.kinds": "moxi_comms.kinds:KINDS"})
    _offer(site, "moxi-cache", "moxi.turns", {"cache": "moxi_cache.hooks:listen"})
    found = modules.extensions("moxi.turns")
    assert found == [
        modules.Extension(module="cache", name="cache", target="moxi_cache.hooks:listen",
                          distribution="moxi-cache"),
        modules.Extension(module="comms", name="comms.kinds",
                          target="moxi_comms.kinds:KINDS", distribution="moxi-comms"),
    ]
    assert "moxi_cache.hooks" not in sys.modules
    assert found[0].load()() == "heard"
    assert found[1].load() == ("mail",)


def test_an_extension_without_an_attribute_loads_its_module(site):
    site.write("moxi_repo/__init__.py", "")
    site.write("moxi_repo/hooks.py", "NAME = 'hooks'\n")
    _offer(site, "moxi-repo", "moxi.repo.git_hooks", {"repo": "moxi_repo.hooks"})
    (ext,) = modules.extensions("moxi.repo.git_hooks")
    assert ext.module == "repo" and ext.load().NAME == "hooks"


def test_an_extension_that_fails_to_load_raises_extension_error(site):
    site.write("moxi_comms/__init__.py", "")
    site.write("moxi_comms/bad.py", "raise RuntimeError('broken listener')\n")
    _offer(site, "moxi-comms", "moxi.turns", {"comms": "moxi_comms.bad:listen",
                                             "comms.gone": "moxi_comms.kinds:NOTHING"})
    bad, gone = modules.extensions("moxi.turns")
    with pytest.raises(modules.ExtensionError) as info:
        bad.load()
    assert info.value.name == "comms" and "RuntimeError: broken listener" in info.value.reason
    with pytest.raises(modules.ExtensionError):
        gone.load()


@pytest.mark.parametrize("group", ["moxi", "moxi.Repo", "moxi.repo.git-hooks",
                                   "moxi.repo.git.hooks", "other.repo", "", None])
def test_a_group_that_is_not_named_moxi_module_part_has_no_entries(site, group):
    _offer(site, "moxi-repo", str(group), {"repo": "moxi_repo.hooks"})
    assert modules.extensions(group) == []


def test_extensions_never_raises(site, monkeypatch):
    import importlib.metadata

    def boom(**kwargs):
        raise OSError("metadata unreadable")

    monkeypatch.setattr(importlib.metadata, "distributions", boom)
    assert modules.extensions("moxi.turns") == []


def test_extensions_returns_what_it_read_before_a_failure(site, monkeypatch):
    import importlib.metadata

    _offer(site, "moxi-alpha", "moxi.turns", {"alpha": "moxi_alpha.hooks"})
    real = importlib.metadata.distributions

    def then_boom(**kwargs):
        yield from real(**kwargs)
        raise OSError("the next one is unreadable")

    monkeypatch.setattr(importlib.metadata, "distributions", then_boom)
    assert [e.name for e in modules.extensions("moxi.turns")] == ["alpha"]


# --- light at import ---------------------------------------------------------------------

def test_importing_modules_loads_nothing_heavy():
    code = ("import sys; import moxi.modules; "
            "print(*[m in sys.modules for m in ('importlib.metadata', 'importlib.util', "
            "'logging', 'ipaddress', 'tomllib', 'json')])")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True,
                         text=True, check=True)
    assert out.stdout.split() == ["False"] * 6


def test_a_real_package_imported_earlier_does_not_stand_in_for_the_site_s(site):
    import types

    stale = types.ModuleType("moxi_stale.module")
    stale.SUMMARY = "the real one"
    sys.modules["moxi_stale"] = types.ModuleType("moxi_stale")
    sys.modules["moxi_stale.module"] = stale
    site.module("stale", SUMMARY='"the fake one"')
    found = modules.load("stale")
    assert found.state == PRESENT, found.reason
    assert found.summary == "the fake one"
