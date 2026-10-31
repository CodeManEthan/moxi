"""A stub `codex` binary for the host entry's process tests: an app-server and a window.

`codex app-server --listen unix://SOCK [-c KEY=VALUE]...` serves a WebSocket on the unix
socket with the app-server methods the host calls, and
`codex -c ... resume THREAD --remote unix://SOCK` is a window that stays up until told
to quit. No model runs: a turn completes a moment after it starts.

Driven by the environment it inherits from the host:

    FAKE_CODEX_DIR        a folder for its records:
                            events.jsonl   one line per process event
                            calls.jsonl    every request the app-server answered
                            threads.json   the threads, so a later app-server resumes them
                            quit           made by a test: the window exits 0
    FAKE_CODEX_UNTRUSTED  set: `hooks/list` lists every turns hook as untrusted
    FAKE_CODEX_REPORT     a JSON sandbox policy every load answer reports, in place of
                          the one the load asked for
    FAKE_CODEX_ECHO       a variable name: its value in the app-server's environment is
                          written into the app-server's start event
    FAKE_CODEX_LIMITS     JSON: what `account/rateLimits/read` answers (else `LIMITS`);
                          `error` answers it with an error

Each event records the process's pid, parent, process group and session, and whether
its stdin is a terminal.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import tomllib
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
#: What `account/rateLimits/read` answers by default: the shape Codex 0.153 gave.
LIMITS = {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 12,
                                                         "windowDurationMins": 300}},
          "accountId": "acct-fake", "planType": "pro"}


def install(folder: Path) -> Path:
    """Write an executable `codex` into `folder` that runs this stub."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "codex"
    path.write_text(f"#!{sys.executable}\n"
                    "import sys\n"
                    f"sys.path.insert(0, {str(HERE)!r})\n"
                    "import fake_codex\n"
                    "sys.exit(fake_codex.main(sys.argv[1:]))\n")
    path.chmod(0o755)
    return path


def records() -> Path | None:
    value = os.environ.get("FAKE_CODEX_DIR")
    return Path(value) if value else None


def event(kind: str, **fields) -> None:
    folder = records()
    if folder is None:
        return
    try:
        stdin_tty = os.isatty(0)
    except OSError:
        stdin_tty = None
    line = {"event": kind, "pid": os.getpid(), "ppid": os.getppid(),
            "pgid": os.getpgrp(), "sid": os.getsid(0), "stdin_tty": stdin_tty,
            "t": time.time(), **fields}
    with open(folder / "events.jsonl", "a") as f:
        f.write(json.dumps(line) + "\n")


def events(folder: Path, kind: str | None = None) -> list[dict]:
    path = folder / "events.jsonl"
    if not path.exists():
        return []
    found = [json.loads(line) for line in path.read_text().splitlines() if line]
    return [e for e in found if kind is None or e["event"] == kind]


def calls(folder: Path) -> list[dict]:
    path = folder / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        print("codex-cli 0.160.0 (stub)")
        return 0
    if argv[:1] == ["app-server"]:
        socket = argv[argv.index("--listen") + 1].removeprefix("unix://")
        return AppServer(socket, argv).run()
    if "resume" in argv and "--remote" in argv:
        return window(argv[argv.index("resume") + 1])
    print(f"fake codex: unknown command {argv}", file=sys.stderr)
    return 2


# -- the app-server ----------------------------------------------------------------------

class _Error(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AppServer:
    def __init__(self, socket: str, argv: list[str]):
        self.socket = socket
        self.keys = _hook_keys(argv)
        self.lock = threading.RLock()
        self.conns: list = []
        self.threads: dict = {}
        self.store = records() / "threads.json" if records() is not None else None
        if self.store is not None and self.store.exists():
            self.threads = json.loads(self.store.read_text())
            for thread in self.threads.values():
                thread["loaded"] = None

    def run(self) -> int:
        from websockets.sync.server import unix_serve

        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        server = unix_serve(self.handle, path=self.socket, compression=None)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        echo = os.environ.get("FAKE_CODEX_ECHO")
        event("app-server", echo=os.environ.get(echo) if echo else None)
        while not stop.wait(0.05):
            pass
        server.shutdown()
        event("app-server-exit")
        return 0

    def handle(self, ws) -> None:
        with self.lock:
            self.conns.append(ws)
        try:
            for raw in ws:
                message = json.loads(raw)
                rid = message.get("id")
                if rid is None:
                    continue
                method, params = message.get("method"), message.get("params") or {}
                folder = records()
                if folder is not None:
                    with open(folder / "calls.jsonl", "a") as f:
                        f.write(json.dumps({"pid": os.getpid(), "method": method,
                                            "params": params}) + "\n")
                try:
                    frame = {"id": rid, "result": self.answer(method, params)}
                except _Error as exc:
                    frame = {"id": rid, "error": {"code": exc.code,
                                                  "message": exc.message}}
                self.send(ws, frame)
        except Exception:  # noqa: BLE001 - a dropped connection
            pass
        finally:
            with self.lock:
                if ws in self.conns:
                    self.conns.remove(ws)

    def send(self, ws, frame: dict) -> None:
        with self.lock:
            try:
                ws.send(json.dumps(frame))
            except Exception:  # noqa: BLE001
                pass

    def notify(self, method: str, params: dict) -> None:
        with self.lock:
            conns = list(self.conns)
        for ws in conns:
            self.send(ws, {"jsonrpc": "2.0", "method": method, "params": params})

    def save(self) -> None:
        if self.store is not None:
            kept = {tid: {k: v for k, v in t.items() if k != "loaded"}
                    for tid, t in self.threads.items()}
            self.store.write_text(json.dumps(kept))

    def answer(self, method: str, params: dict) -> dict:
        if method == "initialize":
            return {"userAgent": "fake/0.160.0"}
        if method == "hooks/list":
            trust = "untrusted" if os.environ.get("FAKE_CODEX_UNTRUSTED") else "trusted"
            hooks = [{"key": key, "enabled": True, "trustStatus": trust}
                     for key in self.keys]
            return {"data": [{"hooks": hooks}]}
        if method == "thread/start":
            thread = {"id": str(uuid.uuid4()), "cwd": params.get("cwd"), "turns": [],
                      "loaded": _policy(params)}
            with self.lock:
                self.threads[thread["id"]] = thread
                self.save()
            return self.load_answer(thread)
        if method == "thread/resume":
            thread = self.thread(params)
            with self.lock:
                if thread.get("loaded") is None:
                    if not thread["turns"]:
                        raise _Error(-32600, f"no rollout found for thread id "
                                             f"{thread['id']}")
                    thread["loaded"] = _policy(params)
            return self.load_answer(thread)
        if method == "turn/start":
            thread = self.thread(params)
            turn = {"id": str(uuid.uuid4()), "status": "inProgress"}
            with self.lock:
                thread["turns"].append(turn)
                self.save()
            self.notify("turn/started", {"threadId": thread["id"], "turn": dict(turn)})
            threading.Timer(0.1, self.complete, args=(thread, turn)).start()
            return {"turn": dict(turn)}
        if method == "turn/steer":
            raise _Error(-32600, "no active turn to steer")
        if method == "thread/turns/list":
            thread = self.thread(params)
            return {"data": [dict(t) for t in reversed(thread["turns"])]}
        if method == "thread/read":
            thread = self.thread(params)
            return {"thread": {"id": thread["id"], "turns": []}}
        if method == "thread/backgroundTerminals/list":
            return {"data": []}
        if method == "account/rateLimits/read":
            given = os.environ.get("FAKE_CODEX_LIMITS")
            if given == "error":
                raise _Error(-32603, "rate limits are not available")
            return json.loads(given) if given else LIMITS
        raise _Error(-32601, f"method not found: {method}")

    def thread(self, params: dict) -> dict:
        with self.lock:
            thread = self.threads.get(params.get("threadId"))
        if thread is None:
            raise _Error(-32600, f"thread not found: {params.get('threadId')}")
        return thread

    def load_answer(self, thread: dict) -> dict:
        report = os.environ.get("FAKE_CODEX_REPORT")
        sandbox = json.loads(report) if report else thread["loaded"]
        return {"thread": {"id": thread["id"], "status": {"type": "idle"},
                           "cwd": thread["cwd"], "turns": []},
                "sandbox": sandbox, "cwd": thread["cwd"]}

    def complete(self, thread: dict, turn: dict) -> None:
        with self.lock:
            turn["status"] = "completed"
            self.save()
        self.notify("turn/completed", {"threadId": thread["id"], "turn": dict(turn)})


def _policy(params: dict) -> dict:
    mode = params.get("sandbox")
    settings = (params.get("config") or {}).get("sandbox_workspace_write") or {}
    if mode == "workspace-write":
        return {"type": "workspaceWrite",
                "writableRoots": list(settings.get("writable_roots") or []),
                "networkAccess": bool(settings.get("network_access"))}
    if mode == "danger-full-access":
        return {"type": "dangerFullAccess"}
    return {"type": "readOnly", "networkAccess": False}


def _hook_keys(argv: list[str]) -> list[str]:
    """The hook keys the host trusts, from its `-c hooks.state={...}`."""
    for i, flag in enumerate(argv[:-1]):
        value = argv[i + 1]
        if flag == "-c" and value.startswith("hooks.state="):
            return list(tomllib.loads(f"v = {value.partition('=')[2]}")["v"])
    return []


# -- the window --------------------------------------------------------------------------

def window(thread_id: str) -> int:
    try:
        foreground = os.tcgetpgrp(0) == os.getpgrp()
    except OSError:
        foreground = None
    event("window", thread=thread_id, foreground=foreground)
    quit_file = records() / "quit" if records() is not None else None

    def ended(*_):
        event("window-exit", why="SIGTERM")
        os._exit(0)

    signal.signal(signal.SIGTERM, ended)
    while True:
        time.sleep(0.05)
        if quit_file is not None and quit_file.exists():
            quit_file.unlink()
            event("window-exit", why="quit")
            return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
