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
    hexd = "0123456789abcdef"
    uid = "-".join(chars(r, n, hexd) for n in (8, 4, 4, 4, 12))
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


def password_assignment(r) -> str:
    return "".join(("pass", "word = \"")) + chars(r, 14) + '"'


def ntfy(r) -> str:
    return "tk" + "_" + chars(r, 29, string.ascii_lowercase + string.digits)


def bearer(r) -> str:
    return "Authorization: " + "Bearer " + chars(r, 40, ALNUM + "._-")


def key_header() -> str:
    return "".join(("-----BEGIN RSA PRIV", "ATE KEY-----"))


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
