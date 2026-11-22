"""The Settings page in a browser: the list of modules and one module's settings at a
time, each type's control, a changed key's default and Reset, More, the foot, the
addresses the page writes for itself, a section that can't be read, a key the shell was
started with locked, copying a key, the page kept across the shell's redraw; changes held
until Save, each type's edit saved to the file, a refusal beside its row, ui's theme keys
applied at once, the asks when the page is left with changes held; Find, its results by
module with a Save line per group, and the changed view; the questions a person brings
(light or dark, what they changed, when a cache is near, where transcripts are read, what
needs a module the install lacks); the fixture with page fields: labels, units, a pick list,
More's words, a sentence for a key another page edits, a change that can hurt confirmed or
gone back from; a value changed behind the page, and a change held while the shell sends the
modules again; ui's Phone group on a gateway-mode shell, with its phones, Activity, a revoke,
the pairing code a phone redeems, a stale phone, Phone access read-only, and no Gateway
section left in the modules view; the small install; each width and theme, with no heading
and colour only on Save and on a change that can hurt; and `<moxi-ui-form>` in submit mode
with catalog rows."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
from moxi import modules
from moxi_dev.testing import PROC_WAIT_S, wait_for
from moxi_ui.testing.browser import copied, ready_copying
from playwright.sync_api import expect

import fixture_kit

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

LOCKED = "Locked for this run of the shell."
TOKENS = Path(__file__).resolve().parents[1] / "web" / "tokens.json"
# A clock time as the Save line words it, in the browser's own form.
CLOCK = r"\d{1,2}:\d{2}(?:\s?[AP]M)?"


def _view(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-settings")


def _settings(page, route="settings/fixture"):
    page.goto(f"/m/ui/{route}")
    view = _view(page)
    expect(view.locator("[data-part=foot]")).to_be_visible()
    return view


def _row(view, name):
    return view.locator(f".set[data-key='{name}']")


def _field(form, name):
    return form.locator(f".field[data-name='{name}']")


def _list(view):
    return view.locator("[data-part=list] a[data-list]")


def _save_line(view):
    return view.locator("[data-part=save]")


def _held(view, name):
    return _row(view, name).locator("[data-line=held]")


def _file(shell, module="fixture") -> dict:
    path = shell.config_file(module)
    return tomllib.loads(path.read_text()) if path.exists() else {}


def _saves(page) -> list:
    """The bodies the page posts to `api/settings/save`, as they go."""
    sent: list = []
    page.on("request", lambda request: sent.append(request.post_data_json)
            if request.url.endswith("/m/ui/api/settings/save") else None)
    return sent


def _save(view, words):
    """Saves the module shown and waits for the Save line to say `words` (a pattern)."""
    _save_line(view).get_by_role("button", name="Save", exact=True).click()
    expect(_save_line(view).locator("[data-line=save]")).to_have_text(re.compile(words))


def test_each_type_is_drawn_with_its_own_control(ui_page):
    view = _settings(ui_page)
    pane = view.locator("[data-part=pane][data-module=fixture]")
    expect(pane).to_be_visible()
    for name in ("greeting", "limit", "workers", "ratio", "folder"):
        expect(view.locator(f"input[type=text][data-control={name}]")).to_be_editable()
    expect(view.locator("input[data-control=greeting]")).to_have_value("hello")
    expect(view.locator("input[data-control=limit]")).to_have_attribute("inputmode", "numeric")
    expect(view.locator("input[data-control=limit]")).to_have_value("10")
    expect(view.locator("input[data-control=ratio]")).to_have_attribute("inputmode", "decimal")
    expect(view.locator("input[data-control=folder]")).to_have_attribute("placeholder", "an absolute path")
    expect(view.locator("[role=switch][data-control=loud]")).to_have_attribute("aria-checked", "false")
    colour = view.locator("[role=radiogroup][data-control=colour] [role=radio]")
    expect(colour).to_have_text(["red", "green", "blue"])
    expect(colour.and_(view.locator("[aria-checked=true]"))).to_have_text("green")
    tags = _row(view, "tags")
    expect(tags.locator("[data-line=none]")).to_have_text("none")
    expect(tags.get_by_role("button", name="Add")).to_be_visible()
    # A table that is no switch table is behind Edit; a switch table is one switch per entry.
    expect(_row(view, "extra").get_by_role("button", name="Edit")).to_be_visible()
    expect(view.locator("[role=switch][data-control='rows.backup']")).to_have_attribute("aria-checked", "false")
    # A key whose module is absent is not drawn.
    expect(_row(view, "peer")).to_have_count(0)
    # With no page fields: the key in the label's place, the doc as the line, the mark as a
    # sentence only where it does not apply at once, and no More.
    expect(_row(view, "limit").locator("[data-line=key]")).to_have_text("limit")
    expect(_row(view, "limit").locator("[data-line=label]")).to_have_count(0)
    expect(_row(view, "limit").locator("[data-line=line]")).to_have_text(["An int read at each use."])
    expect(_row(view, "workers").locator("[data-line=line]")).to_have_text(
        ["An int read when the page server starts.", "Applies at the next start."])
    expect(view.locator("[data-part=more]")).to_have_count(0)
    expect(view.locator("[data-line=default]")).to_have_count(0)
    foot = view.locator("[data-part=foot]")
    expect(foot.locator("[data-line=file]")).to_have_text(re.compile(r"^Kept in ~/.+/fixture/config\.toml$"))
    expect(foot.get_by_role("link", name="Fixture")).to_have_attribute("href", "/m/fixture/")


def test_a_changed_key_shows_its_default_and_the_list_counts_it(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    assert shell.moxi("config", "set", "fixture", "limit", "12").returncode == 0
    page = ui_contexts(shell).pages[0]
    view = _settings(page)
    limit = _row(view, "limit")
    expect(view.locator("input[data-control=limit]")).to_have_value("12")
    expect(limit.locator("[data-line=default]")).to_have_text(re.compile(r"^default 10\s*·\s*Reset$"))
    expect(_list(view).and_(page.locator("[data-list=fixture]"))).to_have_text(re.compile(r"^fixture\s*1$"))
    expect(view.locator("[data-part=list] [data-line=changed]")).to_have_text("1 changed")


def test_the_list_holds_the_core_ui_and_the_rest_and_settings_opens_the_core(ui_shell, ui_page):
    view = _settings(ui_page, "settings")
    names = _list(view)
    expect(names.first).to_have_text("moxi")
    texts = [name.strip() for name in names.all_inner_texts()]
    assert texts[:2] == ["moxi", "ui"] and "fixture" in texts and texts[2:] == sorted(texts[2:])
    expect(names.and_(ui_page.locator("[aria-current=page]"))).to_have_text("moxi")
    pane = view.locator("[data-part=pane][data-module=moxi]")
    skills = _row(view, "skills.harnesses")
    expect(skills.locator("[data-line=label]")).to_have_text("Skills are placed for")
    expect(skills.locator("[data-line=key]")).to_have_text("skills.harnesses")
    # The core's keys behind More: named, shown and hidden, the address following.
    more = view.locator("[data-part=more]")
    expect(more.locator("[data-line=more]")).to_have_text("2 settings: how skills are placed, harness folders")
    expect(_row(view, "skills.form")).to_have_count(0)
    more.get_by_role("button", name="Show").click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/moxi/more")
    expect(_row(view, "skills.form").locator("[data-line=label]")).to_have_text("How skills are placed")
    more.get_by_role("button", name="Hide").click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings")
    expect(_row(view, "skills.form")).to_have_count(0)
    expect(pane.locator("[data-part=foot] [data-line=file]")).to_have_text(
        re.compile(r"^Kept in ~/.+/moxi/config\.toml$"))
    expect(pane.locator("[data-part=foot] a")).to_have_count(0)
    # ui's foot names its Modules page.
    names.and_(ui_page.locator("[data-list=ui]")).click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/ui")
    expect(view.locator("[data-part=foot]").get_by_role("link", name="Modules")).to_have_attribute(
        "href", "/m/ui/modules")


# Logs each policy violation as a console error.
_POLICY = """document.addEventListener("securitypolicyviolation", (event) => {
  console.error(`policy: ${event.violatedDirective} ${event.blockedURI}`);
});"""


def _side_by_side(row):
    """The core's harness folders drawn as a table: its column heads on one line, left to right."""
    heads = row.get_by_role("table").locator("[role=columnheader]")
    expect(heads).to_have_text(["name", "skills", "agents"])
    boxes = [head.bounding_box() for head in heads.all()]
    assert all(abs(box["y"] - boxes[0]["y"]) < 1 for box in boxes), boxes
    assert boxes[0]["x"] < boxes[1]["x"] < boxes[2]["x"], boxes


def test_a_list_of_tables_is_drawn_as_a_table_wherever_it_is_drawn(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    path = shell.config_file("moxi")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('harnesses = [{name = "claude", skills = "", agents = ""}]\n')
    page = ui_contexts(shell).pages[0]
    seen: list[str] = []
    page.on("console", lambda msg: seen.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: seen.append(str(err)))
    page.add_init_script(_POLICY)
    # Shown by More.
    view = _settings(page, "settings")
    view.locator("[data-part=more]").get_by_role("button", name="Show").click()
    _side_by_side(_row(view, "harnesses"))
    # Found.
    _all_read(view)
    _find(view, "harness folders")
    _side_by_side(_group(view, "moxi").locator(".set[data-key=harnesses]"))
    # In the changed view.
    view = _settings(page, "settings")
    changed = view.locator("[data-part=list] [data-line=changed]")
    expect(changed).to_have_text("1 changed", timeout=30_000)
    changed.click()
    _side_by_side(_group(view, "moxi").locator(".set[data-key=harnesses]"))
    # Opened at More's address.
    _side_by_side(_row(_settings(page, "settings/moxi/more"), "harnesses"))
    assert seen == []


def test_the_page_writes_its_own_address_and_back_leaves_it(ui_shell, ui_page):
    ui_page.locator("#moxi-settings").click()
    view = _view(ui_page)
    expect(view.locator("[data-part=pane][data-module=moxi] [data-part=foot]")).to_be_visible()
    ui_page.evaluate("document.querySelector('#moxi-panel > moxi-ui-page').dataset.placed = 'first'")
    _list(view).and_(ui_page.locator("[data-list=fixture]")).click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/fixture")
    expect(view.locator("[data-part=pane][data-module=fixture] [data-part=foot]")).to_be_visible()
    # The shell did not place the page again for the address the page wrote.
    expect(ui_page.locator("#moxi-panel > moxi-ui-page[data-placed=first]")).to_be_attached()
    # The foot's link to the module's page moves through the shell.
    view.locator("[data-part=foot]").get_by_role("link", name="Fixture").click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/fixture/")
    expect(ui_page.locator("#moxi-panel > moxi-fixture-page")).to_be_attached()
    ui_page.go_back()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/fixture")
    expect(view.locator("[data-part=pane][data-module=fixture]")).to_be_visible()
    # Back from the page leaves it: the address it wrote replaced its entry.
    ui_page.go_back()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/")


def test_an_address_naming_no_listed_module_opens_the_core_and_says_so(ui_page):
    view = _settings(ui_page, "settings/nosuch")
    expect(view.locator("[data-part=pane][data-module=moxi]")).to_be_visible()
    expect(view.locator("[data-line=missing]")).to_have_text("nosuch has no settings here.")
    expect(_list(view).and_(ui_page.locator("[aria-current=page]"))).to_have_text("moxi")


def test_a_section_whose_file_wont_parse_says_why_in_the_pane(ui_shell, ui_page):
    path = ui_shell.config_file("fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.read_text() if path.exists() else None
    path.write_text("limit = = 3\n")
    try:
        ui_page.goto("/m/ui/settings/fixture")
        view = _view(ui_page)
        said = view.locator("[data-part=unreadable]")
        expect(said.locator("[data-line=unreadable]")).to_contain_text("fixture's settings can't be read: ")
        expect(said.locator("[data-line=unreadable]")).to_contain_text("is not valid TOML")
        expect(said.locator("[data-line=fix]")).to_contain_text("Fix: correct the file's TOML syntax")
        expect(said.get_by_role("button", name="Copy")).to_be_visible()
        expect(view.locator("[data-part=list] [data-line=unreadable]")).to_have_text("can't be read")
        expect(view.locator("[data-part=foot] [data-line=file]")).to_contain_text("fixture/config.toml")
        # No rows: nothing in it takes input.
        expect(view.locator(".set")).to_have_count(0)
    finally:
        if before is None:
            path.unlink()
        else:
            path.write_text(before)


def test_a_click_on_a_key_copies_it(ui_page):
    ready_copying(ui_page.context)
    view = _settings(ui_page)
    key = _row(view, "limit").locator("[data-line=key]")
    key.click()
    expect(_row(view, "limit").locator("[data-line=copied]")).to_have_text("Copied")
    assert copied(ui_page) == "limit"
    expect(_row(view, "limit").locator("[data-line=copied]")).to_have_count(0, timeout=4000)


def test_a_change_is_held_until_save_and_reset_holds_the_default(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell).pages[0]
    sent = _saves(page)
    view = _settings(page)
    box = view.locator("input[data-control=limit]")
    line = _save_line(view).locator("[data-line=save]")
    expect(line).to_have_text("Nothing to save")
    expect(_save_line(view).get_by_role("button", name="Save")).to_be_disabled()
    box.fill("12")
    expect(_held(view, "limit")).to_have_text(re.compile(r"^not saved · was 10\s*·\s*Undo$"))
    expect(_row(view, "limit")).to_have_attribute("data-held", "")
    expect(line).to_have_text("1 not saved · limit")
    expect(view.locator("[data-part=list] [data-line=unsaved]")).to_have_text("1 not saved")
    # Nothing is written until Save.
    assert "limit" not in _file(shell)
    # Undo gives the row back; Discard drops every change of the module.
    _held(view, "limit").get_by_role("button", name="Undo").click()
    expect(box).to_have_value("10")
    expect(line).to_have_text("Nothing to save")
    box.fill("13")
    view.locator("input[data-control=workers]").fill("4")
    expect(line).to_have_text("2 not saved · limit, workers")
    _save_line(view).get_by_role("button", name="Discard").click()
    expect(line).to_have_text("Nothing to save")
    expect(box).to_have_value("10")
    expect(view.locator("[data-part=list] [data-line=unsaved]")).to_have_count(0)
    # Saved: the file holds it, the row reads it back with its default.
    box.fill("12")
    _save(view, rf"^Saved {CLOCK} · limit applies now$")
    assert _file(shell)["limit"] == 12
    expect(box).to_have_value("12")
    expect(_row(view, "limit").locator("[data-line=default]")).to_have_text(re.compile(r"^default 10\s*·\s*Reset$"))
    # Reset holds the default like any change, and Save unsets the key.
    _row(view, "limit").get_by_role("button", name="Reset").click()
    expect(box).to_have_value("10")
    expect(_held(view, "limit")).to_have_text(re.compile(r"^not saved · was 12\s*·\s*Undo$"))
    _save(view, rf"^Saved {CLOCK} · limit applies now$")
    assert "limit" not in _file(shell)
    expect(_row(view, "limit").locator("[data-line=default]")).to_have_count(0)
    assert [(body["keys"], body["values"], body["resets"], body["was"]) for body in sent] == [
        (["limit"], ["12"], [False], ["10"]), (["limit"], [""], [True], ["12"])]


def test_a_start_key_is_saved_with_its_mark(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    view = _settings(ui_contexts(shell).pages[0])
    view.locator("input[data-control=workers]").fill("3")
    _save(view, rf"^Saved {CLOCK} · workers applies at the next start$")
    assert _file(shell)["workers"] == 3


def test_the_entry_only_the_timer_names_is_off_and_turning_it_on_saves_it(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    view = _settings(ui_contexts(shell).pages[0])
    switch = view.locator("[role=switch][data-control='rows.backup']")
    entry = view.locator(".entry[data-entry='rows.backup']")
    expect(switch).to_have_attribute("aria-checked", "false")
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(entry.locator("[data-line=held]")).to_have_text(re.compile(r"^not saved · was off\s*·\s*Undo$"))
    _save(view, rf"^Saved {CLOCK} · rows backup applies now$")
    assert _file(shell)["rows"] == {"backup": True}
    expect(switch).to_have_attribute("aria-checked", "true")
    expect(entry.locator("[data-line=held]")).to_have_count(0)
    switch.click()
    _save(view, rf"^Saved {CLOCK}")
    assert _file(shell)["rows"] == {"backup": False}


def test_a_value_the_server_refuses_says_why_beside_its_row_and_the_rest_is_saved(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    view = _settings(ui_contexts(shell).pages[0])
    extra = _row(view, "extra")
    extra.get_by_role("button", name="Edit").click()
    extra.locator("textarea").fill("not = [valid")
    view.locator("input[data-control=workers]").fill("3")
    _save(view, rf"^1 of 2 saved at {CLOCK} · extra was refused: it says why beside the field$")
    note = extra.locator("[data-line=note]")
    expect(note).to_have_text(re.compile(r"^Not saved\. .+"))
    # The refused row keeps what was typed, and says what the file still holds.
    expect(extra.locator("textarea")).to_have_value("not = [valid")
    expect(_held(view, "extra")).to_have_text(re.compile(r"^still none\s*·\s*Undo$"))
    expect(_held(view, "workers")).to_have_count(0)
    assert _file(shell) == {"workers": 3}
    expect(view.locator("[data-part=list] [data-line=unsaved]")).to_have_text("1 not saved")
    # A table's TOML as one value is saved.
    extra.locator("textarea").fill("{colour = \"red\"}")
    _save(view, rf"^Saved {CLOCK} · extra applies now$")
    assert _file(shell)["extra"] == {"colour": "red"}


def test_a_list_takes_an_item_and_gives_it_back(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    view = _settings(ui_contexts(shell).pages[0])
    tags = _row(view, "tags")
    tags.get_by_role("button", name="Add").click()
    tags.get_by_role("textbox", name="Add to tags").fill("sky")
    tags.get_by_role("textbox", name="Add to tags").press("Enter")
    expect(tags.locator("[data-item]")).to_have_text(["sky×"])
    expect(_held(view, "tags")).to_have_text(re.compile(r"^not saved · was none\s*·\s*Undo$"))
    _save(view, rf"^Saved {CLOCK} · tags applies now$")
    assert _file(shell)["tags"] == ["sky"]
    tags.get_by_role("button", name="Remove sky").click()
    expect(tags.locator("[data-line=none]")).to_have_text("none")
    _save(view, rf"^Saved {CLOCK} · tags applies now$")
    assert _file(shell).get("tags", []) == []


def _set_background(mode: str, theme: str = "plain") -> str:
    """The `bg` token of the colour set `theme` shows in `mode`, as the browser computes it."""
    tokens = json.loads(TOKENS.read_text())
    name = tokens["$themes"][theme][mode]
    value = tokens["bg"]["$extensions"]["moxi"]["sets"][name]
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (1, 3, 5)))


def test_theme_mode_picks_the_colour_set_on_the_root_and_inside_a_shadow_root(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    light, dark = _set_background("light"), _set_background("dark")
    assert (light, dark) == ("rgb(250, 250, 248)", "rgb(21, 21, 21)")
    page = ui_contexts(shell, color_scheme="light").pages[0]
    html = page.locator("html")
    view = _settings(page, "settings/ui")
    # The Save line sits in the page's shadow root, on --moxi-bg.
    inside = _save_line(view)

    def shows(colour: str) -> None:
        expect(html).to_have_css("background-color", colour)
        expect(inside).to_have_css("background-color", colour)

    def pick(mode: str) -> None:
        view.locator(f"[data-control='theme.mode'] [role=radio][data-value={mode}]").click()

    # system follows the browser's scheme, both ways.
    expect(html).to_have_attribute("data-theme", "system")
    shows(light)
    page.emulate_media(color_scheme="dark")
    shows(dark)
    page.emulate_media(color_scheme="light")
    shows(light)
    # dark fixes the dark set on a light browser once saved, and after a reload; held, it
    # changes nothing.
    pick("dark")
    expect(html).to_have_attribute("data-theme", "system")
    _save(view, rf"^Saved {CLOCK} · Light or dark applies now$")
    expect(html).to_have_attribute("data-theme", "dark")
    shows(dark)
    assert _file(shell, "ui")["theme"] == {"mode": "dark"}
    page.reload()
    expect(html).to_have_attribute("data-theme", "dark")
    view = _settings(page, "settings/ui")
    inside = _save_line(view)
    shows(dark)
    # light fixes the light set on a dark browser, at once and after a reload.
    page.emulate_media(color_scheme="dark")
    pick("light")
    _save(view, rf"^Saved {CLOCK}")
    expect(html).to_have_attribute("data-theme", "light")
    shows(light)
    page.reload()
    expect(html).to_have_attribute("data-theme", "light")
    view = _settings(page, "settings/ui")
    inside = _save_line(view)
    shows(light)


def test_theme_palette_switches_the_colours_on_the_root_and_inside_a_shadow_root(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    plain = {mode: _set_background(mode) for mode in ("light", "dark")}
    neon = {mode: _set_background(mode, "midnight-neon") for mode in ("light", "dark")}
    assert neon == {"light": "rgb(246, 244, 241)", "dark": "rgb(10, 9, 24)"}
    page = ui_contexts(shell, color_scheme="light").pages[0]
    html = page.locator("html")
    view = _settings(page, "settings/ui")
    name = view.locator("select[data-control='theme.palette']")
    expect(name.locator("option")).to_have_text(["Plain", "Midnight neon"])
    expect(name).to_have_value("plain")

    def shows(colour: str) -> None:
        expect(html).to_have_css("background-color", colour)
        # The Save line sits in the page's shadow root, on --moxi-bg.
        expect(_save_line(view)).to_have_css("background-color", colour)

    expect(html).to_have_attribute("data-theme-name", "plain")
    shows(plain["light"])
    # The other theme shows once saved, in the browser's scheme, both ways.
    name.select_option("midnight-neon")
    _save(view, rf"^Saved {CLOCK} · Look applies now$")
    expect(html).to_have_attribute("data-theme-name", "midnight-neon")
    shows(neon["light"])
    page.emulate_media(color_scheme="dark")
    shows(neon["dark"])
    assert _file(shell, "ui")["theme"] == {"palette": "midnight-neon"}
    # It stays after a reload, and under a fixed mode.
    page.reload()
    expect(html).to_have_attribute("data-theme-name", "midnight-neon")
    view = _settings(page, "settings/ui")
    name = view.locator("select[data-control='theme.palette']")
    shows(neon["dark"])
    view.locator("[data-control='theme.mode'] [role=radio][data-value=light]").click()
    _save(view, rf"^Saved {CLOCK}")
    expect(html).to_have_attribute("data-theme", "light")
    expect(html).to_have_attribute("data-theme-name", "midnight-neon")
    shows(neon["light"])
    # Back to the default theme, the mode kept.
    name.select_option("plain")
    _save(view, rf"^Saved {CLOCK}")
    expect(html).to_have_attribute("data-theme-name", "plain")
    expect(html).to_have_attribute("data-theme", "light")
    shows(plain["light"])


def test_changes_are_kept_across_modules_and_leaving_asks_in_the_save_line(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell).pages[0]
    # Opened from Home inside the one document, so Back stays in it.
    page.locator("#moxi-settings").click()
    view = _view(page)
    _list(view).and_(page.locator("[data-list=fixture]")).click()
    box = view.locator("input[data-control=limit]")
    box.fill("12")
    # Moving between modules keeps the change and does not ask.
    _list(view).and_(page.locator("[data-list=ui]")).click()
    expect(page).to_have_url(shell.url + "/m/ui/settings/ui")
    expect(view.locator("[data-part=list] a.u")).to_have_text("fixture")
    _list(view).and_(page.locator("[data-list=fixture]")).click()
    expect(box).to_have_value("12")
    # A link off the page asks in the Save line's place; Stay keeps everything.
    foot = view.locator("[data-part=foot]")
    foot.get_by_role("link", name="Fixture").click()
    ask = _save_line(view)
    expect(ask).to_have_attribute("data-state", "leave")
    expect(ask.locator("[data-line=save]")).to_have_text(
        re.compile(r"^Leave Settings with 1 change in fixture not saved\? limit\.\s*$"))
    expect(ask.get_by_role("button")).to_have_text(["Stay", "Leave without saving", "Save and leave"])
    ask.get_by_role("button", name="Stay").click()
    expect(page).to_have_url(shell.url + "/m/ui/settings/fixture")
    expect(box).to_have_value("12")
    # Back asks too, the page's address put back; Save and leave writes the change, then leaves.
    page.go_back()
    expect(ask).to_have_attribute("data-state", "leave")
    expect(page).to_have_url(shell.url + "/m/ui/settings/fixture")
    ask.get_by_role("button", name="Stay").click()
    expect(page).to_have_url(shell.url + "/m/ui/settings/fixture")
    foot.get_by_role("link", name="Fixture").click()
    ask.get_by_role("button", name="Save and leave").click()
    expect(page).to_have_url(shell.url + "/m/fixture/")
    assert _file(shell)["limit"] == 12
    # Changes in two modules: each named, and no save offered.
    page.go_back()
    view = _view(page)
    expect(view.locator("[data-part=pane][data-module=fixture] [data-part=foot]")).to_be_visible()
    view.locator("input[data-control=workers]").fill("5")
    _list(view).and_(page.locator("[data-list=ui]")).click()
    view.locator("[data-control='theme.mode'] [role=radio][data-value=dark]").click()
    view.locator("[data-part=foot]").get_by_role("link", name="Modules").click()
    ask = _save_line(view)
    expect(ask.locator("[data-line=save]")).to_contain_text("Leave Settings with 2 changes not saved?")
    expect(ask.locator("[data-line=leave]")).to_have_text(
        ["1 in fixture: workers.", "1 in ui: Light or dark.", "Save writes one module at a time."])
    expect(ask.get_by_role("button")).to_have_text(["Leave without saving", "Stay"])
    ask.get_by_role("button", name="Leave without saving").click()
    expect(page).to_have_url(shell.url + "/m/ui/modules")
    assert _file(shell) == {"limit": 12} and _file(shell, "ui") == {}


def _find(view, text):
    """Types `text` in the find field."""
    view.locator("[data-part=find] input").fill(text)


def _group(view, module):
    """A module's rows in Find's results or the changed view."""
    return view.locator(f"[data-part=group][data-module={module}]")


def _keys(rows) -> list:
    return rows.evaluate_all("(rows) => rows.map((row) => row.dataset.key)")


def test_find_groups_its_matches_by_module_and_a_change_there_has_its_own_save_line(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell).pages[0]
    page.locator("#moxi-settings").click()
    view = _view(page)
    _list(view).and_(page.locator("[data-list=fixture]")).click()
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=30_000)
    _find(view, "each use")
    head = view.locator("[data-part=found-head]")
    expect(head.locator("[data-line=count]")).to_have_text("2 settings in 1 module")
    expect(head.locator("[data-line=reads]")).to_have_text("names, keys, descriptions and values")
    # The results take the columns' place: the module and its tally at the left, its rows
    # at the right with the words that matched underlined, and no Save line at rest.
    expect(view.locator("[data-part=pane]")).to_have_count(0)
    group = _group(view, "fixture")
    expect(group.locator(".set")).to_have_count(2)
    assert _keys(group.locator(".set")) == ["greeting", "limit"]
    expect(view.locator("[data-group-of=fixture] [data-line=tally]")).to_have_text(re.compile(r"^2 of \d+$"))
    expect(_row(view, "limit").locator(".hit")).to_have_text(["each use"])
    expect(group.locator("[data-part=save]")).to_have_count(0)
    # A row is edited in place; its group gets a Save line for its module.
    view.locator("input[data-control=limit]").fill("13")
    line = group.locator("[data-part=save] [data-line=save]")
    expect(line).to_have_text("1 not saved in fixture · limit")
    # Leaving asks once, under the results.
    page.locator("a.moxi-brand").click()
    ask = view.locator("[data-part=save]")
    expect(ask).to_have_count(1)
    expect(ask).to_have_attribute("data-state", "leave")
    expect(ask.locator("[data-line=save]")).to_have_text(
        re.compile(r"^Leave Settings with 1 change in fixture not saved\? limit\.\s*$"))
    ask.get_by_role("button", name="Stay").click()
    expect(line).to_have_text("1 not saved in fixture · limit")
    group.locator("[data-part=save]").get_by_role("button", name="Save").click()
    expect(line).to_have_text(re.compile(rf"^Saved {CLOCK} · limit applies now$"))
    assert _file(shell)["limit"] == 13
    expect(_row(view, "limit").locator("[data-line=default]")).to_have_text(re.compile(r"^default 10\s*·\s*Reset$"))
    # Four rows a module, then the rest behind `Show them`.
    _find(view, "a")
    more = group.locator("[data-line=more-in]")
    expect(more).to_have_text(re.compile(r"^\d+ more in fixture · Show them$"))
    expect(group.locator(".set")).to_have_count(4)
    left = int(more.inner_text().split()[0])
    more.get_by_role("button", name="Show them").click()
    expect(group.locator(".set")).to_have_count(4 + left)
    expect(more).to_have_count(0)
    # Clear, or Escape, ends the search; the module's name opens it.
    view.locator("[data-part=find]").get_by_role("button", name="Clear").click()
    expect(view.locator("[data-part=pane][data-module=fixture]")).to_be_visible()
    expect(view.locator("[data-part=find] input")).to_have_value("")
    _find(view, "port")
    expect(_group(view, "ui")).to_be_visible()
    view.locator("[data-part=find] input").press("Escape")
    expect(view.locator("[data-part=pane][data-module=fixture]")).to_be_visible()
    _find(view, "port")
    view.locator("[data-group-of=ui] a").click()
    expect(page).to_have_url(shell.url + "/m/ui/settings/ui")
    expect(view.locator("[data-part=pane][data-module=ui]")).to_be_visible()
    expect(view.locator("[data-part=find] input")).to_have_value("")


def test_a_search_that_matches_nothing_says_so_beside_the_list(ui_shell, ui_page):
    view = _settings(ui_page)
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=30_000)
    _find(view, "zzqx")
    expect(view.locator("[data-part=found-head] [data-line=count]")).to_have_text("No setting matches “zzqx”")
    expect(view.locator("[data-line=help]")).to_have_text(
        "Search reads every installed module's settings. Pick a module at the left to see all of its settings.")
    expect(view.locator("[data-part=list] a[aria-current]")).to_have_count(0)
    _list(view).and_(ui_page.locator("[data-list=ui]")).click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/ui")
    expect(view.locator("[data-part=pane][data-module=ui]")).to_be_visible()
    expect(view.locator("[data-part=find] input")).to_have_value("")


def test_the_changed_view_lists_each_changed_setting_by_module_and_resets_one_there(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    for args in (("fixture", "limit", "12"), ("fixture", "workers", "3"), ("ui", "idle_s", "1200")):
        assert shell.moxi("config", "set", *args).returncode == 0
    page = ui_contexts(shell).pages[0]
    page.locator("#moxi-settings").click()
    view = _view(page)
    _list(view).and_(page.locator("[data-list=fixture]")).click()
    changed = view.locator("[data-part=list] [data-line=changed]")
    expect(changed).to_have_text("3 changed", timeout=30_000)
    changed.click()
    expect(page).to_have_url(shell.url + "/m/ui/settings/changed")
    head = view.locator("[data-part=found-head]")
    count = head.locator("[data-line=count]")
    expect(count).to_have_text("3 settings changed from the default, in 2 modules")
    expect(head.get_by_role("link")).to_have_text("Back to fixture")
    expect(view.locator("[data-part=changed] [data-group-of] a")).to_have_text(["ui", "fixture"])
    expect(view.locator("[data-group-of=fixture] [data-line=tally]")).to_have_text(re.compile(r"^2 of \d+$"))
    group = _group(view, "fixture")
    expect(group.locator(".set")).to_have_count(2)
    assert _keys(group.locator(".set")) == ["limit", "workers"]
    expect(_row(view, "limit").locator("[data-line=default]")).to_have_text(re.compile(r"^default 10\s*·\s*Reset$"))
    # Reset holds the default; the group's own Save line writes it.
    _row(view, "limit").get_by_role("button", name="Reset").click()
    line = group.locator("[data-part=save] [data-line=save]")
    expect(line).to_have_text("1 not saved in fixture · limit")
    group.locator("[data-part=save]").get_by_role("button", name="Save").click()
    expect(line).to_have_text(re.compile(rf"^Saved {CLOCK} · limit applies now$"))
    assert "limit" not in _file(shell)
    expect(group.locator(".set")).to_have_count(1)
    expect(count).to_have_text("2 settings changed from the default, in 2 modules")
    head.get_by_role("link", name="Back to fixture").click()
    expect(page).to_have_url(shell.url + "/m/ui/settings/fixture")
    expect(view.locator("[data-part=pane][data-module=fixture]")).to_be_visible()
    # Opened at its address, it goes back to the core.
    page.goto("/m/ui/settings/changed")
    expect(view.locator("[data-part=found-head] a")).to_have_text("Back to moxi")


def _installed(*names: str) -> bool:
    """Whether this environment installs every module `names` names, as the full install does."""
    found = {module.name for module in modules.scan()}
    return all(name in found for name in names)


def _all_read(view):
    """Waits until every listed module's section has arrived."""
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=30_000)


# --- the questions a person brings ------------------------------------------------------------


def test_switching_the_pages_to_light_is_found_under_ui_and_applies_now(ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell, color_scheme="dark").pages[0]
    view = _settings(page)
    _all_read(view)
    _find(view, "light")
    mode = _group(view, "ui").locator(".set[data-key='theme.mode']")
    expect(mode.locator("[data-line=label]")).to_have_text("Light or dark")
    choices = mode.locator("[role=radio]")
    expect(choices).to_have_text(["follow the browser", "light", "dark"])
    # Unset, it reads the default, and says nothing of one; it applies now, so no mark.
    expect(choices.and_(page.locator("[aria-checked=true]"))).to_have_text("follow the browser")
    expect(mode.locator("[data-line=default]")).to_have_count(0)
    expect(mode.locator("[data-line=line]")).to_have_count(0)
    choices.and_(page.locator("[data-value=light]")).click()
    line = _group(view, "ui").locator("[data-part=save] [data-line=save]")
    expect(line).to_have_text("1 not saved in ui · Light or dark")
    _group(view, "ui").locator("[data-part=save]").get_by_role("button", name="Save").click()
    expect(line).to_have_text(re.compile(rf"^Saved {CLOCK} · Light or dark applies now$"))
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    assert _file(shell, "ui")["theme"] == {"mode": "light"}


def test_the_settings_changed_from_the_defaults_are_counted_by_module_and_listed_together(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    for args in (("moxi", "skills.form", "copy"), ("ui", "idle_s", "1200"), ("fixture", "limit", "12")):
        assert shell.moxi("config", "set", *args).returncode == 0, args
    page = ui_contexts(shell).pages[0]
    view = _settings(page, "settings")
    changed = view.locator("[data-part=list] [data-line=changed]")
    expect(changed).to_have_text("3 changed", timeout=30_000)
    for module in ("moxi", "ui", "fixture"):
        expect(_list(view).and_(page.locator(f"[data-list={module}]"))).to_have_text(re.compile(rf"^{module}\s*1$"))
    changed.click()
    expect(view.locator("[data-part=found-head] [data-line=count]")).to_have_text(
        "3 settings changed from the default, in 3 modules")
    expect(view.locator("[data-part=changed] [data-group-of] a")).to_have_text(["moxi", "ui", "fixture"])
    expect(_row(view, "skills.form").locator("[data-line=default]")).to_have_text(
        re.compile(r"^default auto\s*·\s*Reset$"))
    expect(_row(view, "idle_s").locator("[data-line=default]")).to_have_text(re.compile(r"^default .+\s*·\s*Reset$"))
    expect(_row(view, "limit").locator("[data-line=default]")).to_have_text(re.compile(r"^default 10\s*·\s*Reset$"))


@pytest.mark.skipif(not _installed("cache"), reason="the full install's question: cache is not installed")
def test_when_a_cache_counts_as_near_is_found_with_its_value_and_unit(ui_page):
    view = _settings(ui_page)
    _all_read(view)
    _find(view, "near")
    row = _group(view, "cache").locator(".set[data-key='segment.warn_before_s']")
    expect(row.locator("[data-line=label]")).to_have_text("The status line shows near for the last")
    expect(row.locator("input[data-control='segment.warn_before_s']")).to_have_value("900")
    expect(row.locator(".unit")).to_have_text("seconds")
    expect(row.locator("[data-line=line]")).to_have_text(["Of a cache's life, when the segment turns from warm to near."])


@pytest.mark.skipif(not _installed("sessions"), reason="sessions is not installed")
def test_where_claude_code_transcripts_are_read_from_is_found_and_says_its_default(ui_page):
    view = _settings(ui_page)
    _all_read(view)
    _find(view, "transcripts")
    row = _group(view, "sessions").locator(".set[data-key='claude.projects']")
    expect(row.locator("[data-line=label]")).to_have_text("Claude Code transcripts")
    expect(row.locator("input[data-control='claude.projects']")).to_have_value("")
    expect(row.locator("[data-line=line]")).to_have_text(["Not set: the folder Claude Code itself uses."])


@pytest.mark.skipif(not _installed("launcher", "slots", "comms"),
                    reason="the full install's question: launcher, slots or comms is not installed")
def test_on_the_full_install_every_key_that_needs_another_module_is_drawn(ui_page):
    from moxi.config import Schema

    schema = Schema.from_resource("moxi_launcher", "settings.toml")
    needing = [name for name, key in schema.keys.items() if key.needs and not key.retired and not key.elsewhere]
    assert len(needing) >= 7, needing
    view = _settings(ui_page, "settings/launcher/more")
    expect(view.locator("[data-part=pane][data-module=launcher]")).to_be_visible()
    for name in needing:
        expect(_row(view, name)).to_be_visible()
    # The one key whose module no install holds is the fixture's, and it is not drawn.
    view = _settings(ui_page)
    expect(_row(view, "peer")).to_have_count(0)


# --- the fixture with page fields -------------------------------------------------------------


@pytest.fixture(scope="module")
def paged_site(tmp_path_factory):
    """A site holding the fixture whose schema's copy carries page fields."""
    return fixture_kit.install(tmp_path_factory.mktemp("paged-site"), fields=True)


def test_page_fields_name_each_row_and_more_names_the_rest(ui_shells, ui_contexts, paged_site):
    shell = ui_shells(site=[paged_site])
    assert shell.moxi("config", "set", "fixture", "workers", "3").returncode == 0
    page = ui_contexts(shell).pages[0]
    view = _settings(page)
    # The common keys first, in the schema's order; then More.
    assert _keys(view.locator("[data-part=pane] .set")) == ["greeting", "limit", "loud", "colour", "tags"]
    greeting = _row(view, "greeting")
    expect(greeting.locator("[data-line=label]")).to_have_text("Greeting")
    expect(greeting.locator("[data-line=key]")).to_have_text("greeting")
    expect(greeting.locator("[data-line=line]")).to_have_text(["The word the page greets with."])
    limit = _row(view, "limit")
    expect(limit.locator("[data-line=label]")).to_have_text("Row limit")
    expect(limit.locator(".unit")).to_have_text("rows")
    # A pick list, its options in the choices' words.
    colour = view.locator("select[data-control=colour]")
    expect(colour.locator("option")).to_have_text(["warm red", "leaf green", "sky blue"])
    expect(colour).to_have_value("green")
    # A key another page edits, and one whose module is absent, are not drawn.
    expect(_row(view, "ratio")).to_have_count(0)
    expect(_row(view, "peer")).to_have_count(0)
    more = view.locator("[data-part=more]")
    expect(more.locator("[data-line=more]")).to_have_text("4 settings: server, switches, extra table · 1 changed")
    expect(_list(view).and_(page.locator("[data-list=fixture]"))).to_have_text(re.compile(r"^fixture\s*1$"))
    more.get_by_role("button", name="Show").click()
    expect(view.locator("[data-part=pane] .subh")).to_have_text(["Server", "Switches"])
    expect(_row(view, "workers").locator("[data-line=default]")).to_have_text(re.compile(r"^default 2\s*·\s*Reset$"))
    expect(view.locator("[role=switch][data-control='rows.backup']")).to_be_visible()
    # Find answers a search for the key another page edits with its sentence.
    _all_read(view)
    _find(view, "fixture ratio")
    expect(view.locator("[data-part=found-head] [data-line=count]")).to_have_text("No setting matches “fixture ratio”")
    expect(view.locator("[data-line=elsewhere]")).to_have_text(["The ratio is set on the Fixture page."])
    _find(view, "row limit")
    expect(_group(view, "fixture").locator(".set")).to_have_count(1)
    expect(view.locator("[data-line=elsewhere]")).to_have_count(0)


def test_a_change_that_can_hurt_asks_before_it_is_saved_and_go_back_keeps_it(
        ui_shells, ui_contexts, paged_site):
    shell = ui_shells(site=[paged_site])
    path = shell.config_file("fixture")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('tags = ["sky"]\n')
    view = _settings(ui_contexts(shell).pages[0])
    line = _save_line(view)
    loud = _row(view, "loud")
    view.locator("[role=switch][data-control=loud]").click()
    expect(loud).to_have_class(re.compile(r"\bdg\b"))
    expect(_held(view, "loud")).to_have_text(re.compile(r"^not saved · makes every read shout\s*·\s*Undo$"))
    # Save asks in the Save line's place; Go back writes nothing and keeps the change.
    line.get_by_role("button", name="Save").click()
    expect(line).to_have_attribute("data-state", "confirm")
    expect(line.locator("[data-line=save]")).to_have_text(
        "Turn on Loud? From its next read, the page shouts every line. Nothing else in this save can hurt.")
    expect(line.locator("[data-line=ask]")).to_have_text(["Turn on Loud?"])
    expect(line.get_by_role("button")).to_have_text(["Go back", "Turn on and save"])
    line.get_by_role("button", name="Go back").click()
    expect(line).to_have_attribute("data-state", "held")
    expect(line.locator("[data-line=save]")).to_have_text("1 not saved · Loud")
    assert _file(shell) == {"tags": ["sky"]}
    # A list's item removed, where removing can hurt: each act asked, one button for both.
    tags = _row(view, "tags")
    tags.get_by_role("button", name="Remove sky").click()
    expect(_held(view, "tags")).to_have_text(re.compile(r"^not saved · stops marking rows with this tag\s*·\s*Undo$"))
    line.get_by_role("button", name="Save").click()
    expect(line.locator("[data-line=ask]")).to_have_text(["Turn on Loud?", "Remove sky from Tags?"])
    expect(line.locator("[data-line=save]")).to_contain_text(
        "Remove sky from Tags? From its next read, the page no longer marks a row with this tag. "
        "Nothing else in this save can hurt.")
    line.get_by_role("button", name="Save anyway").click()
    expect(line.locator("[data-line=save]")).to_have_text(re.compile(rf"^Saved {CLOCK} · "))
    assert _file(shell) == {"loud": True, "tags": []}
    expect(_held(view, "loud")).to_have_count(0)


def test_a_value_changed_in_the_file_behind_the_page_is_not_overwritten_until_saved_again(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    view = _settings(ui_contexts(shell).pages[0])
    box = view.locator("input[data-control=limit]")
    box.fill("12")
    expect(_held(view, "limit")).to_have_text(re.compile(r"^not saved · was 10\s*·\s*Undo$"))
    assert shell.moxi("config", "set", "fixture", "limit", "14").returncode == 0
    _save(view, rf"^0 of 1 saved at {CLOCK} · limit was not saved: it says why beside the field$")
    expect(_row(view, "limit").locator("[data-line=note]")).to_have_text(
        "Not saved: it changed since this page read it, to 14. Save again to write yours.")
    # The row keeps what was typed; its `was` follows the file.
    expect(box).to_have_value("12")
    expect(_held(view, "limit")).to_have_text(re.compile(r"^not saved · was 14\s*·\s*Undo$"))
    assert _file(shell)["limit"] == 14
    _save(view, rf"^Saved {CLOCK} · limit applies now$")
    assert _file(shell)["limit"] == 12


# Marks the document with the fixture's version each time `ui.modules` arrives.
_MODULES_SEEN = """async () => {
  (await import("/m/ui/client.js")).subscribe("ui", "modules", (value) => {
    const row = (value?.modules ?? []).find((item) => item.name === "fixture");
    document.documentElement.dataset.fixtureVersion = row?.version ?? "";
  });
}"""


def test_a_change_held_when_the_shell_sends_the_modules_again_stays_and_the_page_is_kept(
        ui_shells, ui_contexts, tmp_path_factory):
    site = fixture_kit.install(tmp_path_factory.mktemp("modules-site"))
    shell = ui_shells(site=[site])
    page = ui_contexts(shell).pages[0]
    view = _settings(page)
    _all_read(view)
    box = view.locator("input[data-control=limit]")
    box.fill("12")
    expect(_save_line(view).locator("[data-line=save]")).to_have_text("1 not saved · limit")
    page.evaluate("document.querySelector('#moxi-panel > moxi-ui-page').dataset.placed = 'first'")
    page.evaluate(_MODULES_SEEN)
    html = page.locator("html")
    expect(html).to_have_attribute("data-fixture-version", "0.1.0")
    # A new version of the fixture: the shell's next snapshot sees it and sends `ui.modules`
    # again, to the page too.
    fixture_kit.set_version(site, "0.2.0")

    def seen_new() -> bool:
        status, value = shell.session().get("/m/ui/api/modules")
        return any(row["name"] == "fixture" and row.get("version") == "0.2.0" for row in value["modules"])

    wait_for(seen_new, PROC_WAIT_S, interval=0.2, what="the snapshot to see version 0.2.0")
    expect(html).to_have_attribute("data-fixture-version", "0.2.0")
    expect(page.locator("#moxi-panel > moxi-ui-page[data-placed=first]")).to_be_attached()
    expect(box).to_have_value("12")
    expect(_held(view, "limit")).to_have_text(re.compile(r"^not saved · was 10\s*·\s*Undo$"))
    expect(_save_line(view).locator("[data-line=save]")).to_have_text("1 not saved · limit")
    _save(view, rf"^Saved {CLOCK} · limit applies now$")
    assert _file(shell)["limit"] == 12


_SIGN_OUT = """async () => {
  const good = localStorage.getItem("moxi-ui-key");
  localStorage.setItem("moxi-ui-key", "A".repeat(43));
  await (await import("/m/ui/client.js")).api("ui", "api/modules");
  return good;
}"""


def _phone_shell(tmp_path_factory, fixture_site):
    """A gateway-mode shell with its gateway on at the bridge's address, and one phone
    paired with no passkey that signed in and read a page, as the audit log says."""
    from moxi_ui.gateway.audit import Audit
    from moxi_ui.gateway.devices import Devices
    from moxi_ui.testing.phone import gateway_shell

    shell = gateway_shell(tmp_path_factory.mktemp("gw"), [fixture_site])
    state = shell.state_dir("ui")
    device = Devices.of(state).add(name="Test phone", credential=None, public_key=None, sign_count=0,
                                   host="gw.moxi.test", provider="cloudflare", change="r1.1")
    writer = Audit(state)
    writer("signin", {"device": device.id, "session": "a" * 12})
    writer.request({"id": "r1", "method": "GET", "path": "/m/fixture/api/read", "status": 200,
                    "device": device.id}, signed_in=True)
    writer.close()
    return shell, device


def test_the_phone_group_shows_the_gateway_each_phone_its_activity_and_revokes_one(
        ui_contexts, tmp_path_factory, fixture_site):
    shell, device = _phone_shell(tmp_path_factory, fixture_site)
    try:
        status, state = shell.session().get("/m/ui/api/gateway/state")
        assert status == 200 and state["installed"] and state["switch"] and state["on"], state
        assert state["exposure"] == "internet", state
        page = ui_contexts(shell).pages[0]
        # The modules view holds no Gateway section: pairing is on Settings › ui.
        page.goto("/m/ui/modules")
        expect(page.locator("#moxi-panel moxi-ui-page .card[data-module=ui]")).to_be_visible()
        expect(page.locator("moxi-ui-gateway")).to_have_count(0)
        view = _settings(page, "settings/ui")
        pane = view.locator("[data-part=pane][data-module=ui]")
        expect(pane.locator(".subh[data-group=phone]")).to_have_text("Phone")
        # Phone access: a word and no control, where phones reach it, the command to copy.
        access = _row(view, "gateway.enabled")
        expect(access.locator("[data-line=label]")).to_have_text("Phone access")
        expect(access.locator("[data-line=word]")).to_have_text("on")
        expect(access.locator("button[role=switch]")).to_have_count(0)
        expect(access.locator("[data-line=access]")).to_have_text(
            f"At {state['host']}:{state['port']}." + (" Phones read only." if state["reads_only"] else ""))
        expect(access.locator("[data-line=terminal]")).to_have_text("Turned off at the terminal: moxi ui gateway disable")
        expect(access.get_by_role("button", name="Copy")).to_be_visible()
        # The command in the mono face, and Copy after it on its line.
        command = access.locator("[data-line=terminal] code")
        expect(command).to_have_text("moxi ui gateway disable")
        face = "el => getComputedStyle(el).fontFamily"
        assert command.evaluate(face) != access.locator("[data-line=terminal]").evaluate(face)
        assert _same_line(command, access.get_by_role("button", name="Copy"))
        # Phone port, as any key, with its mark; then the phone, after it.
        port = _row(view, "gateway.port")
        expect(port.locator("[data-line=label]")).to_have_text("Phone port")
        expect(view.locator("input[data-control='gateway.port']")).to_have_value(str(shell.gateway_port))
        expect(port.locator("[data-line=line]")).to_have_text(["Applies at the next start."])
        phone = view.locator(f".dev[data-device='{device.id}']")
        expect(phone.locator("[data-line=name]")).to_have_text("Test phone")
        expect(phone.locator("[data-line=device]")).to_have_text(
            re.compile(rf"^paired (today|yesterday) · last sign-in \1 {CLOCK} · no passkey$"))
        # ui's count holds its phone keys while the group shows.
        expect(_list(view).and_(page.locator("[data-list=ui]"))).to_have_text(re.compile(r"^ui\s*2$"))
        # Activity, tucked: the audit rows and the phone's sign-ins.
        activity = view.locator("[data-part=activity]")
        expect(activity).to_contain_text("Activity")
        activity.get_by_role("button", name="Show").click()
        log = view.locator("[data-part=log]")
        expect(log.locator("[data-line=signins]")).to_have_text(re.compile(r"^Test phone signed in .+\.$"))
        expect(log.locator("[data-line=row]").first).to_contain_text("path=/m/fixture/api/read")
        expect(log.locator("[data-line=row]").filter(has_text="signin")).to_have_count(1)
        # Revoke asks in place under the phone; Keep it leaves it paired.
        phone.get_by_role("button", name="Revoke").click()
        ask = view.locator("[data-part=revoke]")
        expect(ask).to_contain_text("Revoke Test phone? It is signed out within two seconds and must be paired again to come back.")
        # Its question and its act take the danger colour.
        danger = page.evaluate(_TOKENS)["danger"]
        colour = "el => getComputedStyle(el).color"
        assert ask.locator("b.w").evaluate(colour) == danger
        assert ask.get_by_role("button", name="Revoke Test phone").evaluate(colour) == danger
        ask.get_by_role("button", name="Keep it").click()
        expect(ask).to_have_count(0)
        phone.get_by_role("button", name="Revoke").click()
        ask.get_by_role("button", name="Revoke Test phone").click()
        expect(view.locator("[data-line=revoked]")).to_have_text("Revoked Test phone.")
        expect(view.locator("[data-line=no-phone]")).to_have_text("No phone is paired.")
        expect(phone).to_have_count(0)
        # Revoke waits for no Save.
        expect(_save_line(view).locator("[data-line=save]")).to_have_text("Nothing to save")
        # Pair a phone sits at the left, under the phones.
        assert _box(view.get_by_role("button", name="Pair a phone"))["x"] == pytest.approx(_box(pane)["x"], abs=1)
        # Pair a phone opens the code in place, at its own address, and Close closes it.
        view.get_by_role("button", name="Pair a phone").click()
        pairing = view.locator("[data-part=pair]")
        expect(pairing.locator("svg[aria-label='Pairing code']")).to_be_visible()
        expect(page).to_have_url(re.compile(r"/m/ui/settings/ui/pair$"))
        expect(pairing.locator("[data-line=scan]")).to_have_text(
            re.compile(rf"^Scan this with the phone's camera\. It works once, until {CLOCK}\.$"))
        expect(pairing.locator("[data-line=then]")).to_have_text(
            "The phone then asks for a name and a passkey. It will read Moxi's pages; it cannot launch, "
            "send or change a setting.")
        expect(pairing.get_by_role("button", name="Copy the link instead")).to_be_visible()
        view.get_by_role("button", name="Close").click()
        expect(pairing).to_have_count(0)
        expect(page).to_have_url(re.compile(r"/m/ui/settings/ui$"))
    finally:
        shell.stop()


def test_more_stays_open_when_the_shell_draws_the_page_again(ui_shell, ui_page):
    view = _settings(ui_page, "settings/ui")
    # Every section in, so no read is on its way while the sign-in drops.
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=30_000)
    view.locator("[data-part=more]").get_by_role("button", name="Show").click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/ui/more")
    ui_page.evaluate("document.querySelector('#moxi-panel > moxi-ui-page').dataset.placed = 'first'")
    # The sign-in drops and comes back (another tab stores the key): the shell draws its
    # panel again, a new element in the old one's place.
    key = ui_page.evaluate(_SIGN_OUT)
    expect(ui_page.locator("#moxi-notice")).to_be_visible()
    other = ui_page.context.new_page()
    other.goto(ui_shell.url + "/m/ui/modules")
    other.evaluate("(key) => localStorage.setItem('moxi-ui-key', key)", key)
    expect(ui_page.locator("#moxi-notice")).to_be_hidden()
    expect(ui_page.locator("#moxi-panel > moxi-ui-page[data-placed=first]")).to_have_count(0)
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/ui/more")
    expect(view.locator("[data-part=more]").get_by_role("button", name="Hide")).to_be_visible()
    expect(_row(view, "idle_s").locator("[data-line=label]")).to_have_text("Idle shutdown")


def test_the_saved_line_and_more_stay_when_the_shell_draws_the_page_after_a_save(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell).pages[0]
    view = _settings(page, "settings/ui")
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=30_000)
    view.locator("[data-part=more]").get_by_role("button", name="Show").click()
    view.locator("input[data-control=idle_s]").fill("1200")
    page.evaluate("document.querySelector('#moxi-panel > moxi-ui-page').dataset.placed = 'first'")
    # The sign-in drops and comes back while the change is held: the shell waits.
    key = page.evaluate(_SIGN_OUT)
    expect(page.locator("#moxi-notice")).to_be_visible()
    other = page.context.new_page()
    other.goto(shell.url + "/m/ui/modules")
    other.evaluate("(key) => localStorage.setItem('moxi-ui-key', key)", key)
    expect(page.locator("#moxi-notice")).to_be_hidden()
    expect(page.locator("#moxi-panel > moxi-ui-page[data-placed=first]")).to_be_attached()
    # Saved, the page lets go: the shell draws it again, and the new one says what was saved.
    _save(view, rf"^Saved {CLOCK} · Idle shutdown ")
    expect(page.locator("#moxi-panel > moxi-ui-page[data-placed=first]")).to_have_count(0)
    expect(_save_line(view).locator("[data-line=save]")).to_have_text(re.compile(rf"^Saved {CLOCK} · Idle shutdown "))
    expect(page).to_have_url(shell.url + "/m/ui/settings/ui/more")
    expect(view.locator("[data-part=more]").get_by_role("button", name="Hide")).to_be_visible()
    expect(view.locator("input[data-control=idle_s]")).to_have_value("1200")
    assert _file(shell, "ui")["idle_s"] == 1200


FORCED = """async (sent) => {
  const headers = {"Content-Type": "application/json", Accept: "application/json",
                   "X-Moxi-Key": localStorage.getItem("moxi-ui-key")};
  const out = [];
  for (const [what, body] of sent) {
    const answer = await fetch("/m/ui/api/settings/" + what, {
      method: "POST", headers, body: JSON.stringify(body), credentials: "same-origin"});
    out.push([answer.status, await answer.json()]);
  }
  return out;
}"""


def test_a_phone_paired_with_the_pages_code_closes_it_and_a_stale_phone_says_to_pair_again(
        ui_contexts, tmp_path_factory, fixture_site):
    from urllib.parse import urlsplit

    from moxi_ui.gateway.devices import Devices
    from moxi_ui.testing.phone import PhoneBridge, PhoneSession

    shell, _ = _phone_shell(tmp_path_factory, fixture_site)
    try:
        # A phone paired at an address the gateway has since left.
        old = Devices.of(shell.state_dir("ui")).add(
            name="Old phone", credential=None, public_key=None, sign_count=0, host="old.moxi.test",
            provider="cloudflare", change="r0.1")
        Devices.of(shell.state_dir("ui")).mark_stale(lambda device: device.id == old.id)
        page = ui_contexts(shell).pages[0]
        ready_copying(page.context)
        view = _settings(page, "settings/ui")
        stale = view.locator(f".dev[data-device='{old.id}'] [data-line=device]")
        expect(stale).to_have_text(re.compile(r" · the address changed; pair it again$"))
        # Its words wrap beside Revoke, which stays on the name's line.
        page.set_viewport_size({"width": 1280, "height": 900})
        device = view.locator(f".dev[data-device='{old.id}']")
        assert _same_line(device.locator("[data-line=name]"), device.get_by_role("button", name="Revoke"))
        view.get_by_role("button", name="Pair a phone").click()
        pairing = view.locator("[data-part=pair]")
        expect(pairing.locator("svg[aria-label='Pairing code']")).to_be_visible()
        pairing.get_by_role("button", name="Copy the link instead").click()
        expect(pairing.locator("[data-line=copied]")).to_have_text("Copied")
        code = urlsplit(copied(page)).fragment.removeprefix("c=")
        # The phone redeems the page's own code through the tunnel's stand-in.
        with PhoneBridge(shell.home, shell.gateway_port) as bridge:
            phone = PhoneSession(bridge)
            status, _, begun = phone.request("POST", "/api/pair/begin", {"code": code, "name": "New phone"})
            assert status == 200, begun
            status, _, done = phone.request("POST", "/api/pair/finish",
                                            {"pairing": begun["pairing"], "credential": None})
            assert status == 200, done
        expect(view.locator("[data-line=pair-note]")).to_have_text("Paired: New phone.")
        expect(pairing).to_have_count(0)
        expect(page).to_have_url(re.compile(r"/m/ui/settings/ui$"))
        expect(view.locator(".dev [data-line=name]")).to_contain_text(["New phone"])
    finally:
        shell.stop()


def test_phone_access_is_read_only_and_a_post_to_it_sent_past_the_page_is_refused(
        ui_contexts, tmp_path_factory, fixture_site):
    shell, _ = _phone_shell(tmp_path_factory, fixture_site)
    try:
        before = _file(shell, "ui")
        page = ui_contexts(shell).pages[0]
        view = _settings(page, "settings/ui")
        access = _row(view, "gateway.enabled")
        expect(access.locator("[data-line=word]")).to_have_text("on")
        expect(access.locator("[role=switch], input, select")).to_have_count(0)
        # Changed from its default, but turned on and off at the terminal: no Reset.
        expect(access.locator("[data-line=default]")).to_have_text("default off")
        answers = page.evaluate(FORCED, [
            ["set", {"module": "ui", "key": "gateway.enabled", "value": "false"}],
            ["unset", {"module": "ui", "key": "gateway.enabled"}],
            ["save", {"module": "ui", "keys": ["gateway.enabled"], "values": ["false"], "resets": [False],
                      "was": ["true"]}]])
        assert [status for status, _ in answers[:2]] == [422, 422], answers
        assert all("refused" in body for _, body in answers[:2]), answers
        assert answers[2][0] == 200 and [r["outcome"] for r in answers[2][1]["results"]] == ["refused"], answers
        assert _file(shell, "ui") == before
    finally:
        shell.stop()


# --- the small install, widths, themes ----------------------------------------------------------


def _box(locator) -> dict:
    box = locator.bounding_box()
    assert box is not None, locator
    return box


def _same_line(one, two) -> bool:
    """Whether two pieces sit on one line: their vertical middles within 4 px."""
    a, b = _box(one), _box(two)
    return abs((a["y"] + a["height"] / 2) - (b["y"] + b["height"] / 2)) < 4


def _right(locator) -> float:
    box = _box(locator)
    return box["x"] + box["width"]


@pytest.mark.skipif(not _installed("launcher", "turns"), reason="the full install's panes")
def test_a_control_sits_at_the_right_of_its_label_and_more_keeps_show_on_its_first_line(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site])
    page = ui_contexts(shell).pages[0]
    page.set_viewport_size({"width": 1280, "height": 900})
    # A text field sits on its label's line, out to the row's edge.
    view = _settings(page, "settings/launcher")
    fallback = _row(view, "relaunch.fallback")
    field = fallback.locator("input[data-control='relaunch.fallback']")
    assert _same_line(field, fallback.locator("[data-line=label]"))
    assert _right(field) == pytest.approx(_right(fallback), abs=1)
    # More's words wrap beside Show, which stays at the right of their first line.
    view = _settings(page, "settings/turns")
    more = view.locator("[data-part=more]")
    show = more.get_by_role("button", name="Show")
    assert _same_line(show, more.locator("b"))
    assert _right(show) == pytest.approx(_right(more), abs=1)
    # Look is its pick and a caret at the row's right, not a boxed list.
    view = _settings(page, "settings/ui")
    look = _row(view, "theme.palette")
    pick = look.locator("select[data-control='theme.palette']")
    assert pick.evaluate("el => getComputedStyle(el).appearance") == "none"
    expect(look.locator(".caret")).to_have_text("▾")
    assert _right(look.locator(".caret")) == pytest.approx(_right(look), abs=3)


def test_the_small_install_lists_the_core_and_ui_and_has_no_phone_group(ui_shells, ui_contexts):
    others = [found.name for found in modules.scan() if found.name != "ui"]
    shell = ui_shells(leave_out=others)
    status, state = shell.session().get("/m/ui/api/gateway/state")
    assert status == 200 and state["installed"] is False, state
    page = ui_contexts(shell).pages[0]
    view = _settings(page, "settings/ui")
    _all_read(view)
    expect(_list(view)).to_have_text(["moxi", re.compile(r"^ui")])
    expect(view.locator(".subh[data-group=phone]")).to_have_count(0)
    expect(view.locator("moxi-ui-phones")).to_have_count(0)
    view.locator("[data-part=more]").get_by_role("button", name="Show").click()
    expect(view.locator(".set[data-key^='gateway.']")).to_have_count(0)
    # Find never matches a phone key while the group is not shown.
    for text in ("gateway", "phone port", "8461"):
        _find(view, text)
        expect(view.locator("[data-part=found-head] [data-line=count]")).to_be_visible()
        expect(view.locator(".set[data-key^='gateway.']")).to_have_count(0)
    expect(view.locator("[data-part=found-head] [data-line=count]")).to_have_text("No setting matches “8461”")


# Each colour token as the browser computes it.
_TOKENS = """() => {
  const probe = document.createElement("span");
  document.body.append(probe);
  const tokens = {};
  for (const name of ["text", "text-dim", "text-faint", "accent", "on-accent", "warn", "danger"]) {
    probe.style.color = `var(--moxi-${name})`;
    tokens[name] = getComputedStyle(probe).color;
  }
  probe.remove();
  return tokens;
}"""

# Every colour the page's words show in, and the colours of the pieces that may carry one: a
# row whose change can hurt, Save, and the confirm's question and act while it asks.
_COLOURS = """() => {
  const root = document.querySelector("#moxi-panel > moxi-ui-page").shadowRoot.querySelector("moxi-ui-settings").shadowRoot;
  const seen = new Set();
  for (const el of root.querySelectorAll("*")) {
    const own = [...el.childNodes].some((node) => node.nodeType === 3 && node.textContent.trim() !== "");
    if (own && el.getClientRects().length > 0) seen.add(getComputedStyle(el).color);
  }
  const colour = (selector, what = "color") => getComputedStyle(root.querySelector(selector))[what];
  const shown = (selector, what = "color") => root.querySelector(selector) ? colour(selector, what) : null;
  return {seen: [...seen], harm: colour(".set.dg [data-line=label]"), held: colour(".set.dg [data-line=held] .w"),
          switch: colour(".set.dg [role=switch][aria-checked=true]", "backgroundColor"),
          save: shown("[data-part=save] .btn.acc", "backgroundColor"),
          ask: shown("[data-part=save] [data-line=ask]"), act: shown("[data-part=save] .btn.wn"),
          actBorder: shown("[data-part=save] .btn.wn", "borderColor"),
          headings: root.querySelectorAll("h1, h2, h3, h4").length};
}"""


@pytest.mark.parametrize(("scheme", "palette"), [("light", "plain"), ("dark", "plain"), ("dark", "midnight-neon")])
def test_at_each_width_and_theme_the_page_fits_has_no_heading_and_colours_only_save_and_harm(
        scheme, palette, ui_shells, ui_contexts, ui_width_check, paged_site):
    shell = ui_shells(site=[paged_site])
    if palette != "plain":
        assert shell.moxi("config", "set", "ui", "theme.palette", palette).returncode == 0
    page = ui_contexts(shell, color_scheme=scheme).pages[0]
    view = _settings(page)
    # The shell's right-hand Settings is hidden on Settings.
    expect(page.locator("#moxi-settings")).to_be_hidden()
    view.locator("[role=switch][data-control=loud]").click()
    view.locator("input[data-control=limit]").fill("12")
    expect(_save_line(view).locator("[data-line=save]")).to_have_text("2 not saved · Row limit, Loud")
    for width in (1280, 400):
        page.set_viewport_size({"width": width, "height": 900})
        colours = page.evaluate(_COLOURS)
        tokens = page.evaluate(_TOKENS)
        assert colours["headings"] == 0
        # Words only in the text tokens; the danger colour on the change that can hurt; the
        # accent behind Save alone.
        words = {tokens[name] for name in ("text", "text-dim", "text-faint", "danger", "on-accent")}
        assert set(colours["seen"]) <= words, (colours["seen"], tokens)
        assert colours["harm"] == colours["held"] == colours["switch"] == tokens["danger"], colours
        # The harm colour is a colour of its own on every Look, plain included.
        assert tokens["danger"] not in {tokens["text"], tokens["text-dim"]}, tokens
        assert colours["save"] == tokens["accent"], colours
        scroll, client = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert scroll <= client, (width, scroll, client)
    # The confirm's question and its act take the danger colour too.
    line = _save_line(view)
    line.get_by_role("button", name="Save").click()
    expect(line).to_have_attribute("data-state", "confirm")
    colours = page.evaluate(_COLOURS)
    assert colours["ask"] == colours["act"] == colours["actBorder"] == tokens["danger"], colours
    line.get_by_role("button", name="Go back").click()
    expect(line).to_have_attribute("data-state", "held")
    ui_width_check(page, module="ui", route="settings/fixture", width=400)


def test_a_locked_key_is_read_only_and_a_post_sent_past_the_page_is_refused(
        ui_shells, ui_contexts, fixture_site):
    shell = ui_shells(site=[fixture_site],
                      env={"MOXI_UI_LOCKED_SETTINGS": "fixture.limit, fixture.rows.backup"})
    # The lock is on the shell's routes: the command typed at a keyboard still writes.
    assert shell.moxi("config", "set", "fixture", "limit", "7").returncode == 0
    assert shell.moxi("config", "set", "fixture", "rows.spare", "false").returncode == 0
    page = ui_contexts(shell).pages[0]
    view = _settings(page)
    limit = _row(view, "limit")
    box = view.locator("input[data-control=limit]")
    expect(box).to_be_disabled()
    expect(box).to_have_value("7")
    expect(limit.locator("[data-line=line]").last).to_have_text(LOCKED)
    # Changed, but locked: its default shows with no Reset.
    expect(limit.locator("[data-line=default]")).to_have_text("default 10")
    # A locked entry of a switch table: its switch is off, with the same line.
    backup = view.locator(".entry[data-entry='rows.backup']")
    expect(backup.get_by_role("switch")).to_be_disabled()
    expect(backup.locator("[data-line=line]")).to_have_text(LOCKED)
    # The table that holds it is not off: the entry beside the locked one is not.
    expect(view.locator("[role=switch][data-control='rows.spare']")).to_be_enabled()
    expect(view.locator("input[data-control=workers]")).to_be_editable()
    # Sent from the page's own context, with the session's cookie and key.
    refused = {"refused": "fixture limit is locked for this run of the shell, so it can't "
                          "be changed from a page"}
    answers = page.evaluate(FORCED, [
        ["set", {"module": "fixture", "key": "limit", "value": "12"}],
        ["unset", {"module": "fixture", "key": "limit"}],
        ["set", {"module": "fixture", "key": "rows.backup", "value": "true"}],
        # The table's own key: a write to it would replace the locked entry.
        ["set", {"module": "fixture", "key": "rows", "value": "{backup = true}"}],
        ["set", {"module": "fixture", "key": "rows", "add": "backup"}],
        ["set", {"module": "fixture", "key": "rows", "remove": "backup"}],
        ["unset", {"module": "fixture", "key": "rows"}],
        ["save", {"module": "fixture", "keys": ["limit"], "values": ["12"], "resets": [False],
                  "was": ["7"]}]])
    assert answers[:2] == [[422, refused], [422, refused]]
    assert answers[2][0] == 422 and "fixture rows.backup is locked" in answers[2][1]["refused"]
    assert answers[3:7] == [[422, {"refused": refused["refused"].replace("limit", "rows")}]] * 4
    assert answers[7][0] == 200 and [result["outcome"] for result in answers[7][1]["results"]] == ["refused"]
    assert tomllib.loads(shell.config_file("fixture").read_text()) == {"limit": 7, "rows": {"spare": False}}


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
    ui_page.wait_for_function("() => window.__submitted !== undefined")
    assert ui_page.evaluate("window.__submitted") == {
        "model": "opus", "add_dir": ["/srv/b"], "remote_control": "here", "prompt_file": None,
        "permission_mode": "default"}
