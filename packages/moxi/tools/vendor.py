"""Copy the libraries the core vendors into `src/moxi/_vendor/`, from the versions listed.

    python packages/moxi/tools/vendor.py                 # fetch each wheel from PyPI
    python packages/moxi/tools/vendor.py --wheels DIR    # take each wheel from DIR

`src/moxi/_vendor/vendor.txt` lists one `name==version` per line. For each, the tool takes
that version's pure-Python wheel (from PyPI, checked against the digest PyPI publishes, or
`<name>-<version>-py3-none-any.whl` in DIR), and replaces `_vendor/<name>/` with the
wheel's `<name>/` package plus the licence files from its `.dist-info`. Every absolute
import of the library by itself (`from tomlkit.items import Table`) is rewritten to
`moxi._vendor.<name>`, so the copy never imports an installed one. To change a version,
edit `vendor.txt` and run the tool again.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "src" / "moxi" / "_vendor"
LISTING = VENDOR / "vendor.txt"
PACKAGE = "moxi._vendor"


def listed(path: Path = LISTING) -> list[tuple[str, str]]:
    """The `(name, version)` pairs `vendor.txt` lists, in order."""
    pairs = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name, sep, version = line.partition("==")
        if not sep or not re.fullmatch(r"[a-z0-9_]+", name.strip()) or not version.strip():
            raise SystemExit(f"{path}:{number}: write each line as name==version")
        pairs.append((name.strip(), version.strip()))
    return pairs


def wheel_from_pypi(name: str, version: str) -> bytes:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        release = json.load(response)
    wanted = f"{name}-{version}-py3-none-any.whl"
    for file in release["urls"]:
        if file["filename"] == wanted:
            with urllib.request.urlopen(file["url"], timeout=60) as response:
                data = response.read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != file["digests"]["sha256"]:
                raise SystemExit(f"{wanted}: sha256 {digest} is not the one PyPI lists")
            return data
    raise SystemExit(f"PyPI has no {wanted}")


def wheel_from_dir(directory: Path, name: str, version: str) -> bytes:
    path = directory / f"{name}-{version}-py3-none-any.whl"
    if not path.is_file():
        raise SystemExit(f"{path} does not exist")
    return path.read_bytes()


def rewrite(text: str, name: str, where: str) -> str:
    """`text` with the library's absolute imports of itself pointed into `moxi._vendor`."""
    out = []
    for line in text.splitlines(keepends=True):
        if re.match(rf"\s*import {name}\b", line):
            raise SystemExit(f"{where}: cannot rewrite {line.strip()!r}; extend the tool")
        out.append(re.sub(rf"^(\s*)from {name}(?=(?:\.[\w.]+)?\s+import\b)",
                          rf"\1from {PACKAGE}.{name}", line))
    return "".join(out)


def unpack(data: bytes, name: str, version: str, dest: Path) -> list[str]:
    """Write the wheel's package and licence files under `dest`; return what was written."""
    info = f"{name}-{version}.dist-info/"
    written = []
    with zipfile.ZipFile(io.BytesIO(data)) as whl:
        for member in whl.namelist():
            if member.endswith("/") or "__pycache__" in member:
                continue
            if member.startswith(f"{name}/"):
                rel = member[len(name) + 1:]
            elif member.startswith(info) and (
                    member.startswith(f"{info}licenses/")
                    or Path(member).name.upper().startswith(("LICENSE", "COPYING"))):
                rel = Path(member).name
            else:
                continue
            body = whl.read(member)
            if rel.endswith(".py"):
                body = rewrite(body.decode("utf-8"), name, member).encode("utf-8")
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            written.append(rel)
    if "__init__.py" not in written:
        raise SystemExit(f"{name} {version}: the wheel holds no {name}/__init__.py")
    return written


def vendor(name: str, version: str, data: bytes) -> None:
    with tempfile.TemporaryDirectory(dir=VENDOR) as tmp:
        staged = Path(tmp) / name
        written = unpack(data, name, version, staged)
        old = VENDOR / name
        if old.exists():
            shutil.rmtree(old)
        staged.rename(old)
    print(f"vendored {name} {version}: {len(written)} files")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vendor.py", description=__doc__.split("\n")[0])
    parser.add_argument("--wheels", type=Path, metavar="DIR",
                        help="take each wheel from DIR instead of PyPI")
    args = parser.parse_args(argv)
    for name, version in listed():
        if args.wheels is not None:
            data = wheel_from_dir(args.wheels, name, version)
        else:
            data = wheel_from_pypi(name, version)
        vendor(name, version, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
