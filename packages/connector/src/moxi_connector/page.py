"""The sign-in page at `/authorize`: where the owner proves he is the owner, by typing a code
minted at his own terminal (`moxi connector code`).

`page(under)` makes the page's ASGI app under one origin's `oauth.Under`. The sign-in
mounts it for GET and POST behind its per-minute caps.

**GET.** The request is checked before anything is shown (`Under.check`): a known client,
a redirect URI registered for it and allowed, `response_type=code`, S256, a challenge, a
state, and a `resource` absent or O/mcp. A bad client or redirect gets a plain 400 page
and no redirect, since the redirect isn't trusted yet; any other fault, a repeated
parameter among them, is redirected back with the OAuth error. A good request is kept as
a pending request (`Under.open`), and the page is one form with no script: a line naming
the client by the name it gave itself and the host it returns to, a code field, the
pending id hidden, and one button. When `Under.open` finds no room, the answer is a 429
page and no pending request is dropped.

**POST.** Form-encoded `pending` and `code`. An `Origin` header that is present and not
`null` must be O; a `Sec-Fetch-Site` header that is present must be `same-origin`;
otherwise 403. `Origin: null` reads as absent, so the check then rests on
`Sec-Fetch-Site`. A code that matches a live one spends the code and the pending request
in one step on the loop, with no await between them, so of two finishes racing on one
code exactly one wins; it answers 303 to the redirect URI with the OAuth code, the state
and the issuer. A miss counts against the pending request (dropped at its fifth) and
toward the burn of every live code (`codes.Codes.miss`), and the page says how many tries
remain.

**Text a caller registered is shown as text.** The client's name is anyone's to set, so
the page shows at most its first 64 characters, with control and format characters
removed, HTML-escaped, and labelled as the name the client gave itself.

**Headers agree with browsers.** Every answer carries `Cache-Control: no-store`, a CSP of
`default-src 'none'; style-src 'nonce-<n>'; form-action <O and each allowed callback's
origin>; frame-ancestors 'none'`, `Referrer-Policy: same-origin` and
`X-Content-Type-Options: nosniff`. `form-action` names the callbacks because Chromium
applies it to the redirect after the form's POST, and `same-origin` keeps `Origin` on the
form's own POST (under `no-referrer` Chromium sends `Origin: null`).
"""

from __future__ import annotations

import html
import logging
import secrets
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlsplit

from mcp.server.auth.provider import construct_redirect_uri

from . import grants
from .codes import BURN_AFTER
from .oauth import PageError, RedirectError, Under

log = logging.getLogger("moxi.connector.page")

#: The largest form the page reads.
MAX_FORM = 4096
#: What the page shows a client's name as, at most.
NAME_MAX = 64

FORBIDDEN_POST = "This form can be sent only from the connector's own sign-in page."
GONE = ("This sign-in has expired or was already finished. Start it again from Claude.")
DROPPED = ("Too many wrong codes for this sign-in. Start it again from Claude, with a new "
           "code from moxi connector code.")
WRONG = "That code is wrong or has expired. {left} left."
GET_A_CODE = ("Run moxi connector code in your own terminal, outside every agent session, "
              "and type the code it prints.")
TOO_BIG = "This form is too large."
REPEATED = "a parameter appears more than once"

_STYLE = """body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:2rem auto;
padding:0 1rem;color:#1a1a1a;background:#fafafa}
@media (prefers-color-scheme:dark){body{color:#eee;background:#161616}
input{background:#222;color:#eee;border-color:#555}}
h1{font-size:1.3rem}.name{font-weight:600}.note{color:#666}.bad{color:#b00020}
input[name=code]{font:1.4rem ui-monospace,monospace;letter-spacing:.1em;width:11ch;
padding:.3rem;text-transform:uppercase;border:1px solid #888;border-radius:4px}
button{font-size:1rem;padding:.4rem 1.2rem;margin-left:.5rem}"""


def page(under: Under):
    """The `/authorize` app under `under`'s origin."""
    oauth = under.oauth
    form_action = under.origin.form_action(oauth.redirect_uris)

    def headers(nonce: str) -> list[tuple[bytes, bytes]]:
        csp = (f"default-src 'none'; style-src 'nonce-{nonce}'; form-action {form_action}; "
               "frame-ancestors 'none'")
        return [(b"cache-control", b"no-store"),
                (b"content-security-policy", csp.encode()),
                (b"referrer-policy", b"same-origin"),
                (b"x-content-type-options", b"nosniff")]

    async def show(send, status: int, title: str, body: str) -> None:
        nonce = secrets.token_urlsafe(16)
        text = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f'<meta name="viewport" content="width=device-width, initial-scale=1">'
                f"<title>{html.escape(title)}</title>"
                f'<style nonce="{nonce}">{_STYLE}</style></head>'
                f"<body><h1>{html.escape(title)}</h1>{body}</body></html>").encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/html; charset=utf-8"),
                                (b"content-length", str(len(text)).encode()),
                                *headers(nonce)]})
        await send({"type": "http.response.body", "body": text})

    async def refused(send, status: int, text: str) -> None:
        await show(send, status, "Sign-in refused", f"<p>{_text(text)}</p>")

    async def redirect(send, url: str) -> None:
        await send({"type": "http.response.start", "status": 303,
                    "headers": [(b"location", url.encode()), (b"content-length", b"0"),
                                *headers(secrets.token_urlsafe(16))]})
        await send({"type": "http.response.body", "body": b""})

    async def form(send, status: int, pid: str, *, said: str | None = None) -> None:
        found = under.waiting(pid)
        if found is None:
            await refused(send, 400, GONE)
            return
        req = found.request
        name = shown_name(req.client_name)
        back = html.escape(urlsplit(req.redirect_uri).netloc)
        who = (f'<p><span class="name">{name}</span> <span class="note">(the name the client '
               f"gave itself)</span> asks to sign in to this connector, and returns to "
               f"<b>{back}</b>.</p>" if name else
               f"<p>A client that gave itself no name asks to sign in to this connector, "
               f"and returns to <b>{back}</b>.</p>")
        bad = f'<p class="bad" role="alert">{_text(said)}</p>' if said else ""
        body = (f"{who}{bad}<p>{_text(GET_A_CODE)}</p>"
                f'<form method="post" action="/authorize">'
                f'<input type="hidden" name="pending" value="{html.escape(pid)}">'
                f'<label>Code <input name="code" autocomplete="one-time-code" '
                f'autocapitalize="characters" spellcheck="false" required maxlength="16" '
                f'aria-label="Code"></label>'
                f'<button type="submit">Sign in</button></form>')
        await show(send, status, "Sign in to the connector", body)

    async def get(scope, send) -> None:
        pairs = parse_qsl(scope.get("query_string", b"").decode("latin-1"),
                          keep_blank_values=True)
        params: dict[str, str] = {}
        repeated = set()
        for key, value in pairs:
            if key in params:
                repeated.add(key)
            else:
                params[key] = value
        if repeated & {"client_id", "redirect_uri"}:
            await refused(send, 400, "This sign-in request names its client or its return "
                                     "address more than once.")
            return
        try:
            req = under.check(params)
            if repeated:
                raise RedirectError(construct_redirect_uri(
                    req.redirect_uri, error="invalid_request", error_description=REPEATED,
                    state=req.state, iss=under.origin.issuer), "invalid_request")
            pid = under.open(req)
        except RedirectError as e:
            await redirect(send, e.url)
            return
        except PageError as e:
            await refused(send, e.status, e.text)
            return
        await form(send, 200, pid)

    async def post(scope, receive, send) -> None:
        heads = _headers(scope)
        origin = heads.get(b"origin")
        if origin is not None and origin != b"null" \
                and origin.decode("latin-1") != under.origin.page_origin:
            await refused(send, 403, FORBIDDEN_POST)
            return
        site = heads.get(b"sec-fetch-site")
        if site is not None and site != b"same-origin":
            await refused(send, 403, FORBIDDEN_POST)
            return
        body = await _read(receive, MAX_FORM)
        if body is None:
            await refused(send, 413, TOO_BIG)
            return
        fields = dict(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
        pid = fields.get("pending", "")
        typed = fields.get("code", "")
        found = under.waiting(pid) if pid else None
        if found is None:
            await refused(send, 400, GONE)
            return
        # The spend: the terminal code and the pending request, in one step on the loop.
        live = oauth.codes.take(typed) if typed else None
        if live is not None:
            url = under.issue(pid, label=live.label)
            await oauth.event("code-redeemed", label=live.label,
                              client=found.request.client_id)
            await redirect(send, url)
            return
        left = under.miss(pid)
        burned = oauth.codes.miss()
        await oauth.event("code-miss", client=found.request.client_id, tries=left)
        if burned:
            await oauth.event("code-burned", misses=BURN_AFTER)
        if left <= 0:
            await refused(send, 400, DROPPED)
            return
        await form(send, 400, pid,
                   said=WRONG.format(left="1 try" if left == 1 else f"{left} tries"))

    async def app(scope, receive, send) -> None:
        if scope.get("method") == "POST":
            await post(scope, receive, send)
        else:
            await get(scope, send)

    return app


def shown_name(name: str | None) -> str:
    """A client's name as the page shows it: at most `NAME_MAX` characters, control and
    format characters removed, HTML-escaped. Empty when it gave none."""
    kept = grants._shown(name, NAME_MAX) or ""
    return html.escape(kept.strip(), quote=True)


def _text(text: str) -> str:
    """Text between tags: `<`, `>` and `&` escaped."""
    return html.escape(text, quote=False)


def _headers(scope: Mapping) -> dict[bytes, bytes]:
    out: dict[bytes, bytes] = {}
    for key, value in scope.get("headers", ()):
        out.setdefault(key.lower(), value)
    return out


async def _read(receive, limit: int) -> bytes | None:
    """The request's body, or None past `limit` bytes."""
    data = b""
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            return data
        data += message.get("body", b"")
        if len(data) > limit:
            return None
        if not message.get("more_body", False):
            return data


__all__ = ["DROPPED", "FORBIDDEN_POST", "GET_A_CODE", "GONE", "MAX_FORM", "NAME_MAX",
           "WRONG", "page", "shown_name"]
