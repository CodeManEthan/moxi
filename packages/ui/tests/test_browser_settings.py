"""The Settings page in a browser, as it reads: the list of modules and one module's
settings at a time, each type's control, a changed key's default and Reset, More, the
foot, the addresses the page writes for itself, a section that can't be read, a key the
shell was started with locked, copying a key, the page kept across the shell's redraw;
and `<moxi-ui-form>` in submit mode with catalog rows."""

from __future__ import annotations

import re
import tomllib

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.browser, pytest.mark.timeout(90)]

# The page reads through `moxi config` in a child process.
SETTINGS_MS = 15_000
LOCKED = "Locked for this run of the shell."


def _view(page):
    return page.locator("#moxi-panel > moxi-ui-page moxi-ui-settings")


def _settings(page, route="settings/fixture"):
    page.goto(f"/m/ui/{route}")
    view = _view(page)
    expect(view.locator("[data-part=foot]")).to_be_visible(timeout=SETTINGS_MS)
    return view


def _row(view, name):
    return view.locator(f".set[data-key='{name}']")


def _field(form, name):
    return form.locator(f".field[data-name='{name}']")


def _list(view):
    return view.locator("[data-part=list] a[data-list]")


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


def test_the_page_writes_its_own_address_and_back_leaves_it(ui_shell, ui_page):
    ui_page.locator("#moxi-settings").click()
    view = _view(ui_page)
    expect(view.locator("[data-part=pane][data-module=moxi] [data-part=foot]")).to_be_visible(timeout=SETTINGS_MS)
    ui_page.evaluate("document.querySelector('#moxi-panel > moxi-ui-page').dataset.placed = 'first'")
    _list(view).and_(ui_page.locator("[data-list=fixture]")).click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/fixture")
    expect(view.locator("[data-part=pane][data-module=fixture] [data-part=foot]")).to_be_visible(
        timeout=SETTINGS_MS)
    # The shell did not place the page again for the address the page wrote.
    expect(ui_page.locator("#moxi-panel > moxi-ui-page[data-placed=first]")).to_be_attached()
    # The foot's link to the module's page moves through the shell.
    view.locator("[data-part=foot]").get_by_role("link", name="Fixture").click()
    expect(ui_page).to_have_url(ui_shell.url + "/m/fixture/")
    expect(ui_page.locator("#moxi-panel > moxi-fixture-page")).to_be_attached()
    ui_page.go_back()
    expect(ui_page).to_have_url(ui_shell.url + "/m/ui/settings/fixture")
    expect(view.locator("[data-part=pane][data-module=fixture]")).to_be_visible(timeout=SETTINGS_MS)
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
        expect(said.locator("[data-line=unreadable]")).to_contain_text(
            "fixture's settings can't be read: ", timeout=SETTINGS_MS)
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


def test_a_click_on_a_key_copies_it(ui_shell, ui_page):
    ui_page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=ui_shell.url)
    view = _settings(ui_page)
    key = _row(view, "limit").locator("[data-line=key]")
    key.click()
    expect(_row(view, "limit").locator("[data-line=copied]")).to_have_text("Copied")
    assert ui_page.evaluate("navigator.clipboard.readText()") == "limit"
    expect(_row(view, "limit").locator("[data-line=copied]")).to_have_count(0, timeout=4000)


_SIGN_OUT = """async () => {
  const good = localStorage.getItem("moxi-ui-key");
  localStorage.setItem("moxi-ui-key", "A".repeat(43));
  await (await import("/m/ui/client.js")).api("ui", "api/modules");
  return good;
}"""


def test_more_stays_open_when_the_shell_draws_the_page_again(ui_shell, ui_page):
    view = _settings(ui_page, "settings/ui")
    # Every section in, so no read is on its way while the sign-in drops.
    expect(view.locator("[data-part=list]:not([data-reading])")).to_be_attached(timeout=SETTINGS_MS * 2)
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
    ui_page.wait_for_function("window.__submitted !== undefined")
    assert ui_page.evaluate("window.__submitted") == {
        "model": "opus", "add_dir": ["/srv/b"], "remote_control": "here", "prompt_file": None,
        "permission_mode": "default"}
