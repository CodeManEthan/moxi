"""The launcher's settings: the schema reads, and every key has its default with no file."""

from __future__ import annotations

from moxi.config import DEFAULT, FILE
from moxi.paths import Layout

from moxi_launcher import settings

DEFAULTS = {
    "ready.wait_s": 60,
    "first.wait_s": 120,
    "startup.wait_s": 20,
    "startup.trust_dialog": False,
    "startup.dev_channels_dialog": False,
    "runs.keep_days": 30,
    "relaunch.wait_s": 900,
    "relaunch.person_s": 120,
    "relaunch.dialog_s": 60,
    "relaunch.name_wait_s": 15,
    "relaunch.follow_s": 300,
    "relaunch.fallback": "",
    "relaunch.pass_env": [],
    "relaunch.notice_retry_s": 30,
    "recycle.limit_wait_s": 1800,
    "keep_warm.subagent_cap": 3,
    "keep_warm.director_cap": 10,
    "page.launch_wait_s": 600,
    "records.keep_days": 90,
}
#: The list settings whose defaults are texts: checked for their count only.
TEXTS = {"keep_warm.subagent_texts": 3, "keep_warm.director_texts": 2}


def test_with_no_file_every_key_has_its_default():
    current = settings.current()
    for key, value in DEFAULTS.items():
        assert current[key] == value
        assert current.source(key) == DEFAULT


def test_the_schema_declares_exactly_these_keys():
    assert set(settings.schema().keys) == set(DEFAULTS) | set(TEXTS)
    current = settings.current()
    for key, count in TEXTS.items():
        assert len(current[key]) == count and all(isinstance(t, str) for t in current[key])


def test_the_startup_keys_need_slots():
    schema = settings.schema()
    for key in ("startup.wait_s", "startup.trust_dialog", "startup.dev_channels_dialog"):
        assert schema.keys[key].needs == "slots"


def test_a_key_set_in_the_file_wins():
    path = Layout("launcher").config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[ready]\nwait_s = 5\n\n[startup]\ntrust_dialog = true\n",
                    encoding="utf-8")
    current = settings.current()
    assert current["ready.wait_s"] == 5
    assert current.source("ready.wait_s") == FILE
    assert current["startup.trust_dialog"] is True
    assert current["first.wait_s"] == 120
