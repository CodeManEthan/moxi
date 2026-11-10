"""The settings view and `<moxi-ui-form>` in a browser: every type, the save path through
`moxi config` (a saved int, a switch-table entry, a refused value, a typing slip, a
reset), a file that won't parse, the theme mode and the theme name picking a colour set,
and the form in submit mode with catalog rows."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

# The settings view reads and writes through `moxi config` in a child process.
SETTINGS_MS = 15_000
WRITES = re.compile(r"/m/ui/api/settings/(set|unset)$")
TOKENS = Path(__file__).resolve().parents[1] / "web" / "tokens.json"


def _settings(page, module="fixture"):
    page.goto(f"/m/ui/settings/{module}")
    form = page.locator(f"#moxi-panel > moxi-ui-page section.card[data-module={module}] moxi-ui-form")
    expect(form.locator("[data-control]").first).to_be_attached(timeout=SETTINGS_MS)
    return form


def _field(form, name):
    return form.locator(f".field[data-name='{name}']")


def _msg(form, name):
    return form.locator(f".msg[data-name='{name}']")


def _file(shell, module="fixture") -> dict:
    try:
        return tomllib.loads(shell.config_file(module).read_text())
    except FileNotFoundError:
        return {}


def test_each_type_renders_its_own_control(ui_page):
    form = _settings(ui_page)
    for name in ("greeting", "limit", "workers", "ratio", "folder"):
        expect(form.locator(f"input[type=text][data-control={name}]")).to_be_editable()
    expect(form.locator("input[data-control=limit]")).to_have_attribute("inputmode", "numeric")
    expect(form.locator("input[data-control=ratio]")).to_have_attribute("inputmode", "decimal")
    expect(form.locator("input[data-control=folder]")).to_have_attribute("placeholder", "an absolute path")
    expect(form.locator("input[type=checkbox][data-control=loud]")).not_to_be_checked()
    colour = form.locator("select[data-control=colour]")
    expect(colour).to_have_value("green")
    expect(colour.locator("option")).to_have_text(["red", "green", "blue"])
    expect(_field(form, "tags").get_by_role("button", name="Add")).to_be_visible()
    expect(form.locator("textarea[data-control=extra]")).to_be_editable()
    # The switch table is toggles, never a TOML box.
    expect(form.locator("textarea[data-control=rows]")).to_have_count(0)
    expect(form.locator("input[type=checkbox][data-control='rows.backup']")).to_be_attached()
    # A key whose module is absent is shown off, naming the module.
    peer = _field(form, "peer")
    expect(peer).to_have_class(re.compile(r"\boff\b"))
    expect(peer.locator("input")).to_be_disabled()
    expect(peer).to_contain_text("nosuch")
    expect(_field(form, "limit")).to_contain_text("applies now")
    expect(_field(form, "workers")).to_contain_text("applies at next start")
    expect(_field(form, "limit")).to_contain_text("An int read at each use.")


def test_an_int_is_saved_to_the_file_and_reset_uses_unset(ui_shell, ui_page):
    writes: list[str] = []
    ui_page.on("request", lambda r: writes.append(r.url) if WRITES.search(r.url) else None)
    form = _settings(ui_page)
    box = form.locator("input[data-control=limit]")
    box.fill("12")
    box.press("Tab")
    expect(_msg(form, "limit")).to_have_text("fixture: limit set (by ui)", timeout=SETTINGS_MS)
    expect(_msg(form, "limit")).to_have_class(re.compile(r"\binfo\b"))
    assert _file(ui_shell)["limit"] == 12
    # The section is read again: the value and where it came from follow the file.
    expect(box).to_have_value("12")
    expect(_field(form, "limit")).to_contain_text("applies now")
    reset = _field(form, "limit").get_by_role("button", name="Reset to default")
    reset.click()
    expect(_msg(form, "limit")).to_contain_text("limit", timeout=SETTINGS_MS)
    expect(box).to_have_value("10")
    assert "limit" not in _file(ui_shell)
    assert [url.rsplit("/", 1)[1] for url in writes] == ["set", "unset"]
    expect(_field(form, "limit").get_by_role("button", name="Reset to default")).to_have_count(0)


def test_a_start_key_is_saved_with_its_mark(ui_shell, ui_page):
    form = _settings(ui_page)
    box = form.locator("input[data-control=workers]")
    box.fill("3")
    box.press("Tab")
    expect(_msg(form, "workers")).to_contain_text("workers", timeout=SETTINGS_MS)
    expect(box).to_have_value("3")
    expect(_field(form, "workers")).to_contain_text("applies at next start")
    assert _file(ui_shell)["workers"] == 3


def test_the_entry_only_the_timer_names_shows_off_and_turning_it_on_writes_it(ui_shell, ui_page):
    form = _settings(ui_page)
    toggle = form.locator("input[type=checkbox][data-control='rows.backup']")
    expect(toggle).not_to_be_checked()
    entry = form.locator(".toggle", has=ui_page.locator("[data-control='rows.backup']"))
    expect(entry).to_contain_text("not set")
    toggle.check()
    expect(_msg(form, "rows.backup")).not_to_be_empty(timeout=SETTINGS_MS)
    expect(_msg(form, "rows.backup")).not_to_have_class(re.compile(r"\berror\b"))
    assert _file(ui_shell)["rows"] == {"backup": True}
    toggle = form.locator("input[type=checkbox][data-control='rows.backup']")
    expect(toggle).to_be_checked()
    expect(entry).not_to_contain_text("not set")
    form.get_by_role("button", name="Reset rows.backup").click()
    expect(form.locator("input[type=checkbox][data-control='rows.backup']")).not_to_be_checked(
        timeout=SETTINGS_MS)
    assert _file(ui_shell).get("rows", {}) == {}


def test_a_value_the_server_refuses_shows_its_text(ui_shell, ui_page):
    form = _settings(ui_page)
    area = form.locator("textarea[data-control=extra]")
    with ui_page.expect_response(WRITES) as answer:
        area.fill("not = [valid")
        area.press("Tab")
    assert answer.value.status == 422
    message = _msg(form, "extra")
    expect(message).to_have_class(re.compile(r"\berror\b"))
    expect(message).to_contain_text("which is not one TOML table")
    expect(message).to_contain_text("give the whole table as one TOML value")
    assert "extra" not in _file(ui_shell)


def test_a_typing_slip_is_caught_before_any_request(ui_page):
    writes: list[str] = []
    ui_page.on("request", lambda r: writes.append(r.url) if WRITES.search(r.url) else None)
    form = _settings(ui_page)
    box = form.locator("input[data-control=limit]")
    box.fill("12a")
    expect(_msg(form, "limit")).to_have_class(re.compile(r"\berror\b"))
    expect(_msg(form, "limit")).not_to_be_empty()
    box.press("Tab")
    ratio = form.locator("input[data-control=ratio]")
    ratio.fill("half")
    ratio.press("Tab")
    expect(_msg(form, "ratio")).to_have_class(re.compile(r"\berror\b"))
    folder = form.locator("input[data-control=folder]")
    folder.fill("not/absolute")
    folder.press("Tab")
    expect(_msg(form, "folder")).to_have_class(re.compile(r"\berror\b"))
    ui_page.evaluate("1")  # hand over any request events before counting them
    assert writes == []


def test_a_list_takes_an_item_and_gives_it_back(ui_shell, ui_page):
    form = _settings(ui_page)
    tags = _field(form, "tags")
    tags.get_by_role("textbox").fill("blue sky")
    tags.get_by_role("button", name="Add").click()
    expect(tags.locator("li.item")).to_have_text(["blue skyRemove"], timeout=SETTINGS_MS)
    assert _file(ui_shell)["tags"] == ["blue sky"]
    tags.get_by_role("button", name="Remove blue sky").click()
    expect(tags.locator("li.item")).to_have_count(0, timeout=SETTINGS_MS)
    assert _file(ui_shell).get("tags", []) == []


def test_a_section_whose_file_wont_parse_is_read_only_and_says_why(ui_shell, ui_page):
    path = ui_shell.config_file("fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text() if path.exists() else None
    path.write_text("limit = = 3\n")
    try:
        ui_page.goto("/m/ui/settings/fixture")
        section = ui_page.locator("#moxi-panel > moxi-ui-page section.card[data-module=fixture]")
        expect(section.locator(".problem")).to_contain_text("is not valid TOML", timeout=SETTINGS_MS)
        expect(section).to_contain_text("Fix: correct the file's TOML syntax")
        # Nothing in it takes input: `show` gives no keys for a file it can't read.
        expect(section.locator("[data-control]:enabled")).to_have_count(0)
    finally:
        if before is None:
            path.unlink()
        else:
            path.write_text(before)


def test_the_core_has_its_own_section(ui_page):
    ui_page.goto("/m/ui/settings")
    core = ui_page.locator("#moxi-panel > moxi-ui-page section.card", has_text="moxi (the core)")
    expect(core.locator("h2")).to_have_text("moxi (the core)", timeout=SETTINGS_MS)
    for module in ("ui", "fixture"):
        expect(ui_page.locator(f"moxi-ui-page section.card[data-module={module}]")).to_be_attached()


def _set_background(mode: str, theme: str = "plain") -> str:
    """The `bg` token of the colour set `theme` shows in `mode`, as the browser computes it."""
    tokens = json.loads(TOKENS.read_text())
    name = tokens["$themes"][theme][mode]
    value = tokens["bg"]["$extensions"]["moxi"]["sets"][name]
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (1, 3, 5)))


def test_theme_mode_picks_the_colour_set_on_the_root_and_inside_a_shadow_root(ui_shell, ui_contexts):
    light, dark = _set_background("light"), _set_background("dark")
    assert (light, dark) == ("rgb(250, 250, 248)", "rgb(21, 21, 21)")
    page = ui_contexts(color_scheme="light").pages[0]
    html = page.locator("html")
    form = _settings(page, "ui")
    # The select sits in <moxi-ui-form>'s shadow root, on --moxi-bg.
    select = form.locator("select[data-control='theme.mode']")

    def shows(colour: str) -> None:
        expect(html).to_have_css("background-color", colour, timeout=SETTINGS_MS)
        expect(select).to_have_css("background-color", colour)

    try:
        # system follows the browser's scheme, both ways.
        expect(html).to_have_attribute("data-theme", "system")
        shows(light)
        page.emulate_media(color_scheme="dark")
        shows(dark)
        page.emulate_media(color_scheme="light")
        shows(light)
        # dark fixes the dark set on a light browser, at once and after a reload.
        select.select_option("dark")
        expect(html).to_have_attribute("data-theme", "dark", timeout=SETTINGS_MS)
        shows(dark)
        assert _file(ui_shell, "ui")["theme"] == {"mode": "dark"}
        page.reload()
        expect(html).to_have_attribute("data-theme", "dark")
        form = _settings(page, "ui")
        select = form.locator("select[data-control='theme.mode']")
        shows(dark)
        # light fixes the light set on a dark browser, at once and after a reload.
        page.emulate_media(color_scheme="dark")
        select.select_option("light")
        expect(html).to_have_attribute("data-theme", "light", timeout=SETTINGS_MS)
        shows(light)
        page.reload()
        expect(html).to_have_attribute("data-theme", "light")
        form = _settings(page, "ui")
        select = form.locator("select[data-control='theme.mode']")
        shows(light)
    finally:
        status, _ = ui_shell.session().post("/m/ui/api/settings/unset",
                                            {"module": "ui", "key": "theme.mode"})
        assert status == 200


def test_theme_palette_switches_the_colours_on_the_root_and_inside_a_shadow_root(ui_shell, ui_contexts):
    plain = {mode: _set_background(mode) for mode in ("light", "dark")}
    neon = {mode: _set_background(mode, "midnight-neon") for mode in ("light", "dark")}
    assert neon == {"light": "rgb(246, 244, 241)", "dark": "rgb(10, 9, 24)"}
    page = ui_contexts(color_scheme="light").pages[0]
    html = page.locator("html")
    form = _settings(page, "ui")
    name = form.locator("select[data-control='theme.palette']")
    expect(name.locator("option")).to_have_text(["plain", "midnight-neon"])
    expect(name).to_have_value("plain")

    def shows(colour: str) -> None:
        expect(html).to_have_css("background-color", colour, timeout=SETTINGS_MS)
        # The select sits in <moxi-ui-form>'s shadow root, on --moxi-bg.
        expect(name).to_have_css("background-color", colour)

    try:
        expect(html).to_have_attribute("data-theme-name", "plain")
        shows(plain["light"])
        # The other theme shows at once, in the browser's scheme, both ways.
        name.select_option("midnight-neon")
        expect(html).to_have_attribute("data-theme-name", "midnight-neon", timeout=SETTINGS_MS)
        shows(neon["light"])
        page.emulate_media(color_scheme="dark")
        shows(neon["dark"])
        assert _file(ui_shell, "ui")["theme"] == {"palette": "midnight-neon"}
        # It stays after a reload, and under a fixed mode.
        page.reload()
        expect(html).to_have_attribute("data-theme-name", "midnight-neon")
        form = _settings(page, "ui")
        name = form.locator("select[data-control='theme.palette']")
        shows(neon["dark"])
        form.locator("select[data-control='theme.mode']").select_option("light")
        expect(html).to_have_attribute("data-theme", "light", timeout=SETTINGS_MS)
        expect(html).to_have_attribute("data-theme-name", "midnight-neon")
        shows(neon["light"])
        # Back to the default theme, the mode kept.
        name.select_option("plain")
        expect(html).to_have_attribute("data-theme-name", "plain", timeout=SETTINGS_MS)
        expect(html).to_have_attribute("data-theme", "light")
        shows(plain["light"])
    finally:
        for key in ("theme.palette", "theme.mode"):
            status, _ = ui_shell.session().post("/m/ui/api/settings/unset", {"module": "ui", "key": key})
            assert status == 200


CATALOG = [
    {"name": "model", "type": "string", "flag": "--model", "default": "opus", "doc": "The model"},
    {"name": "add_dir", "type": "path", "flag": "--add-dir", "repeatable": True, "value": ["/srv/a"]},
    {"name": "remote_control", "type": "string", "flag": "--remote-control", "optional_value": True},
    {"name": "prompt_file", "type": "path", "flag": "--prompt-file", "hidden": True},
    {"name": "director", "type": "string", "flag": "--director", "repeatable": True,
     "requires": "nosuch"},
    {"name": "session_id", "type": "string", "flag": "--session-id", "owned": True},
    {"name": "permission_mode", "type": "enum", "values": ["default", "plan"],
     "default": "default", "flag": "--permission-mode", "posture": True},
]


def test_the_form_in_submit_mode_takes_catalog_rows(ui_page, ui_width_check):
    ui_page.evaluate("""(fields) => {
      const form = document.createElement("moxi-ui-form");
      form.id = "launch";
      form.mode = "submit";
      form.submitLabel = "Launch";
      form.fields = fields;
      form.addEventListener("moxi-submit", (event) => { window.__submitted = event.detail.values; });
      document.getElementById("moxi-panel").replaceChildren(form);
    }""", CATALOG)
    form = ui_page.locator("#launch")
    expect(form.locator("[data-control=model]")).to_have_value("opus")
    # An owned row is not offered.
    expect(form.locator("[data-control=session_id]")).to_have_count(0)
    # A repeatable row is a list with Add and Remove.
    add_dir = _field(form, "add_dir")
    expect(add_dir.locator("li.item")).to_have_count(1)
    add_dir.get_by_role("textbox").fill("/srv/b")
    add_dir.get_by_role("button", name="Add").click()
    expect(add_dir.locator("li.item")).to_have_count(2)
    add_dir.get_by_role("button", name="Remove /srv/a").click()
    expect(add_dir.locator("li.item")).to_have_count(1)
    # An optional value is a switch plus a text box, the box off until the switch is on.
    remote = _field(form, "remote_control")
    value = remote.locator("[data-control=remote_control]")
    expect(value).to_be_disabled()
    remote.get_by_role("checkbox").check()
    expect(value).to_be_enabled()
    value.fill("here")
    # A hidden row is under More.
    more = form.locator("details")
    expect(more.locator("summary")).to_have_text("More")
    expect(more.locator("[data-control=prompt_file]")).to_be_hidden()
    more.locator("summary").click()
    expect(more.locator("[data-control=prompt_file]")).to_be_visible()
    # A row that needs an absent module is off and says which.
    director = _field(form, "director")
    expect(director.get_by_role("textbox")).to_be_disabled()
    expect(director).to_contain_text("works only with the nosuch module, which is not installed")
    expect(_field(form, "permission_mode")).to_contain_text("permission posture")
    ui_width_check(ui_page)
    form.get_by_role("button", name="Launch").click()
    ui_page.wait_for_function("window.__submitted !== undefined")
    assert ui_page.evaluate("window.__submitted") == {
        "model": "opus", "add_dir": ["/srv/b"], "remote_control": "here", "prompt_file": None,
        "permission_mode": "default"}
