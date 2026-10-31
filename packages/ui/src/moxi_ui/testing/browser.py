"""The browser checks every module runs on its own components: the contract check and the
360 px width check.

Both take a Playwright page (the sync API) already on the shell's document, signed in
(`TestShell.sign_in`). They call only the page's own methods, so this module imports
nothing of Playwright. A failed check raises `AssertionError` saying what was wrong.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

#: The width every page and component lays out down to, in CSS px.
PHONE_WIDTH = 360
#: Milliseconds a placed component has to render into its shadow root.
RENDER_MS = 5000
#: Seconds with no new request after which a component is taken to have settled.
QUIET_S = 0.3
#: The longest a component is given to settle, in seconds.
SETTLE_S = 3.0
#: The shell's push channel, which `subscribe` reaches through.
PUSH_PATH = "/m/ui/api/push"

# The colour properties a token can reach: a component that takes a token shows it in one
# of these somewhere in its shadow tree.
_COLOURS = ["color", "background-color", "background-image", "border-top-color",
            "border-right-color", "border-bottom-color", "border-left-color",
            "outline-color", "text-decoration-color", "box-shadow", "fill", "stroke"]
_PROBES = ("rgb(1, 2, 3)", "rgb(250, 251, 252)")

_JS_WALK = """
function* tree(root) {
  for (const node of root.querySelectorAll("*")) {
    yield node;
    if (node.shadowRoot) yield* tree(node.shadowRoot);
  }
}
function* walk(element) {
  yield element;
  if (element.shadowRoot) yield* tree(element.shadowRoot);
  yield* tree(element);
}
const frames = () => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
"""

_JS_PLACE = """async ([module, name, attrs, mark]) => {
  const client = await import("/m/ui/client.js");
  const host = document.createElement("div");
  host.dataset.moxiCheck = mark;
  (document.getElementById("moxi-panel") ?? document.body).append(host);
  const before = {sheets: document.styleSheets.length,
                  adopted: document.adoptedStyleSheets.length};
  let element;
  try {
    element = await client.place(host, module, name, attrs);
  } catch (err) {
    host.remove();
    return {error: String(err)};
  }
  if (element === null) {
    host.remove();
    return {placed: false};
  }
  window.__moxiChecks = window.__moxiChecks ?? {};
  window.__moxiChecks[mark] = {host, element, before};
  return {placed: true, tag: element.localName};
}"""

_JS_RENDERED = """(mark) => {
  const element = window.__moxiChecks[mark].element;
  return element.shadowRoot !== null && element.shadowRoot.querySelector("*") !== null;
}"""

_JS_SHAPE = """(mark) => {
  const {element, before} = window.__moxiChecks[mark];
  return {
    open: element.shadowRoot !== null,
    defined: customElements.get(element.localName) !== undefined,
    sheets: document.styleSheets.length - before.sheets,
    adopted: document.adoptedStyleSheets.length - before.adopted,
  };
}"""

_JS_TOKEN = _JS_WALK + """
const colours = (element, names) => [...walk(element)].map((node) => {
  const style = getComputedStyle(node);
  return names.map((name) => style.getPropertyValue(name)).join("|");
}).join("\\n");
return async ([mark, token, probes, names]) => {
  const {host, element} = window.__moxiChecks[mark];
  const seen = [];
  for (const probe of probes) {
    host.style.setProperty(`--moxi-${token}`, probe);
    await frames();
    seen.push(colours(element, names));
  }
  host.style.removeProperty(`--moxi-${token}`);
  return seen[0] !== seen[1];
};
"""

_JS_REMOVE = """(mark) => {
  const check = window.__moxiChecks?.[mark];
  if (check) { check.host.remove(); delete window.__moxiChecks[mark]; }
}"""

_JS_WIDTH = _JS_WALK + """
const name = (node) => {
  let text = node.localName;
  if (node.id) text += "#" + node.id;
  if (typeof node.className === "string" && node.className.trim())
    text += "." + node.className.trim().split(/\\s+/).join(".");
  const root = node.getRootNode();
  return root instanceof ShadowRoot ? `${name(root.host)} > ${text}` : text;
};
return async () => {
  await frames();
  const width = window.innerWidth;
  const wide = [...walk(document.documentElement)]
    .filter((node) => node.getBoundingClientRect().right > width + 0.5)
    .slice(0, 8).map((node) => `${name(node)} (right edge ${Math.round(node.getBoundingClientRect().right)})`);
  return {width, scroll: document.documentElement.scrollWidth, body: document.body.scrollWidth, wide};
};
"""


def _function(body: str) -> str:
    """A page function from a script that ends in `return <function>`."""
    return f"(args) => {{ const run = (() => {{ {body} }})(); return run(args); }}"


@dataclass
class ComponentCheck:
    """What the contract check saw: the element name, the requests the component made
    while it was placed and settled, and the tokens that reached its colours. With
    `keep`, `locator` is the placed element, still in the page."""

    module: str
    name: str
    element: str
    requests: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    locator: Any = None


def _on_shell(page: Any) -> None:
    if not page.evaluate("() => document.getElementById('moxi-shell') !== null"):
        raise AssertionError(f"the page is not on the shell's document ({page.url}); "
                             "sign in first")


def _settle(page: Any) -> None:
    """Wait until `page`'s context has made no request for `QUIET_S` seconds, at most
    `SETTLE_S`: there is no event for "the page is done", so quiet stands for it."""
    stamp = [time.monotonic()]

    def seen(request: Any) -> None:
        stamp[0] = time.monotonic()

    page.context.on("request", seen)
    try:
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() - stamp[0] < QUIET_S and time.monotonic() < deadline:
            page.wait_for_timeout(50)
    finally:
        page.context.remove_listener("request", seen)


def _topics(url: str) -> set[str]:
    """The topics a push request asks for."""
    joined = ",".join(parse_qs(urlsplit(url).query).get("topics", []))
    return {topic for topic in joined.split(",") if topic}


def _held(page: Any) -> set[str]:
    """The topics `page`'s context already holds: those of the last push request each of
    its pages made. Playwright keeps a page's recent requests from 1.56 on; with an older
    one, or once the request has aged out of them, no topic counts as held."""
    held: set[str] = set()
    for each in page.context.pages:
        recent = getattr(each, "requests", None)
        if recent is None:
            return set()
        pushes = [request.url for request in recent()
                  if urlsplit(request.url).path == PUSH_PATH]
        if pushes:
            held |= _topics(pushes[-1])
    return held


def _allowed(url: str, origin: str, modules: Sequence[str], held: set[str]) -> bool:
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" != origin:
        return False
    if any(parts.path.startswith(f"/m/{module}/") for module in modules):
        return True
    if parts.path == PUSH_PATH:
        return all(topic in held or topic.split(".", 1)[0] in (*modules, "ui")
                   for topic in _topics(url))
    return False


def check_component(page: Any, module: str, name: str,
                    attrs: Mapping[str, str] | None = None, *,
                    tokens: Sequence[str] = ("accent",), allow: Sequence[str] = (),
                    keep: bool = False) -> ComponentCheck:
    """The contract check on `module`'s component `name`, placed with `attrs` through
    `client.js`'s `place()` on the shell's document in `page`.

    It passes when the shell places the component; it renders into an open shadow root
    within 5 s; it adds no style sheet to the document; each of `tokens` (theme token
    names without `--moxi-`, `accent` by default) reaches at least one colour in its shadow
    tree, so changing the token on its host changes what it shows; and every request its
    browser context made from the placement until it settled went to the shell's own
    origin, under `/m/<module>/` (or `/m/<m>/` for each `m` of `allow`, the modules whose
    components it places in turn), or to the push channel for `<module>`'s, `allow`'s
    and the shell's own topics. The push channel is one stream for the whole page, which
    reopens with every topic the page holds when a component subscribes, so a topic the
    page held before the placement is not counted against the component.

    The component is removed after the check, unless `keep`, when `locator` is the
    element still in the page.
    """
    _on_shell(page)
    # Requests the page was already making (the shell placing its own blocks) are not
    # the component's.
    _settle(page)
    held = _held(page)
    origin = page.evaluate("() => location.origin")
    mark = f"{module}.{name}.{time.monotonic_ns()}"
    requests: list[str] = []
    stamp = [time.monotonic()]

    def seen(request: Any) -> None:
        requests.append(request.url)
        stamp[0] = time.monotonic()

    context = page.context
    context.on("request", seen)
    try:
        placed = page.evaluate(_JS_PLACE, [module, name, dict(attrs or {}), mark])
        if "error" in placed:
            raise AssertionError(f"place() refused {module}'s {name}: {placed['error']}")
        if not placed["placed"]:
            raise AssertionError(f"the shell did not place {module}'s {name}: it is not "
                                 "installed, not declared, refused (see the modules view), "
                                 "or its script did not load or define its element")
        try:
            page.wait_for_function(_JS_RENDERED, arg=mark, timeout=RENDER_MS)
        except Exception as err:  # noqa: BLE001 - Playwright's TimeoutError, named below
            raise AssertionError(f"{placed['tag']} rendered nothing into an open shadow "
                                 f"root in {RENDER_MS / 1000:g}s") from err
        shape = page.evaluate(_JS_SHAPE, mark)
        if not shape["defined"]:
            raise AssertionError(f"{placed['tag']} is not a defined custom element")
        if shape["sheets"] or shape["adopted"]:
            raise AssertionError(f"{placed['tag']} added a style sheet to the document; a "
                                 "component's styles belong in its shadow root")
        reached = []
        for token in tokens:
            if not page.evaluate(_function(_JS_TOKEN), [mark, token, list(_PROBES), _COLOURS]):
                raise AssertionError(f"--moxi-{token} reaches no colour of {placed['tag']}: "
                                     "changing it on the host changed nothing it shows")
            reached.append(token)
        deadline = time.monotonic() + SETTLE_S
        while time.monotonic() - stamp[0] < QUIET_S and time.monotonic() < deadline:
            page.wait_for_timeout(50)
    finally:
        context.remove_listener("request", seen)
    modules = (module, *allow)
    strays = [url for url in requests if not _allowed(url, origin, modules, held)]
    if strays:
        if not keep:
            page.evaluate(_JS_REMOVE, mark)
        raise AssertionError(f"{placed['tag']} made requests outside /m/{module}/: "
                             + ", ".join(strays))
    result = ComponentCheck(module, name, placed["tag"], requests, reached)
    if keep:
        result.locator = page.locator(f'[data-moxi-check="{mark}"] > {placed["tag"]}')
    else:
        page.evaluate(_JS_REMOVE, mark)
    return result


def check_width(page: Any, *, module: str | None = None, name: str = "page",
                attrs: Mapping[str, str] | None = None, route: str = "",
                width: int = PHONE_WIDTH, height: int = 800) -> dict[str, Any]:
    """The width check: at a viewport `width` CSS px wide (360 by default) the document
    has no horizontal scroll, and no element reaches past its right edge (where a parent
    that doesn't scroll would cut it off).

    With no `module` it checks the page as it is. With `module` and `name` `page` it
    first opens that module's panel at `route` through `navigate()`; with another `name`
    it first places that component (with `attrs`) in the panel, where it is left. Raises
    `AssertionError` naming the elements that reach past the right edge; returns the
    widths it measured.
    """
    page.set_viewport_size({"width": width, "height": height})
    if module is not None:
        _on_shell(page)
        if name == "page":
            page.evaluate("async ([m, r]) => (await import('/m/ui/client.js')).navigate(m, r)",
                          [module, route])
            page.locator(f"#moxi-panel > moxi-{module}-page").wait_for(state="attached")
            page.wait_for_function(
                f"() => document.querySelector('#moxi-panel > moxi-{module}-page')"
                "?.shadowRoot?.querySelector('*') != null", timeout=RENDER_MS)
        else:
            mark = f"{module}.{name}.{time.monotonic_ns()}"
            placed = page.evaluate(_JS_PLACE, [module, name, dict(attrs or {}), mark])
            if not placed.get("placed"):
                raise AssertionError(f"the shell did not place {module}'s {name}: {placed}")
            page.wait_for_function(_JS_RENDERED, arg=mark, timeout=RENDER_MS)
    seen = page.evaluate(_function(_JS_WIDTH))
    what = f"{module}'s {name}" if module else page.url
    if seen["scroll"] > seen["width"] or seen["body"] > seen["width"]:
        raise AssertionError(f"{what} scrolls sideways at {width} px: the document is "
                             f"{max(seen['scroll'], seen['body'])} px wide; past the edge: "
                             + ("; ".join(seen["wide"]) or "nothing found"))
    if seen["wide"]:
        raise AssertionError(f"{what} reaches past the right edge at {width} px, cut off "
                             "where it doesn't scroll: " + "; ".join(seen["wide"]))
    return seen
