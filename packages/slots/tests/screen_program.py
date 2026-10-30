"""A stand-in for a program that draws a dialog: it draws a fixture's rows in its
terminal and answers keys the way a dialog does, a set delay after each key.

    screen_program.py FIXTURE LOG [--choices A|B|C] [--delay S] [--on-enter exit|ignore|leave]

Rules (rows of only `─`) are drawn as wide as the terminal. `--choices` names the rows
the marker moves among, by their text without the marker; Up and Down move it one row,
`--delay` seconds after the key. Enter, after the same delay: `exit` ends the program
(status 0), `ignore` does nothing, `leave` clears the dialog and draws a plain prompt.
Every key read is appended to LOG as a word (`Up`, `Down`, `Enter`, or its bytes).
"""

from __future__ import annotations

import argparse
import os
import re
import select
import sys
import termios
import time
import tty

MARKER = "❯"
_ESC = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_INDEX = re.compile(r"\d+\.\s+")


def bare(row: str) -> str:
    text = row.strip()
    if text.startswith(MARKER):
        text = text[len(MARKER):].strip()
    found = _INDEX.match(text)
    return (text[found.end():] if found else text).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("log")
    ap.add_argument("--choices", default="")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--on-enter", default="exit", choices=("exit", "ignore", "leave"))
    args = ap.parse_args()
    rows = [_ESC.sub("", r).replace(" ", " ").rstrip()
            for r in open(args.fixture, encoding="utf-8").read().split("\n")]
    choices = [c for c in args.choices.split("|") if c]
    at = {bare(r): i for i, r in enumerate(rows) if bare(r) in choices}
    order = [at[c] for c in choices if c in at]
    sel = next((k for k, i in enumerate(order) if rows[i].lstrip().startswith(MARKER)), 0)
    column = rows[order[sel]].find(MARKER) if order else -1

    def row_text(i: int) -> str:
        if i not in order or column < 0:
            return rows[i]
        text = rows[i].replace(MARKER, " ").ljust(column + 1)
        if order.index(i) == sel:
            text = text[:column] + MARKER + text[column + 1:]
        return text

    def draw(lines: list[str] | None = None) -> None:
        cols, height = os.get_terminal_size()
        out = lines if lines is not None else [row_text(i) for i in range(len(rows))]
        out = [("─" * cols if r and set(r) == {"─"} else r[:cols]) for r in out]
        out = out[-(height - 1):]
        sys.stdout.write("\x1b[H\x1b[2J" + "\r\n".join(out))
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    pending: list[tuple[float, str]] = []
    try:
        draw()
        while True:
            now = time.monotonic()
            for due, key in [p for p in pending if p[0] <= now]:
                pending.remove((due, key))
                if key == "Enter":
                    if args.on_enter == "exit":
                        return 0
                    if args.on_enter == "leave":
                        draw(["", "─" * 10, "> ", "─" * 10, "  ready"])
                        order.clear()
                    continue
                if order:
                    step = 1 if key == "Down" else -1
                    sel = min(max(sel + step, 0), len(order) - 1)
                    draw()
            wait = min((p[0] for p in pending), default=now + 0.5) - now
            ready, _, _ = select.select([fd], [], [], max(wait, 0.01))
            if not ready:
                continue
            data = os.read(fd, 64)
            words = []
            while data:
                if data[:3] in (b"\x1b[A", b"\x1bOA"):
                    words.append("Up")
                    data = data[3:]
                elif data[:3] in (b"\x1b[B", b"\x1bOB"):
                    words.append("Down")
                    data = data[3:]
                elif data[:1] in (b"\r", b"\n"):
                    words.append("Enter")
                    data = data[1:]
                else:
                    words.append(repr(data))
                    data = b""
            with open(args.log, "a", encoding="utf-8") as log:
                for word in words:
                    log.write(word + "\n")
            for word in words:
                if word in ("Up", "Down", "Enter"):
                    pending.append((time.monotonic() + args.delay, word))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    sys.exit(main())
