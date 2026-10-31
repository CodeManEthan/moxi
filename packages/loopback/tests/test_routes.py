"""The route table: declarations and their refusals, the table and its builder, the
export, the query and body allowlist, and projection."""

import json
import sys
import textwrap

import pytest

from moxi_loopback.routes import (
    BUDGET,
    BadRequest,
    Entry,
    Field,
    Out,
    Page,
    PageError,
    ProjectionError,
    Route,
    Table,
    build_table,
    export,
    list_assets,
    parse_body,
    parse_query,
    parse_size,
    project,
)

H = "pkg.handlers:fn"


def read(path="/api/summary", **kw):
    kw.setdefault("returns", {})
    return Route(path, "read", H, **kw)


def act(path="/api/refresh", **kw):
    kw.setdefault("returns", {})
    return Route(path, "act", H, **kw)


def stream(path="/api/events", **kw):
    kw.setdefault("topics", {"today": Out("int")})
    return Route(path, "read", H, stream=True, **kw)


# --- declarations ---------------------------------------------------------------------


def test_a_good_route_takes_its_method_from_its_kind():
    assert read().method == "GET"
    assert act().method == "POST"


@pytest.mark.parametrize("path", [
    "/api/<id>", "/api/{id}", "/api/sum*", "api/summary", "/api/summary/", "/", "//x",
    "/api/../x", "/api/%73ummary", "/Api/summary", "/api/sum mary", "/api/summary\n", "",
])
def test_a_path_that_is_not_literal_lower_case_segments_is_refused(path):
    with pytest.raises(PageError):
        read(path)


@pytest.mark.parametrize("path", ["/api/health", "/api/prove"])
def test_the_reserved_built_in_paths_are_refused(path):
    with pytest.raises(PageError):
        read(path)


def test_a_kind_other_than_read_or_act_is_refused():
    with pytest.raises(PageError):
        Route("/x", "write", H, returns={})


@pytest.mark.parametrize("handler", ["pkg.handlers", "pkg handlers:fn", ":fn", "pkg:", "pkg:f-n"])
def test_a_handler_that_is_not_a_target_is_refused(handler):
    with pytest.raises(PageError):
        Route("/x", "read", handler, returns={})


def test_a_route_without_returns_is_refused_and_empty_returns_is_allowed():
    with pytest.raises(PageError):
        Route("/x", "read", H)
    assert Route("/x", "read", H, returns={}).returns == {}


def test_a_stream_with_returns_is_refused():
    with pytest.raises(PageError):
        stream(returns={})


@pytest.mark.parametrize("topics", [None, {}])
def test_a_stream_without_topics_is_refused(topics):
    with pytest.raises(PageError):
        Route("/api/events", "read", H, stream=True, topics=topics)


def test_topics_on_a_read_that_does_not_stream_is_refused():
    with pytest.raises(PageError):
        read(topics={"today": Out("int")})


@pytest.mark.parametrize("name", ["Today", "to_day", "-today", "today-", "to--day", "", "today\n"])
def test_a_bad_topic_name_is_refused(name):
    with pytest.raises(PageError):
        stream(topics={name: Out("int")})


def test_a_stream_act_is_refused():
    with pytest.raises(PageError):
        Route("/x", "act", H, stream=True, topics={"a": Out("int")})


def test_a_second_stream_route_is_refused():
    with pytest.raises(PageError):
        Page(routes=(stream("/api/a"), stream("/api/b")))


def test_topics_declared_as_a_query_key_on_a_stream_route_is_refused():
    with pytest.raises(PageError):
        stream(query={"topics": Field("string")})
    # On a route that does not stream it is an ordinary key.
    assert read(query={"topics": Field("string")}).query["topics"].type == "string"


def test_a_duplicate_path_is_refused():
    with pytest.raises(PageError):
        Page(routes=(read("/api/x"), act("/api/x")))


@pytest.mark.parametrize("idle_s", [4.9, 0, -1, True, "300", float("inf"), float("nan")])
def test_idle_s_under_five_or_not_a_number_is_refused(idle_s):
    with pytest.raises(PageError):
        Page(routes=(), idle_s=idle_s)


def test_idle_s_of_five_is_allowed():
    assert Page(routes=(), idle_s=5).idle_s == 5


@pytest.mark.parametrize("budget", [
    {"rss": "100MB"}, {"rss": "1.5G"}, {"rss": 100}, {"rss": "100m"}, {"rss": ""},
    {"fds": 0}, {"fds": -1}, {"fds": True}, {"fds": "64"},
    {"idle_cpu": 0.01}, {"rss": "100M", "hook_ms": 2}, "100M", ["rss"],
])
def test_a_malformed_budget_or_one_with_an_unknown_key_is_refused(budget):
    with pytest.raises(PageError):
        Page(routes=(), budget=budget)


def test_a_budget_fills_its_defaults():
    assert dict(Page(routes=()).budget) == {"rss": "120M", "fds": 64}
    assert dict(Page(routes=(), budget={"rss": "100M"}).budget) == {"rss": "100M", "fds": 64}
    assert dict(Page(routes=(), budget={"fds": 8}).budget) == {"rss": "120M", "fds": 8}
    assert dict(BUDGET) == {"rss": "120M", "fds": 64}


@pytest.mark.parametrize(("text", "size"), [
    ("0", 0), ("512", 512), ("4K", 4096), ("100M", 100 * 1024**2), ("2G", 2 * 1024**3),
])
def test_parse_size_reads_the_size_spelling_in_powers_of_1024(text, size):
    assert parse_size(text) == size


@pytest.mark.parametrize("text", ["", "M", "1T", "1 M", "-1", "1.5M", "1M\n", "1k", 5])
def test_parse_size_refuses_anything_else(text):
    with pytest.raises(ValueError):
        parse_size(text)


def test_a_page_health_target_and_assets_name_are_checked():
    with pytest.raises(PageError):
        Page(routes=(), health="not a target")
    with pytest.raises(PageError):
        Page(routes=(), assets="web/static", package="pkg")
    with pytest.raises(PageError):
        Page(routes=(), assets="../web", package="pkg")


def test_a_page_takes_the_package_of_the_code_that_builds_it():
    assert Page(routes=(), assets="web").package == (__package__ or None)
    assert Page(routes=(), assets="web", package="pkg.sub").package == "pkg.sub"
    assert Page(routes=()).package is None


# --- fields -------------------------------------------------------------------------


@pytest.mark.parametrize("make", [
    lambda: Field("list"),
    lambda: Field("table"),
    lambda: Field("int", items=Field("int")),
    lambda: Field("string", items={"a": Field("int")}),
    lambda: Field("list", items=Field("table", items={"a": Field("int")})),
    lambda: Field("list", items=Field("list", items=Field("int"))),
    lambda: Field("list", items={"a": Field("int")}),
    lambda: Field("table", items=Field("int")),
    lambda: Field("table", items={"a": "int"}),
    lambda: Field("table", items={"a.b": Field("int")}),
    lambda: Field("enum"),
    lambda: Field("enum", values=("a", "a")),
    lambda: Field("enum", values="ab"),
    lambda: Field("string", values=("a",)),
    lambda: Field("bool", max=1),
    lambda: Field("int", max=1.5),
    lambda: Field("string", max=-1),
    lambda: Field("float", max=float("inf")),
    lambda: Field("date"),
    lambda: Field("int", required="yes"),
])
def test_field_declaration_refusals(make):
    with pytest.raises(PageError):
        make()


@pytest.mark.parametrize("kind", ["float", "list", "table"])
def test_a_query_takes_only_string_int_bool_or_enum(kind):
    items = {"list": Field("int"), "table": {"a": Field("int")}}.get(kind)
    with pytest.raises(PageError):
        read(query={"k": Field(kind, items=items)})


def test_only_an_act_takes_a_body():
    with pytest.raises(PageError):
        read(body={"force": Field("bool")})


@pytest.mark.parametrize("cap", [0, 1024 * 1024 + 1, True, 1.5])
def test_body_cap_is_one_byte_to_one_mib(cap):
    with pytest.raises(PageError):
        act(body_cap=cap)
    assert act(body_cap=1024 * 1024).body_cap == 1024 * 1024


@pytest.mark.parametrize("make", [
    lambda: Out("list"),
    lambda: Out("table"),
    lambda: Out("int", items=Out("int")),
    lambda: Out("table", items={"a": Field("int")}),
    lambda: Out("list", items={"a": Out("int")}),
    lambda: Out("enum"),
    lambda: Out("int", mark="path"),
    lambda: Out("string", mark="secret"),
    lambda: Out("string", nullable=1),
])
def test_out_declaration_refusals(make):
    with pytest.raises(PageError):
        make()


# --- the table ----------------------------------------------------------------------


def test_add_find_and_entry_kinds():
    table = Table()
    entry = table.add("GET", "/signin")
    assert entry == Entry("GET", "/signin", "builtin")
    assert table.find("GET", b"/signin") is entry
    assert table.find("GET", "/signin") is entry
    assert table.find("POST", b"/signin") is None
    assert table.find("GET", b"/sign%69n") is None
    assert table.find("GET", b"/signin\xff") is None
    with pytest.raises(ValueError):
        table.add("GET", "/signin")


@pytest.mark.parametrize("args", [
    ("PUT", "/x", "builtin"), ("GET", "/x", "act"), ("POST", "/x", "read"),
    ("POST", "/x", "asset"), ("GET", "x", "builtin"), ("GET", "/a//b", "builtin"),
    ("GET", "/x", "page"), ("POST", "/x", "act", True),
])
def test_add_refuses_a_bad_entry(args):
    with pytest.raises(ValueError):
        Table().add(*args)


def test_a_frozen_table_refuses_add_and_mount():
    table = Table()
    table.add("GET", "/")
    assert table.freeze() is table and table.frozen
    with pytest.raises(ValueError):
        table.add("GET", "/signin")
    with pytest.raises(ValueError):
        table.mount("/m/usage", {"format": 1, "routes": [], "assets": []})


def sample_page(**kw):
    return Page(routes=(
        Route("/api/summary", "read", "moxi_usage.pagehandlers:summary",
              query={"days": Field("int", required=False, max=366)},
              returns={"total": Out("int"), "log": Out("string", mark="path")},
              gateway=True),
        Route("/api/refresh", "act", "moxi_usage.pagehandlers:refresh",
              body={"force": Field("bool", required=False)},
              returns={"ok": Out("bool")}),
        Route("/api/events", "read", "moxi_usage.pagehandlers:events", stream=True,
              topics={"today": Out("table", items={"total": Out("int")}),
                      "rows": Out("list", items=Out("table", items={"n": Out("int")}))}),
    ), **kw)


def test_build_table_holds_the_routes_and_the_built_ins():
    table, names = build_table(sample_page(budget={"rss": "100M"}))
    assert names == ()
    assert {(e.method, e.raw_path, e.kind, e.stream) for e in table} == {
        ("GET", "/api/summary", "read", False),
        ("POST", "/api/refresh", "act", False),
        ("GET", "/api/events", "read", True),
        ("GET", "/api/health", "builtin", False),
        ("GET", "/api/prove", "builtin", False),
    }
    assert not table.frozen


def test_export_round_trips_through_json_with_topics_and_the_filled_budget():
    data = export(sample_page(budget={"rss": "100M"}))
    assert json.loads(json.dumps(data)) == data
    assert data["format"] == 1
    assert data["budget"] == {"rss": "100M", "fds": 64}
    assert data["assets"] == []
    routes = {route["path"]: route for route in data["routes"]}
    assert set(routes) == {"/api/summary", "/api/refresh", "/api/events"}
    assert "handler" not in routes["/api/summary"]
    assert routes["/api/summary"]["gateway"] is True
    assert routes["/api/summary"]["query"]["days"] == {
        "type": "int", "required": False, "values": [], "max": 366, "items": None}
    assert routes["/api/summary"]["returns"]["log"] == {
        "type": "string", "mark": "path", "nullable": False, "items": None}
    assert routes["/api/events"]["returns"] is None
    assert routes["/api/events"]["topics"]["today"] == {
        "type": "table", "mark": None, "nullable": False,
        "items": {"total": {"type": "int", "mark": None, "nullable": False, "items": None}}}
    assert routes["/api/events"]["topics"]["rows"]["items"]["type"] == "table"


def test_mount_puts_an_export_under_its_prefix_with_each_entry_kind():
    data = json.loads(json.dumps(export(sample_page())))
    data["assets"] = ["app.js", "index.html"]
    table = Table()
    table.mount("/m/usage", data)
    assert {(e.method, e.raw_path, e.kind, e.stream) for e in table} == {
        ("GET", "/m/usage/api/summary", "read", False),
        ("POST", "/m/usage/api/refresh", "act", False),
        ("GET", "/m/usage/api/events", "read", True),
        ("GET", "/m/usage/app.js", "asset", False),
        ("GET", "/m/usage/index.html", "asset", False),
        ("GET", "/m/usage/", "asset", False),
    }
    # The built-ins are not in the export, so the shell's table never reaches them.
    assert table.find("GET", b"/m/usage/api/health") is None
    assert table.find("GET", b"/m/usage/api/prove") is None


@pytest.mark.parametrize("data", [
    {"format": 2, "routes": [], "assets": []},
    {"routes": [], "assets": []},
    {"format": 1, "routes": [{"path": "/x/", "kind": "read"}], "assets": []},
    {"format": 1, "routes": [{"path": "/x", "kind": "asset"}], "assets": []},
    {"format": 1, "routes": [], "assets": ["../x.js"]},
    {"format": 1, "routes": [], "assets": "app.js"},
    {"format": 1, "routes": [{"path": "/x", "kind": "read"}, {"path": "/x", "kind": "read"}],
     "assets": []},
])
def test_mount_refuses_a_bad_export_and_adds_nothing(data):
    table = Table()
    with pytest.raises(ValueError):
        table.mount("/m/usage", data)
    assert len(table) == 0


@pytest.mark.parametrize("prefix", ["/m/usage/", "m/usage", "", "/", "/m//usage"])
def test_mount_refuses_a_bad_prefix(prefix):
    with pytest.raises(ValueError):
        Table().mount(prefix, {"format": 1, "routes": [], "assets": []})


# --- assets -------------------------------------------------------------------------


@pytest.fixture
def make_package(tmp_path, monkeypatch):
    """Write a package `<name>` holding a `web` asset directory with the given files."""
    monkeypatch.syspath_prepend(str(tmp_path))

    def make(name, files, dirs=()):
        root = tmp_path / name
        (root / "web").mkdir(parents=True)
        (root / "__init__.py").write_text("")
        for file in files:
            (root / "web" / file).write_bytes(b"x")
        for sub in dirs:
            (root / "web" / sub).mkdir()
        return name

    return make


def test_build_table_lists_the_assets_and_serves_index_at_the_root(make_package):
    package = make_package("pagepkg_good", ["index.html", "app.js", "style.css", "icon.png"])
    page = Page(routes=(read(),), assets="web", package=package)
    table, names = build_table(page)
    assert names == ("app.js", "icon.png", "index.html", "style.css")
    assert table.find("GET", b"/").kind == "asset"
    assert table.find("GET", b"/app.js").kind == "asset"
    assert table.find("GET", b"/api/summary").kind == "read"
    assert export(page)["assets"] == list(names)


@pytest.mark.parametrize(("files", "dirs"), [
    (["app.js"], ["sub"]),
    (["app.exe"], []),
    (["README"], []),
    (["app.py"], []),
    (["-app.js"], []),
    (["app js.js"], []),
])
def test_build_table_refuses_a_bad_asset_table(make_package, files, dirs):
    package = make_package(f"pagepkg_bad_{abs(hash((tuple(files), tuple(dirs))))}", files, dirs)
    page = Page(routes=(), assets="web", package=package)
    with pytest.raises(PageError):
        build_table(page)
    with pytest.raises(PageError):
        export(page)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need a privilege on Windows")
def test_build_table_refuses_an_asset_entry_that_is_not_a_file(make_package, tmp_path):
    package = make_package("pagepkg_dangling", ["app.js"])
    (tmp_path / package / "web" / "gone.js").symlink_to(tmp_path / "nowhere.js")
    page = Page(routes=(), assets="web", package=package)
    with pytest.raises(PageError, match="gone.js is not a file"):
        build_table(page)
    with pytest.raises(PageError):
        export(page)


def test_build_table_refuses_a_missing_directory_or_package(make_package):
    package = make_package("pagepkg_missing", [])
    with pytest.raises(PageError):
        list_assets(Page(routes=(), assets="static", package=package))
    with pytest.raises(PageError):
        list_assets(Page(routes=(), assets="web", package="no_such_pagepkg"))


def test_build_table_refuses_an_asset_that_collides_with_a_route(make_package):
    package = make_package("pagepkg_clash", ["summary.json"])
    page = Page(routes=(read("/summary.json"),), assets="web", package=package)
    with pytest.raises(PageError):
        build_table(page)


def test_a_page_built_in_a_package_module_finds_its_own_assets(make_package, tmp_path):
    package = make_package("pagepkg_own", ["index.html"])
    (tmp_path / package / "page.py").write_text(textwrap.dedent("""
        from moxi_loopback.routes import Page
        PAGE = Page(routes=(), assets="web")
    """))
    import importlib
    module = importlib.import_module(f"{package}.page")
    assert module.PAGE.package == package
    assert build_table(module.PAGE)[1] == ("index.html",)


# --- the query ----------------------------------------------------------------------


QUERY = read(query={
    "days": Field("int", required=False, max=366),
    "all": Field("bool", required=False),
    "sort": Field("enum", required=False, values=("new", "old")),
    "q": Field("string", required=False, max=5),
    "need": Field("string", required=False),
})


@pytest.mark.parametrize(("qs", "want"), [
    (b"", {}),
    (b"days=30", {"days": 30}),
    (b"days=-5", {"days": -5}),
    (b"days=366", {"days": 366}),
    (b"days=000000000000000001", {"days": 1}),
    (b"all=true&all2=", None),
    (b"all=false", {"all": False}),
    (b"sort=old", {"sort": "old"}),
    (b"q=a%20b", {"q": "a b"}),
    (b"q=", {"q": ""}),
    ("q=caf%C3%A9", {"q": "café"}),
])
def test_query_parsing_rules(qs, want):
    if want is None:
        with pytest.raises(BadRequest):
            parse_query(QUERY, qs)
    else:
        assert parse_query(QUERY, qs) == want


@pytest.mark.parametrize(("qs", "key"), [
    (b"days=1.5", "days"), (b"days=%2B5", "days"), (b"days=+5", "days"), (b"days= 5", "days"),
    (b"days=1_0", "days"), (b"days=1234567890123456789", "days"), (b"days=367", "days"),
    (b"days=", "days"), (b"all=True", "all"), (b"all=1", "all"), (b"all=", "all"),
    (b"sort=NEW", "sort"), (b"sort=", "sort"), (b"q=abcdef", "q"),
    (b"days=1&days=1", "days"),
])
def test_a_bad_query_value_names_the_key_and_not_the_value(qs, key):
    with pytest.raises(BadRequest) as err:
        parse_query(QUERY, qs)
    assert err.value.key == key
    value = qs.split(b"=", 1)[1].decode()
    if value and value not in key:
        assert value not in str(err.value)


def test_an_unknown_query_key_is_named_and_its_value_never_echoed():
    with pytest.raises(BadRequest) as err:
        parse_query(QUERY, b"secret=hunter2")
    assert err.value.key == "secret"
    assert "hunter2" not in str(err.value)


def test_an_unknown_key_that_is_not_a_plain_name_is_not_repeated():
    with pytest.raises(BadRequest) as err:
        parse_query(QUERY, b"%3Cscript%3E=1")
    assert "<script>" not in str(err.value)


@pytest.mark.parametrize("qs", [b"days", b"days=1&", b"&days=1", b"q=\xff", b"q=%ff"])
def test_a_malformed_query_string_is_a_bad_request(qs):
    with pytest.raises(BadRequest):
        parse_query(QUERY, qs)


def test_a_required_query_key_that_is_missing_is_named():
    route = read(query={"day": Field("int")})
    with pytest.raises(BadRequest) as err:
        parse_query(route, b"")
    assert err.value.key == "day"


def test_a_stream_route_leaves_its_topics_key_for_the_stream():
    route = stream(query={"since": Field("int", required=False)})
    assert parse_query(route, b"topics=today&since=3") == {"since": 3}
    with pytest.raises(BadRequest):
        parse_query(read(), b"topics=today")


# --- the body -----------------------------------------------------------------------


BODY = act(body={
    "force": Field("bool", required=False),
    "n": Field("int", required=False, max=10),
    "ratio": Field("float", required=False, max=1.0),
    "name": Field("string", required=False, max=3),
    "mode": Field("enum", required=False, values=("a", "b")),
    "tags": Field("list", required=False, max=2, items=Field("string")),
    "opts": Field("table", required=False, items={
        "depth": Field("int"),
        "inner": Field("table", required=False, items={"on": Field("bool")}),
    }),
})


def body(value):
    return json.dumps(value).encode()


def test_a_good_body_parses():
    got = parse_body(BODY, body({"force": True, "n": 3, "ratio": 1, "name": "abc", "mode": "b",
                                 "tags": ["x", "y"], "opts": {"depth": 1, "inner": {"on": False}}}))
    assert got == {"force": True, "n": 3, "ratio": 1.0, "name": "abc", "mode": "b",
                   "tags": ["x", "y"], "opts": {"depth": 1, "inner": {"on": False}}}
    assert isinstance(got["ratio"], float)


@pytest.mark.parametrize(("value", "key"), [
    ({"force": 1}, "force"),
    ({"n": True}, "n"),
    ({"n": 1.0}, "n"),
    ({"n": 11}, "n"),
    ({"n": None}, "n"),
    ({"ratio": True}, "ratio"),
    ({"ratio": "1"}, "ratio"),
    ({"ratio": 1.5}, "ratio"),
    ({"name": 5}, "name"),
    ({"name": "abcd"}, "name"),
    ({"mode": "c"}, "mode"),
    ({"tags": "x"}, "tags"),
    ({"tags": ["x", "y", "z"]}, "tags"),
    ({"tags": ["x", 5]}, "tags.1"),
    ({"opts": []}, "opts"),
    ({"opts": {}}, "opts.depth"),
    ({"opts": {"depth": 1, "deep": 2}}, "opts.deep"),
    ({"opts": {"depth": 1, "inner": {"on": True, "off": False}}}, "opts.inner.off"),
    ({"opts": {"depth": "1"}}, "opts.depth"),
    ({"unknown": 1}, "unknown"),
])
def test_a_bad_body_names_the_dotted_path_and_never_the_value(value, key):
    with pytest.raises(BadRequest) as err:
        parse_body(BODY, body(value))
    assert err.value.key == key
    assert "hunter2" not in str(err.value)


@pytest.mark.parametrize("raw", [
    b"", b"{", b"[]", b"1", b"null", b'"x"', b'{"n": NaN}', b'{"ratio": Infinity}',
    b'{"n": 1, "n": 2}', b"\xff\xfe{}",
])
def test_a_body_that_is_not_one_json_object_is_a_bad_request(raw):
    with pytest.raises(BadRequest):
        parse_body(BODY, raw)


def test_a_required_body_key_that_is_missing_is_named():
    route = act(body={"force": Field("bool")})
    with pytest.raises(BadRequest) as err:
        parse_body(route, b"{}")
    assert err.value.key == "force"


def test_a_huge_integer_for_a_float_is_a_bad_request():
    with pytest.raises(BadRequest):
        parse_body(BODY, b'{"ratio": ' + b"9" * 400 + b"}")


@pytest.mark.parametrize(("raw", "key"), [
    (b'{"x": 1e400}', "x"),
    (b'{"x": -1e400}', "x"),
    (b'{"xs": [1.5, 1e400]}', "xs.1"),
])
def test_a_float_with_no_max_refuses_a_value_too_large_to_be_finite(raw, key):
    route = act(body={"x": Field("float", required=False),
                      "xs": Field("list", required=False, items=Field("float"))})
    assert parse_body(route, b'{"x": 1e300, "xs": [-1e300]}') == {"x": 1e300, "xs": [-1e300]}
    with pytest.raises(BadRequest) as err:
        parse_body(route, raw)
    assert err.value.key == key
    assert "finite" in str(err.value)


# --- projection ---------------------------------------------------------------------


SHAPE = Route("/api/x", "read", H, returns={
    "total": Out("int"),
    "rows": Out("list", items=Out("table", items={"n": Out("int"), "name": Out("string")})),
    "note": Out("string", nullable=True),
    "ratio": Out("float"),
}).shape


def test_projection_drops_an_undeclared_key_at_every_depth_and_reports_each():
    dropped = []
    got = project({"total": 3, "secret": "x", "rows": [{"n": 1, "name": "a", "extra": 1}]},
                  SHAPE, dropped=dropped.append)
    assert got == {"total": 3, "rows": [{"n": 1, "name": "a"}]}
    assert dropped == ["secret", "rows.0.extra"]


def test_strict_projection_fails_on_a_dropped_key():
    with pytest.raises(ProjectionError) as err:
        project({"total": 3, "rows": [{"n": 1, "extra": "hunter2"}]}, SHAPE, strict=True)
    assert err.value.path == "rows.0.extra"
    assert "hunter2" not in str(err.value)


def test_a_list_of_two_rows_projects_as_format_one():
    rows = [{"n": 1, "name": "a", "x": 0}, {"n": 2, "name": "b"}]
    assert project({"rows": rows}, SHAPE) == {"rows": [{"n": 1, "name": "a"}, {"n": 2, "name": "b"}]}
    topic = Out("list", items=Out("table", items={"n": Out("int")}))
    assert project([{"n": 1}, {"n": 2, "m": 3}], topic) == [{"n": 1}, {"n": 2}]


def test_a_declared_nullable_key_keeps_its_null():
    assert project({"note": None, "total": 1}, SHAPE, strict=True) == {"note": None, "total": 1}


def test_null_for_a_key_not_declared_nullable_is_refused():
    for strict in (False, True):
        with pytest.raises(ProjectionError) as err:
            project({"total": None}, SHAPE, strict=strict)
        assert err.value.path == "total"
    with pytest.raises(ProjectionError):
        project({"rows": [{"n": None}]}, SHAPE)
    with pytest.raises(ProjectionError):
        project({"rows": None}, SHAPE)


@pytest.mark.parametrize(("value", "path"), [
    ({"total": "3"}, "total"),
    ({"total": True}, "total"),
    ({"total": 1.0}, "total"),
    ({"ratio": float("nan")}, "ratio"),
    ({"ratio": float("inf")}, "ratio"),
    ({"ratio": False}, "ratio"),
    ({"rows": {"n": 1}}, "rows"),
    ({"rows": [5]}, "rows.0"),
    ({"rows": [{"name": 5}]}, "rows.0.name"),
    ([1], ""),
])
def test_a_mistyped_value_is_refused_whether_strict_or_not(value, path):
    with pytest.raises(ProjectionError) as err:
        project(value, SHAPE)
    assert err.value.path == path


def test_a_float_takes_an_integer_and_a_declared_key_left_out_stays_out():
    assert project({"ratio": 2}, SHAPE) == {"ratio": 2}
    assert project({}, SHAPE) == {}


def test_projection_keeps_marked_values_for_the_gateway_to_act_on():
    shape = Out("table", items={"log": Out("string", mark="path"), "key": Out("string", mark="redact")})
    assert project({"log": "/a", "key": "k"}, shape) == {"log": "/a", "key": "k"}


def test_a_non_string_key_is_dropped():
    dropped = []
    assert project({1: "x", "total": 1}, SHAPE, dropped=dropped.append) == {"total": 1}
    assert dropped == ["1"]
