"""Reading a session record backwards, in blocks, to a limit: `read_back`.

A clock wants the newest line of some kind, which in a long record is near the end, so it
reads the file from the end in fixed blocks and stops at the first line that settles its
question, or at a byte limit. `read_back` is that read, once, for every harness. It hands
its caller everything a reader of either harness uses, and decides nothing:

* each non-blank line as raw bytes, newest first, with whether it is the file's last line
  and the file does not end in a newline (`unterminated`): a harness may be writing it;
* line 1, read first, when asked (`head=True`): a Codex rollout's line 1 says whether the
  file is the thread's whole history, and the backward read then stops at line 2;
* how the read ended (`ended`): `start` when every line back to its stopping line was
  yielded, `limit` when the byte limit stopped it with lines left, None while lines remain
  or when the caller stopped first.

Which line counts, and what each ending means, are the caller's. A read that hit the
limit is not a read of the whole file, and only the caller knows what that costs it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

#: The values of `Reader.ended`.
START = "start"
LIMIT = "limit"

_DONE = object()


def read_back(path: str | Path, *, block: int, limit: int, head: bool = False) -> Reader:
    """Open `path` and return a `Reader` over its lines, newest first.

    `block` is the size of each backward read in bytes, and `limit` the most bytes the
    backward read takes: it is checked before each block, so a read stops once it has taken
    `limit` bytes or more. With `head=True`, line 1 is read first, from the start of the
    file, by the same block size and under a limit of its own of `limit` bytes; it is held
    as `Reader.first` and the backward read stops at line 2.

    The file's size is taken when it is opened; bytes appended after that are not read.
    Raises OSError when the file cannot be opened or read, and ValueError on a block under
    1 or a negative limit. Use the reader as a context manager, or iterate it to its end,
    to close the file.
    """
    return Reader(path, block=block, limit=limit, head=head)


class Reader:
    """One backward read. Iterate it for `(raw, unterminated)` pairs, newest first.

    `raw` is the line's bytes without its newline. `unterminated` is True only for the
    file's last line when the file does not end in a newline; that line is yielded like
    any other, whether or not it parses. Blank lines are skipped and no other line is.

    `first` is line 1's bytes without its newline when `head=True` (`b""` for an empty
    file), and None without `head` or when line 1 does not end within the head's limit;
    the read then ends `limit` with no line yielded.

    `ended` is None until the read is over: `start` once the line the read stops at (line
    1, or line 2 with `head`) has been yielded or there was none to yield, `limit` once the
    limit stopped it with lines left. It is set as soon as the reader knows, which can be
    while the caller holds the last line it will get. A caller that stops iterating before
    then sees None.
    """

    def __init__(self, path: str | Path, *, block: int, limit: int, head: bool = False):
        if not isinstance(block, int) or isinstance(block, bool) or block < 1:
            raise ValueError(f"block must be a positive number of bytes, not {block!r}")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError(f"limit must be zero or more bytes, not {limit!r}")
        self.path = Path(path)
        self.block = block
        self.limit = limit
        self.first: bytes | None = None
        self.ended: str | None = None
        self._file = open(self.path, "rb")
        try:
            self._size = os.fstat(self._file.fileno()).st_size
            self._unterminated = False
            if self._size:
                self._file.seek(self._size - 1)
                self._unterminated = self._file.read(1) != b"\n"
            self._stop: int | None = 0
            if head:
                self._read_head()
        except BaseException:
            self._file.close()
            raise
        self._lines = self._backward()
        self._next: object = None
        self._primed = False

    # -- line 1 ------------------------------------------------------------------------

    def _read_head(self) -> None:
        """Read line 1 into `first` and set where the backward read stops."""
        f = self._file
        f.seek(0)
        chunk = bytearray()
        while len(chunk) < self._size and len(chunk) < self.limit:
            more = f.read(min(self.block, self._size - len(chunk)))
            if not more:
                self._size = len(chunk)          # the file shrank under the read
                break
            searched = len(chunk)
            chunk += more
            newline = chunk.find(b"\n", searched)
            if newline >= 0:
                self.first = bytes(chunk[:newline])
                self._stop = newline + 1
                return
        if len(chunk) >= self._size:
            # The whole file is line 1, with no newline (or the file is empty).
            self.first = bytes(chunk)
            self._stop = self._size
            return
        # Line 1 does not end within the limit: the backward read has no line 2 to stop at.
        self._stop = None
        self.ended = LIMIT

    # -- the backward read -------------------------------------------------------------

    def _backward(self) -> Iterator[tuple[bytes, bool]]:
        stop = self._stop
        if stop is None:
            return
        f = self._file
        pos = self._size
        scanned = 0
        carry = b""
        at_end = True                     # the next piece is the one that ends the file
        while pos > stop:
            if scanned >= self.limit:
                self.ended = LIMIT
                return
            step = min(self.block, pos - stop)
            pos -= step
            f.seek(pos)
            data = f.read(step) + carry
            scanned += step
            pieces = data.split(b"\n")
            if pos > stop:
                # The first piece may be the tail of a line that starts in an earlier block.
                carry = pieces[0]
                whole = pieces[1:]
            else:
                carry = b""
                whole = pieces
            for raw in reversed(whole):
                last = at_end
                at_end = False
                if not raw.strip():
                    continue
                yield raw, last and self._unterminated
        self.ended = START

    # -- iteration ---------------------------------------------------------------------

    def __iter__(self) -> Reader:
        return self

    def __next__(self) -> tuple[bytes, bool]:
        if not self._primed:
            self._primed = True
            self._next = self._advance()
        item = self._next
        if item is _DONE:
            raise StopIteration
        # Look one line ahead, so `ended` is set while the caller holds the last line.
        self._next = self._advance()
        return item  # type: ignore[return-value]

    def _advance(self) -> object:
        if self._file.closed:
            return _DONE
        try:
            return next(self._lines)
        except StopIteration:
            self.close()
            return _DONE
        except BaseException:
            self.close()
            raise

    # -- closing -----------------------------------------------------------------------

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> Reader:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return (f"Reader({str(self.path)!r}, block={self.block}, limit={self.limit}, "
                f"ended={self.ended!r})")
