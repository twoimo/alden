"""Hermetic isolation for the auto-reply runtime test module.

The runtime under test resolves its state from HOME, OPENKAKAO_* variables and a
few hard-coded loopback endpoints. A test run must never read or write the
operator's real account data and must never reach the live local model server.

``begin_test`` gives every test a fresh empty HOME, clears every OPENKAKAO_*
variable, and restores the whole environment afterwards. ``install_guards``
(called once per module) wraps the socket and file-open entry points:

* an outbound connection is allowed only to a loopback port that this process
  bound itself (a test fake); the live model port and every other endpoint are
  refused;
* opening a file, or connecting a sqlite database, under the real home directory
  is refused unless the path is inside this repository checkout or the test's
  own temporary HOME.

A refused operation raises ``HermeticViolation`` and is also recorded. Production
code often swallows broad exceptions, so the recorded violations are checked in
each test's cleanup as well, and any violation fails the test even when the
production code caught the error.
"""

import builtins
import io
import os
import pwd
import socket
import sqlite3
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIVE_MODEL_PORT = 11234
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
ENVIRONMENT_PREFIXES = ("OPENKAKAO",)


class HermeticViolation(AssertionError):
    """A test tried to reach the network or a real user path."""


def _real_home() -> str:
    # Resolved from the passwd database, before any test rewrites HOME.
    return os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)


REAL_HOME = _real_home()
_STATE = {
    "violations": [],
    "bound_ports": set(),
    "temp_home": None,
    "installed": False,
    "originals": {},
}


def violations() -> list[str]:
    return list(_STATE["violations"])


def _record(message: str) -> None:
    _STATE["violations"].append(message)
    raise HermeticViolation(message)


def _under(path: str, base: str) -> bool:
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def _path_is_forbidden(raw) -> bool:
    if isinstance(raw, int):
        return False
    try:
        text = os.fspath(raw)
    except TypeError:
        return False
    if isinstance(text, bytes):
        text = os.fsdecode(text)
    if not text or text == ":memory:" or text.startswith("file:"):
        return False
    resolved = os.path.realpath(os.path.abspath(text))
    if not _under(resolved, REAL_HOME):
        return False
    if _under(resolved, str(ROOT)):
        return False
    temp_home = _STATE["temp_home"]
    if temp_home and _under(resolved, os.path.realpath(temp_home)):
        return False
    return True


def _check_path(raw, operation: str) -> None:
    if _path_is_forbidden(raw):
        _record(f"hermetic: {operation} touched the real HOME: {os.fspath(raw)!r}")


def _check_connect(sock, address) -> None:
    family = sock.family
    if family == socket.AF_UNIX:
        _check_path(address, "connect(AF_UNIX)")
        return
    if family not in (socket.AF_INET, socket.AF_INET6):
        _record(f"hermetic: connect on unsupported family {family}")
    host, port = address[0], address[1]
    if host not in LOOPBACK_HOSTS:
        _record(f"hermetic: non-loopback connect to {host}:{port}")
    if port == LIVE_MODEL_PORT:
        _record(f"hermetic: connect to the live model server {host}:{port}")
    if port not in _STATE["bound_ports"]:
        _record(f"hermetic: loopback connect to {host}:{port} which this test did not bind")


def install_guards() -> None:
    """Install process guards for one test module. Idempotent per module run."""
    if _STATE["installed"]:
        return
    originals = _STATE["originals"]
    originals["socket.connect"] = socket.socket.connect
    originals["socket.connect_ex"] = socket.socket.connect_ex
    originals["socket.bind"] = socket.socket.bind
    originals["sqlite3.connect"] = sqlite3.connect
    originals["io.open"] = io.open
    originals["builtins.open"] = builtins.open
    originals["os.open"] = os.open

    def connect(self, address):
        _check_connect(self, address)
        return originals["socket.connect"](self, address)

    def connect_ex(self, address):
        _check_connect(self, address)
        return originals["socket.connect_ex"](self, address)

    def bind(self, address):
        result = originals["socket.bind"](self, address)
        if self.family in (socket.AF_INET, socket.AF_INET6):
            _STATE["bound_ports"].add(self.getsockname()[1])
        return result

    def guarded_sqlite_connect(database, *args, **kwargs):
        _check_path(database, "sqlite3.connect")
        return originals["sqlite3.connect"](database, *args, **kwargs)

    def guarded_open(file, *args, **kwargs):
        _check_path(file, "open")
        return originals["io.open"](file, *args, **kwargs)

    def guarded_os_open(path, *args, **kwargs):
        _check_path(path, "os.open")
        return originals["os.open"](path, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.bind = bind
    sqlite3.connect = guarded_sqlite_connect
    io.open = guarded_open
    builtins.open = guarded_open
    os.open = guarded_os_open
    _STATE["installed"] = True


def uninstall_guards() -> None:
    if not _STATE["installed"]:
        return
    originals = _STATE["originals"]
    socket.socket.connect = originals["socket.connect"]
    socket.socket.connect_ex = originals["socket.connect_ex"]
    socket.socket.bind = originals["socket.bind"]
    sqlite3.connect = originals["sqlite3.connect"]
    io.open = originals["io.open"]
    builtins.open = originals["builtins.open"]
    os.open = originals["os.open"]
    _STATE["installed"] = False


def seed_empty_context_store(home: Path) -> Path:
    """Create the empty, owner-only decision store an operator machine always has.

    The runtime opens ``~/Library/Application Support/openkakao/context.sqlite3``
    read-only and refuses anything that is not a regular 0600 single-link file.
    A fresh HOME has none, so an empty store with the ``reply_decisions`` table
    stands in for the real store: every terminal-decision lookup then answers
    "no prior decision", which is what these tests assume.
    """
    store_dir = home / "Library" / "Application Support" / "openkakao"
    store_dir.mkdir(parents=True, exist_ok=True)
    store_dir.chmod(0o700)
    store = store_dir / "context.sqlite3"
    connection = sqlite3.connect(str(store))
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reply_decisions(
                event_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                decision TEXT, reason TEXT, category TEXT,
                reply TEXT, sent_at TEXT
            )
            """
        )
        connection.commit()
    finally:
        connection.close()
    store.chmod(0o600)
    return store


def begin_test(testcase) -> Path:
    """Give one test an empty HOME, a clean OPENKAKAO_* environment and fresh guards.

    Registers the cleanups on ``testcase`` that restore the environment and fail
    the test on any recorded violation. Returns the temporary HOME.
    """
    install_guards()
    saved_environment = dict(os.environ)
    home = Path(tempfile.mkdtemp(prefix="auto-reply-home-"))
    for key in list(os.environ):
        if key.startswith(ENVIRONMENT_PREFIXES):
            del os.environ[key]
    os.environ["HOME"] = str(home)
    _STATE["temp_home"] = str(home)
    seed_empty_context_store(home)
    _STATE["violations"] = []
    _STATE["bound_ports"] = set()

    def restore() -> None:
        os.environ.clear()
        os.environ.update(saved_environment)
        _STATE["temp_home"] = None
        _STATE["bound_ports"] = set()

    def assert_clean() -> None:
        found = list(_STATE["violations"])
        _STATE["violations"] = []
        if found:
            testcase.fail("hermetic guard tripped:\n  " + "\n  ".join(found))

    testcase.addCleanup(restore)
    testcase.addCleanup(lambda: _remove_tree(home))
    testcase.addCleanup(assert_clean)
    return home


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)
