"""The phone bridge: a browser reaches a gateway-mode test shell's gateway as a phone does.

The gateway listens on a Unix socket or a loopback port, which no browser opens as
`https://<host>`. So the test kit stands in for the tunnel's provider, and nothing in
`moxi_ui.gateway` knows it is under test. `PhoneBridge(home, target)` runs two listeners
on `127.0.0.1` ports it picks:

- a TLS terminator holding a certificate for `gw.moxi.test` from a CA made for this
  bridge, which copies each connection's bytes to `target` (the gateway's port, or its
  socket's path) unchanged, as a provider's proxy does. When nothing listens there, it
  answers a request with a plain-text 502, as Serve and `cloudflared` do;
- an HTTP `CONNECT` proxy that tunnels `gw.moxi.test`, on any port, to the terminator and
  refuses every other name.

`bridge.launch(browser_type)` starts an engine with its proxy set to the bridge, so the
phone's URL is `https://gw.moxi.test/` and no host file or resolver changes. Chromium also
takes `--ignore-certificate-errors-spki-list` with the leaf's key, so it counts the page as
a secure one, with passkeys and a worker; WebKit and Firefox take `ignore_https_errors`.

The address: `write_address(shell, target=...)` writes a real tunnel record into the
shell's state, as `moxi tunnel add` leaves it, so the gateway's own `address()` reads
`gw.moxi.test`. `set_gateway(shell, enabled=..., port=...)` writes the gateway's settings
into the shell's config file. Its provider is `cloudflare` by default, whose uid is this
user's, so the gateway's provider check passes the terminator's connections as they are.

Without a browser, `bridge.pair(shell)` pairs a phone with no passkey, through the desk's
pair route and the gateway's open routes, and gives a `PhoneSession` over the bridge.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import http.client
import json
import os
import socket
import ssl
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from moxi import files

#: The phone's host name: the tunnel address every bridge test uses.
HOST = "gw.moxi.test"
#: The phone's origin at the default port.
ORIGIN = f"https://{HOST}"
#: The largest request head the 502 answer reads.
_HEAD_MAX = 64 * 1024
_BAD_GATEWAY = (b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\n"
                b"Content-Length: 11\r\nConnection: close\r\n\r\nBad Gateway")
#: The device each engine plays: Chromium a Pixel and WebKit an iPhone, with touch and the
#: phone's user agent; Firefox, which has no phone engine, desktop Firefox at 360 px.
DEVICES: dict[str, dict[str, Any]] = {
    "chromium": {"viewport": {"width": 412, "height": 839}, "device_scale_factor": 2.625,
                 "is_mobile": True, "has_touch": True,
                 "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"},
    "webkit": {"viewport": {"width": 393, "height": 659}, "device_scale_factor": 3,
               "is_mobile": True, "has_touch": True,
               "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
                             "Mobile/15E148 Safari/604.1"},
    "firefox": {"viewport": {"width": 360, "height": 740}},
}


def _certificates(where: Path) -> tuple[Path, Path, Path, str]:
    """A CA made here and a leaf for `HOST` it signs: the chain and key files the
    terminator serves, the CA's file, and the leaf key's SPKI hash (base64 SHA-256)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    start, end = now - datetime.timedelta(days=1), now + datetime.timedelta(days=30)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "moxi test bridge CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(start).not_valid_after(end)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                         critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True,
                                       crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, encipher_only=False,
                                       decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)]))
            .issuer_name(ca_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    chain, key_file, ca_file = where / "chain.pem", where / "key.pem", where / "ca.pem"
    files.atomic_write(chain, leaf.public_bytes(pem) + ca.public_bytes(pem))
    files.atomic_write(key_file, key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                   serialization.NoEncryption()))
    files.atomic_write(ca_file, ca.public_bytes(pem))
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    return chain, key_file, ca_file, base64.b64encode(hashlib.sha256(spki).digest()).decode()


class PhoneBridge:
    """The two listeners of the module doc. `target` is the gateway's TCP port, or its
    socket's path; a test may change it while the bridge runs, and each new connection
    goes where it then names. Use it as a context manager, or `start()` and `stop()`."""

    __test__ = False

    def __init__(self, home: str | os.PathLike, target: int | str | None = None):
        self.home = Path(home)
        self.target = target
        chain, key, self.ca_file, self.spki = _certificates(self.home / "bridge")
        self._tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._tls.load_cert_chain(chain, key)
        self._tls.set_alpn_protocols(["http/1.1"])
        self._listeners: list[socket.socket] = []
        self._open: set[socket.socket] = set()
        self._lock = threading.Lock()
        self.terminator_port = 0
        self.proxy_port = 0

    def __enter__(self) -> PhoneBridge:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    @property
    def proxy(self) -> dict[str, str]:
        """Playwright's `proxy` option for this bridge."""
        return {"server": f"http://127.0.0.1:{self.proxy_port}"}

    def start(self) -> PhoneBridge:
        terminator = self._listen(self._terminate)
        self.terminator_port = terminator.getsockname()[1]
        self.proxy_port = self._listen(self._connect).getsockname()[1]
        return self

    def stop(self) -> None:
        """Close both listeners and end every connection through them. A connection is
        only shut down, as `cut` does; the thread that serves it closes it."""
        with self._lock:
            listeners, held = self._listeners, list(self._open)
            self._listeners, self._open = [], set()
        for listener in listeners:
            _close(listener)
        for sock in held:
            _shut(sock)

    def cut(self) -> None:
        """End every open connection through the bridge, as a provider that restarts
        does; the listeners stay.

        It shuts each socket down, which ends every read and write on it at once, and
        leaves the closing to the threads that serve it (`_pump`). A socket closed here
        could give its number to the next connection the bridge accepts while a pump
        thread is still on its way to read the old one; that thread would then take the
        new connection's bytes, and the new connection's TLS would fail."""
        with self._lock:
            held, self._open = list(self._open), set()
        for sock in held:
            _shut(sock)

    # -- browsers -------------------------------------------------------------------------

    def launch(self, browser_type: Any, **options: Any) -> Any:
        """Launch `browser_type` (a Playwright browser type) headless, its proxy set to
        this bridge, with Chromium taking the leaf's key as valid."""
        options.setdefault("proxy", self.proxy)
        if browser_type.name == "chromium":
            options["args"] = [*options.get("args", ()),
                               f"--ignore-certificate-errors-spki-list={self.spki}"]
        return browser_type.launch(**options)

    @staticmethod
    def context_args(engine: str, **extra: Any) -> dict[str, Any]:
        """A context's options for `engine`: its phone (`DEVICES`), the phone's origin as
        the base URL, and `ignore_https_errors` for the engines that take no SPKI flag."""
        args: dict[str, Any] = {**DEVICES[engine], "base_url": ORIGIN}
        if engine != "chromium":
            args["ignore_https_errors"] = True
        args.update(extra)
        return args

    # -- without a browser ----------------------------------------------------------------

    def connection(self, *, timeout: float = 10.0) -> http.client.HTTPSConnection:
        """An HTTPS connection to `https://gw.moxi.test/` through the terminator, checked
        against this bridge's CA."""
        context = ssl.create_default_context(cafile=str(self.ca_file))
        conn = http.client.HTTPSConnection(HOST, timeout=timeout, context=context)
        raw = socket.create_connection(("127.0.0.1", self.terminator_port), timeout=timeout)
        conn.sock = context.wrap_socket(raw, server_hostname=HOST)
        return conn

    def pair(self, shell: Any, name: str = "test phone") -> PhoneSession:
        """Pair a phone with no passkey: the desk's pair route mints the code, and the
        gateway's begin and finish redeem it. Returns the phone's session."""
        status, data = shell.session().post("/m/ui/api/gateway/pair")
        if status != 200:
            raise AssertionError(f"the desk's pair route answered {status}: {data}")
        code = urlsplit(data["url"]).fragment.removeprefix("c=")
        phone = PhoneSession(self)
        status, _, begun = phone.request("POST", "/api/pair/begin", {"code": code, "name": name})
        if status != 200:
            raise AssertionError(f"the pairing's begin answered {status}")
        status, headers, done = phone.request(
            "POST", "/api/pair/finish", {"pairing": begun["pairing"], "credential": None})
        if status != 200:
            raise AssertionError(f"the pairing's finish answered {status}")
        phone.cookie = headers["set-cookie"].split(";", 1)[0]
        phone.key, phone.device = done["key"], done["device"]
        return phone

    # -- the listeners --------------------------------------------------------------------

    def _listen(self, handle: Any) -> socket.socket:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        self._listeners.append(listener)
        threading.Thread(target=self._accept, args=(listener, handle), daemon=True,
                         name="moxi-ui-phone-bridge").start()
        return listener

    def _accept(self, listener: socket.socket, handle: Any) -> None:
        while True:
            try:
                sock, _ = listener.accept()
            except OSError:
                return
            self._keep(sock)
            threading.Thread(target=self._guarded, args=(handle, sock), daemon=True,
                             name="moxi-ui-phone-bridge-conn").start()

    def _guarded(self, handle: Any, sock: socket.socket) -> None:
        try:
            handle(sock)
        except (OSError, ssl.SSLError, ValueError):
            _close(sock)

    def _keep(self, sock: socket.socket) -> None:
        with self._lock:
            self._open.add(sock)

    def _upstream(self) -> socket.socket | None:
        target = self.target
        try:
            if isinstance(target, str):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(target)
                return sock
            if target is None:
                return None
            return socket.create_connection(("127.0.0.1", int(target)), timeout=5)
        except OSError:
            return None

    def _terminate(self, raw: socket.socket) -> None:
        tls = self._tls.wrap_socket(raw, server_side=True)
        try:
            self._keep(tls)
            upstream = self._upstream()
            if upstream is None:
                _read_head(tls)
                tls.sendall(_BAD_GATEWAY)
                return
            upstream.settimeout(None)
            self._keep(upstream)
            _pump(tls, upstream)
        finally:
            _close(tls)

    def _connect(self, client: socket.socket) -> None:
        head = _read_head(client)
        line = head.split(b"\r\n", 1)[0].decode("latin-1").split()
        host = line[1].rsplit(":", 1)[0].lower() if len(line) == 3 else ""
        if line[:1] != ["CONNECT"] or host != HOST:
            client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n"
                           b"Connection: close\r\n\r\n")
            _close(client)
            return
        upstream = socket.create_connection(("127.0.0.1", self.terminator_port))
        try:
            self._keep(upstream)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            _pump(client, upstream)
        finally:
            _close(upstream)


def _read_head(sock: socket.socket) -> bytes:
    head = b""
    while b"\r\n\r\n" not in head and len(head) < _HEAD_MAX:
        part = sock.recv(4096)
        if not part:
            break
        head += part
    return head


def _pump(one: socket.socket, other: socket.socket) -> None:
    """Copy bytes both ways until either side ends, then close both.

    The direction that ends first only shuts both sockets down, which ends the other
    direction's read or write; the sockets are closed here once both directions have
    stopped. Closed any sooner, a socket's number can go to the next connection the bridge
    accepts while the other direction is still about to use it (`PhoneBridge.cut`)."""
    def copy(src: socket.socket, dst: socket.socket) -> None:
        try:
            while data := src.recv(65536):
                dst.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            _shut(src)
            _shut(dst)

    back = threading.Thread(target=copy, args=(other, one), daemon=True,
                            name="moxi-ui-phone-bridge-pump")
    back.start()
    copy(one, other)
    back.join()
    _close(one)
    _close(other)


def _shut(sock: socket.socket) -> None:
    """End every read and write on `sock`, from any thread, without closing it. For a TLS
    socket this is the plain socket's shutdown: `SSLSocket.shutdown` would also drop the
    TLS state a pump thread may be reading through."""
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except (OSError, ValueError):
        pass


def _close(sock: socket.socket) -> None:
    """Shut `sock` down and close it; only the thread that serves it calls this."""
    _shut(sock)
    try:
        sock.close()
    except OSError:
        pass


class PhoneSession:
    """A paired phone with no browser, over the bridge: the cookie and key a pairing gave,
    and the headers the phone's browser sends."""

    def __init__(self, bridge: PhoneBridge):
        self.bridge = bridge
        self.cookie = ""
        self.key = ""
        self.device = ""

    def headers(self, method: str = "GET") -> dict[str, str]:
        headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors",
                   "Sec-Fetch-Dest": "empty"}
        if self.cookie:
            headers["Cookie"] = self.cookie
            headers["X-Moxi-Key"] = self.key
        if method != "GET":
            headers["Origin"] = ORIGIN
            headers["Content-Type"] = "application/json"
        return headers

    def request(self, method: str, path: str, body: Any = None,
                headers: Mapping[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
        """One request: `(status, headers, data)`, the headers' names lower-cased and
        `data` the parsed JSON, else the body's bytes."""
        conn = self.bridge.connection()
        try:
            payload = None if body is None else json.dumps(body).encode()
            conn.request(method, path, body=payload,
                         headers={**self.headers(method), **(headers or {})})
            answer = conn.getresponse()
            raw = answer.read()
        finally:
            conn.close()
        found = {name.lower(): value for name, value in answer.getheaders()}
        data: Any = raw
        if raw and "json" in found.get("content-type", ""):
            data = json.loads(raw)
        return answer.status, found, data

    def get(self, path: str) -> tuple[int, Any]:
        status, _, data = self.request("GET", path)
        return status, data

    def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        status, _, data = self.request("POST", path, {} if body is None else body)
        return status, data

    @contextmanager
    def stream(self, path: str, *, timeout: float = 10.0) -> Iterator[Any]:
        """A server-sent event stream over the bridge, open for the `with` block."""
        from .shell import Stream

        conn = self.bridge.connection(timeout=timeout)
        try:
            conn.request("GET", path, headers={**self.headers(), "Accept": "text/event-stream"})
            yield Stream(conn.getresponse())
        finally:
            conn.close()


# -- the shell's tunnel record and settings ---------------------------------------------


def write_address(shell: Any, *, target: int | str, port: int = 443, host: str = HOST,
                  provider: str = "cloudflare", state: str = "on",
                  retired: Sequence[tuple[str, int]] = ()) -> Path:
    """Write the shell's tunnel record with the one entry `ui/gateway` at `host:port`,
    forwarding to `target` (a port: the `http` form; a path: `unix`), and the `retired`
    origins, as `moxi tunnel add` leaves it. Each write is a new change token."""
    record = shell.state_dir("tunnel") / "applied.json"
    try:
        old = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {"format": 1, "id": os.urandom(16).hex(), "last_generation": 0}
    generation = int(old.get("last_generation", 0)) + 1
    form = "unix" if isinstance(target, str) else "http"
    entry = {"reach": "own-devices", "provider": provider, "form": form, "host": host,
             "port": port, "target": target, "state": state, "generation": generation,
             "from": None, "displaced": None, "detail": None,
             "applied_at": datetime.datetime.now(datetime.timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ")}
    data = {**old, "last_generation": generation, "warned_ct": [], "cloudflared_started": None,
            "retired": [{"host": h, "port": p} for h, p in retired],
            "entries": {"ui/gateway": entry}}
    files.atomic_write(record, json.dumps(data, indent=2) + "\n")
    return record


def remove_address(shell: Any) -> None:
    """Take `ui/gateway` out of the shell's tunnel record, as `moxi tunnel remove` does."""
    record = shell.state_dir("tunnel") / "applied.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["entries"] = {}
    data["last_generation"] = int(data.get("last_generation", 0)) + 1
    files.atomic_write(record, json.dumps(data, indent=2) + "\n")


def set_gateway(shell: Any, *, enabled: bool | None = None, port: int | None = None) -> Path:
    """Write the gateway's settings, `gateway.enabled` and `gateway.port`, into the shell's
    config file, keeping its other values. The file is the one `moxi config set` writes;
    writing it here applies no unit, which on Linux and macOS `moxi config set` of the
    switch would try."""
    path = shell.config_file("ui")
    with files.edit_toml(path) as doc:
        table = doc.setdefault("gateway", {})
        if enabled is not None:
            table["enabled"] = enabled
        if port is not None:
            table["port"] = port
    return path
