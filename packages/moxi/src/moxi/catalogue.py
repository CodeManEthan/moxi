"""The catalogue: Moxi's first-party modules, by name.

`catalogue.toml` ships in this package. It holds an integer `version` and one
`[[module]]` table per first-party module: its `name`, its `distribution`
(`moxi-<name>`) and a one-line `summary`. `moxi install NAME` installs a listed name's
distribution; a name the catalogue doesn't list needs `--from` or `--third-party`, so a
mistyped name never installs whatever the index holds under it.

`moxi modules --available --refresh` is the one command that fetches a newer copy, from
the repository's default branch (`URL`), with a ten-second timeout and a 256 KiB cap. A
fetched copy that checks and whose version is higher than the shipped one is written to
`CACHE/moxi/catalogue.toml`. Readers take whichever of the two has the higher version,
the shipped one on a tie. No other command uses the network.
"""

from __future__ import annotations

import os

from . import names

#: The shipped copy, beside this module.
SHIPPED = "catalogue.toml"
#: Where `--refresh` fetches a newer copy.
URL = (f"https://raw.githubusercontent.com/{names.ORG}/{names.HARNESS}/main/packages/"
       f"{names.HARNESS}/src/{names.HARNESS}/{SHIPPED}")
#: The fetch's timeout in seconds, and the most it reads.
TIMEOUT = 10.0
CAP = 256 * 1024


class CatalogueError(ValueError):
    """A catalogue that can't be read or doesn't check, or a refresh that failed."""


class Entry:
    """One first-party module: its name, its distribution and its one line."""

    __slots__ = ("name", "distribution", "summary")

    def __init__(self, name: str, distribution: str, summary: str):
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "distribution", distribution)
        object.__setattr__(self, "summary", summary)

    def __setattr__(self, key, value):
        raise AttributeError(f"cannot assign to field {key!r}")

    def __eq__(self, other):
        if other.__class__ is not self.__class__:
            return NotImplemented
        return (self.name, self.distribution, self.summary) == (
            other.name, other.distribution, other.summary)

    def __hash__(self):
        return hash((self.name, self.distribution, self.summary))

    def __repr__(self):
        return (f"Entry(name={self.name!r}, distribution={self.distribution!r}, "
                f"summary={self.summary!r})")


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
        entries.append(Entry(name, dist, summary.strip()))
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
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": names.HARNESS})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            body = response.read(CAP + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise CatalogueError(f"{url} is not published yet (404)") from None
        raise CatalogueError(f"{url} answered HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise CatalogueError(f"cannot fetch {url}: {reason}") from None
    if len(body) > CAP:
        raise CatalogueError(f"{url} is larger than {CAP // 1024} KiB; not read")
    return body


def refresh(env=None, *, fetch=None) -> tuple[Catalogue, str]:
    """Fetch the repository's copy and keep it when it is newer than the shipped one.

    Returns the catalogue in effect afterwards and one line saying what happened. Raises
    `CatalogueError` when the fetch fails or the fetched copy doesn't check; the copies
    on disk are then unchanged.
    """
    body = (fetch or _fetch)(URL)
    fetched = parse(body, URL)
    have = shipped()
    if fetched.version <= have.version:
        return load(env), (f"the published catalogue is version {fetched.version}; the "
                           f"shipped one (version {have.version}) stays")
    from . import files

    path = cached_path(env)
    files.atomic_write(path, body, mode=0o644)
    kept = parse(body, str(path))
    return kept, f"catalogue version {kept.version} kept in {path}"


__all__ = ["Catalogue", "CatalogueError", "Entry", "parse", "shipped", "cached",
           "cached_path", "load", "refresh", "URL", "TIMEOUT", "CAP", "SHIPPED"]
