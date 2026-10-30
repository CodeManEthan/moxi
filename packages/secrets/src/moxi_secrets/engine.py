"""The engine: RE2 where google-re2 imports, Python's re where it does not.

Every pattern runs on RE2 through the google-re2 binding when the `re2` extra is
installed. Without it the gitleaks-format sets run on Python's re in a worker child with
a deadline (see `moxi_secrets.fallback`), and private names and personal data run on
Python's re in this process.

`Text` is one scan's view of the caller's text: lone surrogates read as U+FFFD (as Go
reads an invalid byte), the UTF-8 bytes encoded once, byte offsets mapped back to `str`
offsets, and lines found through one newline index. Offsets and secrets always come from
the caller's own text, which has the same length as the engine's.

Standard library only at import; `re2` is imported at first use.
"""

from __future__ import annotations

import os
import platform
import re
import struct
import sys
from bisect import bisect_left

#: The first CPython minor version google-re2 ships no wheel for. The `re2` extra's
#: marker in pyproject.toml carries the same bound; raise both together.
RE2_BOUND = (3, 15)

_SURROGATE = re.compile("[\ud800-\udfff]")
_NON_ASCII = re.compile("[^\x00-\x7f]")


# -- where a google-re2 wheel installs ------------------------------------------------------

def platform_facts() -> dict:
    """This interpreter's answers to the questions `re2_wheel_fits` asks."""
    import sysconfig

    try:
        libc = os.confstr("CS_GNU_LIBC_VERSION")
    except (AttributeError, ValueError, OSError):
        # Windows has no confstr; musl raises ValueError for the name.
        libc = None
    return {
        "implementation": platform.python_implementation(),
        "version": sys.version_info[:2],
        "gil_disabled": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "system": platform.system(),
        "machine": platform.machine(),
        "bits": struct.calcsize("P") * 8,
        "libc": libc,
        "mac": platform.mac_ver()[0] if sys.platform == "darwin" else "",
    }


def _version(text: str | None) -> tuple:
    """`"2.39"` or `"glibc 2.39"` as (2, 39); () when there is none."""
    if not text:
        return ()
    found = re.search(r"(\d+)(?:\.(\d+))?", text)
    if not found:
        return ()
    return (int(found.group(1)), int(found.group(2) or 0))


def re2_wheel_fits(facts: dict | None = None) -> bool:
    """True where the newest google-re2 release the `re2` extra allows has a wheel.

    That release (1.1.20251105) ships wheels for CPython below `RE2_BOUND`, not
    free-threaded, on glibc Linux 2.28 or later (x86_64, aarch64), macOS 13 or later
    (arm64, x86_64) and Windows (x86, x64, and arm64 from CPython 3.11). Anywhere else an
    install would build the sdist, which needs the C++ libraries, so the extra is not
    offered there. `facts` replaces `platform_facts()` (for tests).
    """
    f = platform_facts() if facts is None else facts
    if f["implementation"] != "CPython" or tuple(f["version"]) >= RE2_BOUND:
        return False
    if f["gil_disabled"]:
        return False
    system, machine, bits = f["system"], f["machine"].lower(), f["bits"]
    if system == "Linux":
        libc = f["libc"]
        if not libc or not libc.startswith("glibc"):
            return False
        return (_version(libc) >= (2, 28) and bits == 64
                and machine in ("x86_64", "amd64", "aarch64", "arm64"))
    if system == "Darwin":
        return _version(f["mac"]) >= (13, 0) and machine in ("arm64", "x86_64")
    if system == "Windows":
        if bits == 32:
            return True
        if machine in ("arm64", "aarch64"):
            return tuple(f["version"]) >= (3, 11)
        return machine in ("amd64", "x86_64")
    return False


# -- which engine runs ----------------------------------------------------------------------

_re2 = None   # None: not tried yet; False: will not import; else the module


def re2_module():
    """The google-re2 module, or None when it is not installed or will not import."""
    global _re2
    if _re2 is None:
        try:
            import re2 as module
        except Exception:  # noqa: BLE001 - a broken install counts as no install
            module = False
        _re2 = module
    return _re2 or None


def reset() -> None:
    """Forget the import attempt, so the next scan tries `import re2` again (for tests)."""
    global _re2, _active
    _re2 = None
    _active = None


class Engine:
    """One way to run patterns: `compile`, then search `target(text)`, mapping offsets."""

    name = ""
    #: The key of a pattern's text in sets.json: "pattern" (verbatim) or "fallback".
    key = ""

    def __init__(self):
        self._cache: dict = {}

    def compile(self, pattern: str):
        found = self._cache.get(pattern)
        if found is None:
            found = self._cache[pattern] = self._compile(pattern)
        return found

    def target(self, t: Text):
        raise NotImplementedError

    def path_target(self, path: str):
        """`path` as this engine searches it, mapped as a text is: a lone surrogate (a
        file name byte that is not UTF-8) reads as U+FFFD."""
        return self.target(Text(path))

    def to_str(self, t: Text, offset: int) -> int:
        raise NotImplementedError

    def next_start(self, t: Text, offset: int) -> int:
        """The offset one character after `offset` in `target(t)`."""
        raise NotImplementedError


class Re2Engine(Engine):
    """RE2 over the scan's UTF-8 bytes; spans come back as byte offsets."""

    name = "re2"
    key = "pattern"

    def __init__(self, module):
        super().__init__()
        self.re2 = module

    def _compile(self, pattern: str):
        # A str pattern compiles in UTF-8 mode; the compiled object then searches bytes
        # without encoding them again.
        return self.re2.compile(pattern)

    def target(self, t: Text):
        return t.data

    def to_str(self, t: Text, offset: int) -> int:
        return t.str_pos(offset)

    def next_start(self, t: Text, offset: int) -> int:
        data = t.data
        if offset >= len(data):
            return offset + 1
        lead = data[offset]
        return offset + (1 if lead < 0xC0 else 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4)

    def set_of(self, patterns):
        """An RE2 set over `patterns`, or None when the binding refuses to build one."""
        re2 = self.re2
        try:
            found = re2.Set.SearchSet(re2.Options())
            for pattern in patterns:
                found.Add(pattern)
            found.Compile()
        except Exception:  # noqa: BLE001 - the caller searches each pattern instead
            return None
        return found


class ReEngine(Engine):
    """Python's re over the scan's engine text; spans are `str` offsets already."""

    name = "re"
    key = "fallback"

    def _compile(self, pattern: str):
        return re.compile(pattern)

    def target(self, t: Text):
        return t.etext

    def to_str(self, t: Text, offset: int) -> int:
        return offset

    def next_start(self, t: Text, offset: int) -> int:
        return offset + 1


def _re2_version() -> str:
    try:
        from importlib.metadata import version

        return version("google-re2")
    except Exception:  # noqa: BLE001
        return "unknown"


_active = None
_python = ReEngine()


def active() -> Engine:
    """RE2 when it imports, else Python's re."""
    global _active
    if _active is None:
        module = re2_module()
        _active = Re2Engine(module) if module is not None else _python
    return _active


def python() -> ReEngine:
    """The Python re engine (the fallback, and the worker child's)."""
    return _python


# -- one scan's text ------------------------------------------------------------------------

class Text:
    """The caller's text as the engines see it, built once per scan."""

    __slots__ = ("text", "etext", "ascii", "_data", "_qs", "_cum", "_ps", "_newlines",
                 "_lower")

    def __init__(self, text: str):
        self.text = text
        self.ascii = text.isascii()
        # A lone surrogate (a byte decoded with surrogateescape) reads as U+FFFD, one
        # character for one, so `str` offsets are the same in both.
        self.etext = text if self.ascii else _SURROGATE.sub("�", text)
        self._data = None
        self._qs = None
        self._newlines = None
        self._lower = None

    @property
    def data(self) -> bytes:
        """The engine text's UTF-8, encoded once."""
        if self._data is None:
            self._data = self.etext.encode("utf-8")
        return self._data

    def _index(self) -> None:
        # For each non-ASCII character: its byte offset (qs), its str offset (ps), and the
        # extra bytes of every non-ASCII character up to and including it (cum).
        qs, ps, cum = [], [], []
        extra = 0
        for found in _NON_ASCII.finditer(self.etext):
            p = found.start()
            code = ord(found.group())
            ps.append(p)
            qs.append(p + extra)
            extra += 1 if code < 0x800 else 2 if code < 0x10000 else 3
            cum.append(extra)
        self._qs, self._ps, self._cum = qs, ps, cum

    def str_pos(self, b: int) -> int:
        """The `str` offset of byte offset `b`, a character boundary of `data`."""
        if self.ascii:
            return b
        if self._qs is None:
            self._index()
        k = bisect_left(self._qs, b)
        return b - (self._cum[k - 1] if k else 0)

    def byte_pos(self, s: int) -> int:
        """The byte offset of `str` offset `s`."""
        if self.ascii:
            return s
        if self._qs is None:
            self._index()
        k = bisect_left(self._ps, s)
        return s + (self._cum[k - 1] if k else 0)

    @property
    def newlines(self) -> list:
        if self._newlines is None:
            out, find, text, i = [], self.etext.find, self.etext, 0
            while True:
                i = find("\n", i)
                if i < 0:
                    break
                out.append(i)
                i += 1
            self._newlines = out
            del text
        return self._newlines

    def line(self, pos: int) -> int:
        """The 1-based line of `str` offset `pos`."""
        return bisect_left(self.newlines, pos) + 1 if pos else 1

    @property
    def lower(self) -> str:
        """The engine text lowercased, for the keyword gate. A dotted capital I lowers to
        two characters in Python and to one in Go; reading it as one only lets more rules
        in."""
        if self._lower is None:
            lower = self.etext.lower()
            if not self.ascii:
                lower = lower.replace("i̇", "i")
            self._lower = lower
        return self._lower


def re2_literal(text: str) -> str:
    """`text` as an RE2 pattern that matches it literally: ASCII letters and digits as
    they are, every other character as `\\x{...}`."""
    return "".join(c if c.isascii() and c.isalnum() else "\\x{%x}" % ord(c) for c in text)
