"""Two backward readers written apart from `read_back`, kept simple so they are easy to
check by eye. The parity tests run them beside `read_back` over the same bytes and compare
what each hands back.

* `claude_walk` reads from the end in blocks to line 1, the limit checked before each
  block. It returns the non-blank lines newest first and how it stopped.
* `codex_walk` reads line 1 first, then from the end back to line 2, each line with
  whether it is the unterminated last one. It returns line 1, the pairs, and its two flags
  (`reached_start`, `hit_limit`).
"""

from __future__ import annotations

import os


def claude_walk(path, block: int, limit: int):
    lines = []
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        carry = b""
        scanned = 0
        while pos > 0:
            if scanned >= limit:
                return lines, "limit"
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + carry
            scanned += step
            parts = data.split(b"\n")
            carry = parts[0] if pos > 0 else b""
            whole = parts[1:] if pos > 0 else parts
            for raw in reversed(whole):
                if raw.strip():
                    lines.append(raw)
    return lines, "start"


def codex_walk(path, block: int, limit: int):
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        f.seek(0)
        chunk = b""
        while True:
            more = f.read(block)
            if not more:
                break
            chunk += more
            if b"\n" in chunk or len(chunk) > limit:
                break
        head, sep, _ = chunk.partition(b"\n")
        first_end = len(head) + len(sep)
        unterminated = False
        if size:
            f.seek(size - 1)
            unterminated = f.read(1) != b"\n"
        pos, scanned, carry, first = size, 0, b"", True
        pairs, hit_limit = [], False
        while pos > first_end:
            if scanned >= limit:
                hit_limit = True
                break
            step = min(block, pos - first_end)
            pos -= step
            f.seek(pos)
            data = f.read(step) + carry
            scanned += step
            parts = data.split(b"\n")
            if pos > first_end:
                carry, whole = parts[0], parts[1:]
            else:
                carry, whole = b"", parts
            for raw in reversed(whole):
                if not raw.strip():
                    continue
                pairs.append((raw, first and unterminated))
                first = False
        reached_start = not hit_limit and pos <= first_end
        return head, pairs, reached_start, hit_limit
