"""Made-up values for the tests, generated from a seed at test time.

No test file holds a secret-shaped literal: every token here is built from short pieces
and seeded random characters, so the repo's own guards and the gitleaks binary never meet
one in the tree. Where the fixed part alone would match a rule, the pieces are joined at
run time with `"".join`, because Python folds `"a" + "b"` into one constant and the
compiled file in `__pycache__` would hold the whole match.
"""

from __future__ import annotations

import base64
import json
import random
import string

ALNUM = string.ascii_letters + string.digits
URLSAFE = ALNUM + "-_"
HEX = "0123456789abcdef"


def rng(seed: int = 7) -> random.Random:
    return random.Random(seed)


def chars(r: random.Random, n: int, alphabet: str = ALNUM) -> str:
    return "".join(r.choice(alphabet) for _ in range(n))


def github(r, kind: str = "p") -> str:
    return "gh" + kind + "_" + chars(r, 36)


def stripe(r) -> str:
    return "sk" + "_live_" + chars(r, 24)


def google_api(r) -> str:
    return "AI" + "za" + chars(r, 35, URLSAFE)


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")


def jwt(r) -> str:
    head = _b64({"alg": "HS256", "typ": "JWT"})
    body = _b64({"sub": chars(r, 12), "name": chars(r, 10), "iat": r.randrange(10**9)})
    return f"{head}.{body}.{chars(r, 43, URLSAFE)}"


def bws(r) -> str:
    uid = "-".join(chars(r, n, HEX) for n in (8, 4, 4, 4, 12))
    return "0" + "." + uid + "." + chars(r, 30) + ":" + chars(r, 24) + "=="


def google_refresh(r) -> str:
    return "1/" + "/0" + chars(r, 40, URLSAFE)


def google_access(r) -> str:
    return "ya" + "29." + chars(r, 60, URLSAFE)


def google_client_secret(r) -> str:
    return "GOC" + "SPX-" + chars(r, 28, URLSAFE)


def discord(r) -> str:
    return r.choice("MNO") + chars(r, 23, URLSAFE) + "." + chars(r, 6) + "." + \
        chars(r, 30, URLSAFE)


def telegram(r) -> str:
    return str(r.randrange(10**8, 10**9)) + ":" + "AA" + chars(r, 33, URLSAFE)


def url_credential(r, user: str = "") -> str:
    return "https://" + user + ":" + chars(r, 16) + "@" + "db.example.test/x"


def sk_key(r, n: int = 32, alphabet: str = ALNUM, kind: str = "") -> str:
    """An `sk-` key: `kind` (`proj-`, `or-v1-`) and then `n` characters of `alphabet`."""
    return "".join(("s", "k-", kind, chars(r, n, alphabet)))


def password_assignment(r) -> str:
    return "".join(("pass", "word = \"")) + chars(r, 14) + '"'


def ntfy(r) -> str:
    return "tk" + "_" + chars(r, 29, string.ascii_lowercase + string.digits)


def bearer(r) -> str:
    return "Authorization: " + "Bearer " + chars(r, 40, ALNUM + "._-")


def key_header(kind: str = "RSA", tail: str = "") -> str:
    return "".join(("-----BEGIN ", kind, " PRIV", "ATE KEY", tail, "-----"))


def generic_value(r) -> str:
    """An assignment broad's generic-api-key finds on its own: the value is the secret.
    Draws until one does, since a random value may hold one of the rule's stopwords."""
    from moxi_secrets import scan

    while True:
        value = "".join(("api", "_key=")) + chars(r, 24)
        if any(h.rule == "generic-api-key" for h in scan(value, ("broad",))):
            return value


#: Lines on which generic-api-key's line allowlists keep its hit out of `scan`, each
#: with the path that turns its allowlist on: a secret mount, a JavaScript import, and a
#: BitBake recipe's checksum line.
LINE_ALLOWED = (("RUN --mount=type=secret,id=a {value}", None),
                ("import {{ a }} from 'b'; {value}", None),
                ('LIC_FILES_CHKSUM = "{value}"', "meta/x.bb"))

#: Unicode 17.0's Default_Ignorable_Code_Point, as (first, last) with adjacent ranges
#: joined: written here from the Unicode file, apart from secrets' own table.
DEFAULT_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
                     (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
                     (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
                     (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
                     (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))

_seen: list = []


def see_through() -> str:
    """Every character the scan reads past: each C0 control but tab and newline, DEL,
    each C1 control, every Cf character of the running Python, and every
    default-ignorable code point."""
    if not _seen:
        import unicodedata

        codes = {*range(0x20), *range(0x7F, 0xA0)} - {0x09, 0x0A}
        codes |= {c for c in range(0x110000) if unicodedata.category(chr(c)) == "Cf"}
        for first, last in DEFAULT_IGNORABLE:
            codes |= set(range(first, last + 1))
        _seen.append("".join(chr(c) for c in sorted(codes)))
    return _seen[0]


#: Characters the scan does not read past: tab, newline and the Unicode space
#: separators.
NOT_SEEN = "\t\n \u00a0\u1680" + "".join(chr(c) for c in range(0x2000, 0x200B)) + \
    "\u202f\u205f\u3000"


def split(value: str, c: str, at: int = 10) -> str:
    """`value` with `c` inside it."""
    return value[:at] + c + value[at:]


def curl_user(r) -> str:
    user = chars(r, 6, string.ascii_lowercase) + ":" + chars(r, 12)
    return " ".join(("curl", "-u", user, "https://api.example.test"))


def luhn_complete(digits: str) -> str:
    """`digits` with a Luhn check digit appended."""
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2 == 0:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return digits + str((10 - total % 10) % 10)


def card(r, length: int = 16, prefix: str = "4") -> str:
    return luhn_complete(prefix + chars(r, length - len(prefix) - 1, string.digits))


def iban(r, country: str = "DE", length: int = 22) -> str:
    """A made-up IBAN with valid check digits."""
    bban = chars(r, length - 4, string.digits)
    moved = bban + country + "00"
    number = int("".join(str(int(c, 36)) for c in moved))
    check = 98 - number % 97
    return f"{country}{check:02d}{bban}"


def grouped(value: str, size: int = 4, sep: str = " ") -> str:
    return sep.join(value[i:i + size] for i in range(0, len(value), size))


#: Each credential shape with the rule that must find it.
CREDENTIALS = (
    (github, "github-pat"),
    (lambda r: github(r, "o"), "github-oauth"),
    (lambda r: github(r, "s"), "github-app-token"),
    (stripe, "stripe-access-token"),
    (google_api, "gcp-api-key"),
    (jwt, "jwt"),
    (bws, "bws-access-token"),
    (google_refresh, "google-oauth-refresh-token"),
    (google_access, "google-oauth-access-token"),
    (google_client_secret, "google-oauth-client-secret"),
    (discord, "discord-bot-token"),
    (ntfy, "ntfy-access-token"),
    (sk_key, "sk-api-key"),
)


def mixed_text(r, lines: int = 20) -> str:
    """Prose with credential shapes on some lines."""
    out = []
    for _ in range(lines):
        words = " ".join(chars(r, r.randrange(2, 9), string.ascii_lowercase)
                         for _ in range(r.randrange(3, 12)))
        if r.random() < 0.4:
            make, _rule = r.choice(CREDENTIALS)
            words += " " + make(r) + " " + chars(r, 4, string.ascii_lowercase)
        out.append(words)
    return "\n".join(out)
