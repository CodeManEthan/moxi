"""The catalogue: Moxi's first-party modules, by name.

`catalogue.toml` ships in this package. It holds an integer `version` and one
`[[module]]` table per first-party module: its `name`, its `distribution`
(`moxi-<name>`) and a one-line `summary`. `moxi install NAME` installs a listed name's
distribution; a name the catalogue doesn't list needs `--from` or `--third-party`, so a
mistyped name never installs whatever the index holds under it.

An entry may also say `unreleased = true`: the module is not on the package index yet,
so `moxi install NAME` refuses it unless `--from` gives its source.

`moxi modules --available --refresh` is the one command that fetches a newer copy, and
only one a released core shipped, since the repository's main branch holds unreleased
work. It lists the repository's core release tags, `moxi-v<version>` (`TAGS_URL`, no
token), and from the highest version down, at most `TRIES` of them, asks the package
index whether that version of moxi is published (`INDEX_URL`); it fetches the
catalogue at the first that is (`FILE_URL`). Each read has a ten-second timeout and a
256 KiB cap. A fetched copy that checks and whose version is higher than the shipped one
is written to `CACHE/moxi/catalogue.toml`. Readers take whichever of the two has the
higher version, the shipped one on a tie. No other command uses the network.
"""

from __future__ import annotations

import os

from . import names

#: The shipped copy, beside this module.
SHIPPED = "catalogue.toml"
#: The core's release tags start with this: its distribution, then `-v`. The release tag
#: scheme is moxi-publishing's; the core spells it here because it imports no module.
TAG_PREFIX = f"{names.HARNESS}-v"
#: The repository's tags that start with `TAG_PREFIX`.
TAGS_URL = (f"https://api.github.com/repos/{names.OWNER}/{names.HARNESS}/git/"
            f"matching-refs/tags/{TAG_PREFIX}")
#: The package index's page of one version of the core; a 404 means not published.
INDEX_URL = f"https://pypi.org/pypi/{names.HARNESS}/{{version}}/json"
#: The shipped catalogue at a release tag.
FILE_URL = (f"https://raw.githubusercontent.com/{names.OWNER}/{names.HARNESS}/{{tag}}/"
            f"packages/{names.HARNESS}/src/{names.HARNESS}/{SHIPPED}")
#: How many release tags, from the highest down, the refresh asks the index about.
TRIES = 3
#: Each read's timeout in seconds, and the most it reads.
TIMEOUT = 10.0
CAP = 256 * 1024


class CatalogueError(ValueError):
    """A catalogue that can't be read or doesn't check, or a refresh that failed."""


class NotFound(CatalogueError):
    """A read that answered 404."""


class Entry:
    """One first-party module: its name, its distribution, its one line, and whether it
    is still unreleased (not on the package index yet)."""

    __slots__ = ("name", "distribution", "summary", "unreleased")

    def __init__(self, name: str, distribution: str, summary: str, *,
                 unreleased: bool = False):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "distribution", distribution)
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "unreleased", unreleased)

    def __setattr__(self, key, value):
        raise AttributeError(f"cannot assign to field {key!r}")

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return (self.name, self.distribution, self.summary, self.unreleased) == (
            other.name, other.distribution, other.summary, other.unreleased)

    def __hash__(self):
        return hash((self.name, self.distribution, self.summary, self.unreleased))

    def __repr__(self):
        return (f"Entry(name={self.name!r}, distribution={self.distribution!r}, "
                f"summary={self.summary!r}"
                + (", unreleased=True)" if self.unreleased else ")"))


class Catalogue:
    """A read catalogue: its version, its entries in file order and where it came from."""

    __slots__ = ("version", "entries", "source")

    def __init__(self, version: int, entries: tuple[Entry, ...], source: str):
        self.version = version
        self.entries = entries
        #: "shipped", or the path of the refreshed copy.
        self.source = source

    def get(self, name: str) -> Entry | None:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]

    def suggest(self, name: str) -> list[str]:
        """The listed names closest to `name`, nearest first."""
        import difflib

        return difflib.get_close_matches(name, self.names(), n=3, cutoff=0.6)


def parse(text: str | bytes, where: str = SHIPPED) -> Catalogue:
    """Read and check a catalogue's text. Raises `CatalogueError` naming `where`."""
    import tomllib

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CatalogueError(f"{where} is not UTF-8 text: {exc}") from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CatalogueError(f"{where} is not TOML: {exc}") from None
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CatalogueError(f"{where} has no integer version of 1 or more "
                             f"(it has {version!r})")
    rows = data.get("module", [])
    if not isinstance(rows, list):
        raise CatalogueError(f"{where}: module is not a list of [[module]] tables")
    entries: list[Entry] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise CatalogueError(f"{where}: module {i} is not a table")
        name, dist, summary = row.get("name"), row.get("distribution"), row.get("summary")
        if not names.is_module_name(name) or names.is_reserved(name):
            raise CatalogueError(f"{where}: module {i} has the name {name!r}, which is no "
                                 f"module name")
        if name in seen:
            raise CatalogueError(f"{where} lists {name} twice")
        seen.add(name)
        if dist != names.distribution(name):
            raise CatalogueError(f"{where}: {name}'s distribution is {dist!r}; it must be "
                                 f"{names.distribution(name)}")
        if not isinstance(summary, str) or not summary.strip() or "\n" in summary:
            raise CatalogueError(f"{where}: {name}'s summary must be one non-empty line")
        unreleased = row.get("unreleased", False)
        if not isinstance(unreleased, bool):
            raise CatalogueError(f"{where}: {name}'s unreleased must be true or false")
        entries.append(Entry(name, dist, summary.strip(), unreleased=unreleased))
    return Catalogue(version, tuple(entries), where)


def shipped() -> Catalogue:
    """The copy that ships in this package."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SHIPPED)
    with open(path, "rb") as fh:
        catalogue = parse(fh.read(), SHIPPED)
    return Catalogue(catalogue.version, catalogue.entries, "shipped")


def cached_path(env=None):
    """Where `--refresh` keeps a newer copy: `CACHE/moxi/catalogue.toml`."""
    from . import paths

    return paths.core().cache_dir(env) / SHIPPED


def cached(env=None) -> Catalogue | None:
    """The refreshed copy, or None when there is none or it doesn't check."""
    path = cached_path(env)
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    try:
        return parse(raw, str(path))
    except CatalogueError:
        return None


def load(env=None) -> Catalogue:
    """The catalogue in effect: the higher version of the shipped and refreshed copies."""
    have = shipped()
    newer = cached(env)
    if newer is not None and newer.version > have.version:
        return newer
    return have


def _fetch(url: str) -> bytes:
    """GET `url` within `TIMEOUT`, reading at most `CAP` bytes. Raises `CatalogueError`."""
    import http.client
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": names.HARNESS})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            body = response.read(CAP + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotFound(f"{url} is not published yet (404)") from None
        raise CatalogueError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, http.client.HTTPException, OSError,
            ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise CatalogueError(f"cannot fetch {url}: {reason}") from None
    if len(body) > CAP:
        raise CatalogueError(f"{url} is larger than {CAP // 1024} KiB; not read")
    return body


def _release_versions(body: bytes) -> list[tuple[int, ...]]:
    """The versions of the core's release tags in a matching-refs answer, highest first.
    Only plain release versions (`0.1.7`) count; a pre-release is no catalogue to keep."""
    import json
    import re

    try:
        refs = json.loads(body)
    except ValueError as exc:
        raise CatalogueError(f"{TAGS_URL} is not JSON: {exc}") from None
    found = set()
    for ref in refs if isinstance(refs, list) else []:
        name = ref.get("ref") if isinstance(ref, dict) else None
        if not isinstance(name, str) or not name.startswith("refs/tags/" + TAG_PREFIX):
            continue
        version = name[len("refs/tags/" + TAG_PREFIX):]
        if re.fullmatch(r"(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*", version):
            found.add(tuple(int(part) for part in version.split(".")))
    return sorted(found, reverse=True)


def _published(body: bytes) -> bool:
    import json

    try:
        data = json.loads(body)
    except ValueError:
        return False
    files = data.get("urls") if isinstance(data, dict) else None
    return isinstance(files, list) and any(
        isinstance(f, dict) and not f.get("yanked") for f in files)


def refresh(env=None, *, fetch=None) -> tuple[Catalogue, str]:
    """Fetch the catalogue of the highest released core and keep it when it is newer than
    the shipped one.

    Returns the catalogue in effect afterwards and one line saying what happened. Raises
    `CatalogueError` when a read fails, when no release tag of the core among the highest
    `TRIES` is published, or when the fetched copy doesn't check; the copies on disk are
    then unchanged.
    """
    fetch = fetch or _fetch
    url = None
    for version in _release_versions(fetch(TAGS_URL))[:TRIES]:
        text = ".".join(str(part) for part in version)
        try:
            page = fetch(INDEX_URL.format(version=text))
        except NotFound:
            continue
        if _published(page):
            url = FILE_URL.format(tag=TAG_PREFIX + text)
            break
    if url is None:
        raise NotFound("no released version of moxi was found (a release tag whose "
                       "version is on the package index)")
    body = fetch(url)
    fetched = parse(body, url)
    have = shipped()
    if fetched.version <= have.version:
        return load(env), (f"the released catalogue is version {fetched.version}; the "
                           f"shipped one (version {have.version}) stays")
    from . import files

    path = cached_path(env)
    files.atomic_write(path, body, mode=0o644)
    kept = parse(body, str(path))
    return kept, f"catalogue version {kept.version} kept in {path}"


__all__ = ["Catalogue", "CatalogueError", "NotFound", "Entry", "parse", "shipped",
           "cached", "cached_path", "load", "refresh", "TAG_PREFIX", "TAGS_URL",
           "INDEX_URL", "FILE_URL", "TRIES", "TIMEOUT", "CAP", "SHIPPED"]
