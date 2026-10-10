"""One-request, authenticated local vision server; never adopts shared servers.

The small supervisor owns/reaps its MLX child when the caller's pipe closes,
including a caller crash. A deadline and cancellation monitor close that pipe.
No model download, preference change, or shared-server mutation occurs here.
"""
from __future__ import annotations

import fcntl
import http.client
import json
import math
import os
import secrets
import select
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

import auto_reply_ondevice as ondevice
from mlx_serve_lifecycle import validate_executable
from verify_model_provenance import fingerprint, load_manifest, verify_model

PORT = 11237
BASE_URL = f"http://127.0.0.1:{PORT}/v1"
MODEL_ID = "ddalcu/Qwen3.8-27B-MLX-Serve-4bit"
MAX_LIFETIME = 90.0
REVISION = "901aa73a1ff5456752c02e55582609e0040ca470"
CONTEXT_TOKENS = 32768
OUTPUT_TOKENS = 1024
ADMISSION_CODES = frozenset({
    "local_vision_platform_unavailable", "local_vision_deadline_invalid", "local_vision_busy",
    "local_vision_state_unsafe", "local_vision_port_in_use", "local_vision_executable_unverified",
    "local_vision_sandbox_unavailable", "local_vision_checkpoint_unavailable",
    "local_vision_memory_unavailable", "local_vision_memory_insufficient",
    "local_vision_checkpoint_mismatch", "local_vision_input_budget_exceeded",
    "local_vision_cleanup_required",
})
KEY_ENV = "ALDEN_OWNED_VISION_KEY"
SANDBOX = '(version 1) (allow default) (deny network-outbound) (allow network-outbound (remote ip "localhost:*"))'


class VisionUnavailable(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def vision_command(executable: Path, model_dir: Path) -> tuple[str, ...]:
    return (
        str(executable), "--serve", "--model", str(model_dir),
        "--model-dir", str(model_dir.parents[1]), "--host", "127.0.0.1",
        "--port", str(PORT), "--ctx-size", str(CONTEXT_TOKENS), "--max-concurrent", "1",
        "--max-resident-models", "1", "--max-resident-mem", "24GB",
        "--os-reserve-gib", "8", "--wired-margin-gib", "8",
        "--no-mtp", "--no-drafter", "--no-pld", "--prefix-cache-entries", "0",
        "--prefix-cache-disk", "off", "--tokenize-cache-entries", "0",
        "--kv-quant", "4", "--prefill-chunk", "512", "--timeout", "30",
        "--max-tokens", "1024", "--no-prevent-sleep",
        "--api-key-env", KEY_ENV, "--api-key-strict",
    )


@contextmanager
def _vision_lock(state_root: Path):
    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink():
        raise VisionUnavailable("local_vision_state_unsafe")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(root / "mlx-vision-request.lock", flags, 0o600)
    except OSError as exc:
        raise VisionUnavailable("local_vision_state_unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise VisionUnavailable("local_vision_state_unsafe")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise VisionUnavailable("local_vision_busy") from exc
        yield
    finally:
        os.close(descriptor)


def _port_available() -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", PORT))
        except OSError:
            return False
    return True


def _checkpoint_identity(model_dir: Path, manifest: dict) -> dict:
    metadata = model_dir.stat(follow_symlinks=False)
    files = {}
    for row in manifest["files"]:
        info = (model_dir / row["path"]).stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_size != row["bytes"]:
            raise VisionUnavailable("local_vision_checkpoint_mismatch")
        files[row["path"]] = list(fingerprint(info))
    return {"revision": REVISION, "directory": [metadata.st_dev, metadata.st_ino], "files": files}


def _verify_checkpoint(model_dir: Path, state_root: Path, check_cancelled):
    manifest = load_manifest(Path(__file__).with_name("alden-vision-core-manifest.json"))
    if manifest.get("repository") != MODEL_ID or manifest["revision"] != REVISION:
        raise VisionUnavailable("local_vision_checkpoint_mismatch")
    identity = _checkpoint_identity(model_dir, manifest)
    cache_path = Path(state_root) / "mlx-vision-verified-core.json"
    cached = ondevice._private_json(cache_path)
    if cached != identity:
        report = verify_model(model_dir, manifest, check_cancelled=check_cancelled)
        if not report["ok"] or _checkpoint_identity(model_dir, manifest) != identity:
            raise VisionUnavailable("local_vision_checkpoint_mismatch")
        # Cache only verified immutable file identities, never prompts or keys.
        # Every request rechecks inode/size/mtime/ctime/mode; a changed file is
        # hashed again against the pinned publisher manifest before loading.
        descriptor, temporary = tempfile.mkstemp(prefix=".mlx-vision-core-", dir=state_root)
        try:
            with os.fdopen(descriptor, "w") as output:
                json.dump(identity, output)
            os.replace(temporary, cache_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return manifest, identity


def _catalog(key: str, timeout: float, transport):
    req = urllib.request.Request(BASE_URL + "/models", headers={"Authorization": "Bearer " + key})
    with transport(req, timeout=timeout) as response:
        raw = response.read(ondevice.MLX_GATEWAY_MAX_RESPONSE_BYTES + 1)
    if len(raw) > ondevice.MLX_GATEWAY_MAX_RESPONSE_BYTES:
        raise VisionUnavailable("local_vision_catalog_invalid")
    return json.loads(raw)["data"]


def _owns_listener(pid: int) -> bool:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), f"-iTCP:{PORT}", "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True, timeout=2.0, check=False,
    )
    return result.returncode == 0 and str(pid) in result.stdout.split()


def _reap_stranded_group(group: int, command: tuple[str, ...]) -> bool:
    """After abnormal supervision, signal only a still-verifiable own group."""
    members = subprocess.run(["/usr/bin/pgrep", "-g", str(group)], capture_output=True, text=True, timeout=1, check=False)
    if members.returncode == 1 and not members.stdout.strip():
        return True
    if members.returncode != 0:
        return False
    pids = members.stdout.split()
    if not pids or any(not pid.isdigit() for pid in pids):
        return False
    rows = subprocess.run(["/bin/ps", "-ww", "-p", ",".join(pids), "-o", "pgid=,stat=,args="], capture_output=True, text=True, timeout=1, check=False)
    if rows.returncode != 0:
        return False
    expected = " ".join(command)
    active = False
    for line in rows.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or parts[0] != str(group):
            return False
        if parts[1].startswith("Z"):
            continue
        if parts[2] != expected:
            return False
        active = True
    if active:
        os.killpg(group, signal.SIGKILL)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        remaining = subprocess.run(["/bin/ps", "-p", ",".join(pids), "-o", "stat="], capture_output=True, text=True, timeout=1, check=False)
        if remaining.returncode == 1 or (remaining.stdout.strip() and all(s.startswith("Z") for s in remaining.stdout.split())):
            return True
        time.sleep(.05)
    return False


def input_byte_budget(system_prompt: str, images: list[Path]) -> int:
    # Qwen's byte-level tokenizer cannot exceed one token per UTF-8 byte.
    # Image bounds follow the pinned16px patches/2x merge and pixel limits.
    tokens = len(system_prompt.encode("utf-8")) + OUTPUT_TOKENS + 512
    for path in images:
        result = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True, timeout=2, check=False)
        import re
        width = re.search(r"pixelWidth:\s*(\d+)", result.stdout)
        height = re.search(r"pixelHeight:\s*(\d+)", result.stdout)
        if result.returncode or width is None or height is None:
            raise VisionUnavailable("local_vision_input_budget_exceeded")
        w, h = int(width[1]), int(height[1])
        if min(w, h) <= 0 or max(w, h) / min(w, h) > 200:
            raise VisionUnavailable("local_vision_input_budget_exceeded")
        scale = max(1.0, math.sqrt(65536 / (w * h)))
        scale = min(scale, math.sqrt(16777216 / (w * h)))
        tokens += (math.ceil(w * scale / 32) + 1) * (math.ceil(h * scale / 32) + 1) + 64
    if tokens > CONTEXT_TOKENS:
        raise VisionUnavailable("local_vision_input_budget_exceeded")
    return CONTEXT_TOKENS - tokens


def check_input_budget(system_prompt: str, prompt: bytes, images: list[Path]):
    if len(prompt) > input_byte_budget(system_prompt, images):
        raise VisionUnavailable("local_vision_input_budget_exceeded")


def _owns_peer_socket(pid: int, client_port: int) -> bool:
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid), f"-iTCP:{PORT}", "-sTCP:ESTABLISHED", "-Fn"],
        capture_output=True, text=True, timeout=1.0, check=False,
    )
    return result.returncode == 0 and f"n127.0.0.1:{PORT}->127.0.0.1:{client_port}" in result.stdout.splitlines()


class _OwnedResponse:
    def __init__(self, response, connection):
        self.response, self.connection = response, connection
        self.status, self.headers = response.status, response.headers

    def read(self, size=-1):
        return self.response.read(size)

    def close(self):
        self.response.close()
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _bound_transport(child_pid: int, supervisor, check_cancelled):
    def open_request(request, *, timeout):
        parsed = urllib.parse.urlsplit(request.full_url)
        if (parsed.scheme != "http" or parsed.netloc != f"127.0.0.1:{PORT}"
                or parsed.path not in {"/v1/models", "/v1/chat/completions"}
                or parsed.query or parsed.fragment):
            raise VisionUnavailable("local_vision_endpoint_invalid")
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=timeout)
        try:
            check_cancelled()
            if supervisor.poll() is not None:
                raise VisionUnavailable("local_vision_owner_gone")
            connection.connect()
            client_port = connection.sock.getsockname()[1]
            deadline = time.monotonic() + min(timeout, 1.0)
            while not _owns_peer_socket(child_pid, client_port):
                check_cancelled()
                if supervisor.poll() is not None or time.monotonic() >= deadline:
                    raise VisionUnavailable("local_vision_connection_unattested")
                time.sleep(0.01)
            # This established socket is owned by the child. A replacement
            # listener cannot inherit it after the child exits. No credentials
            # or pixels were written before this connection-level attestation.
            check_cancelled()
            connection.request(request.get_method(), parsed.path, body=request.data, headers=dict(request.header_items()))
            response = connection.getresponse()
            wrapped = _OwnedResponse(response, connection)
            if response.status >= 300:
                raise urllib.error.HTTPError(request.full_url, response.status, response.reason, response.headers, wrapped)
            return wrapped
        except urllib.error.HTTPError:
            raise
        except BaseException:
            connection.close()
            raise
    return open_request


@contextmanager
def owned_vision_endpoint(state_root: Path, *, timeout: float, check_cancelled=lambda: None):
    """Yield URL, ID, capabilities, key, remaining seconds, bound transport.

    The caller must already hold its ordinary MLX request lease throughout.
    Authentication stays in memory. Logs live in a private temporary directory
    and are removed after cleanup; no private pixels/prompts are logged here.
    """
    if sys.platform != "darwin":
        raise VisionUnavailable("local_vision_platform_unavailable")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise VisionUnavailable("local_vision_deadline_invalid")
    deadline = time.monotonic() + min(float(timeout), MAX_LIFETIME)
    executable = Path("/Applications/MLX-Serve.app/Contents/MacOS/mlx-serve")
    model_dir = Path.home() / ".mlx-serve/models" / MODEL_ID
    global_root = Path.home() / "Library/Caches/Alden/local-vision"
    check_cancelled()
    with _vision_lock(state_root), _vision_lock(global_root):
        if (global_root / "mlx-vision-cleanup-required").exists():
            raise VisionUnavailable("local_vision_cleanup_required")
        if not _port_available():
            raise VisionUnavailable("local_vision_port_in_use")
        if validate_executable(executable):
            raise VisionUnavailable("local_vision_executable_unverified")
        if not Path("/usr/bin/sandbox-exec").is_file():
            raise VisionUnavailable("local_vision_sandbox_unavailable")
        if not model_dir.is_dir() or model_dir.is_symlink():
            raise VisionUnavailable("local_vision_checkpoint_unavailable")
        try:
            available = ondevice.detect_memory_budget().usable_bytes
        except Exception as exc:
            raise VisionUnavailable("local_vision_memory_unavailable") from exc
        if available < ondevice.QWEN38_27B_REQUIRED_BYTES:
            raise VisionUnavailable("local_vision_memory_insufficient")
        if time.monotonic() >= deadline:
            raise VisionUnavailable("local_vision_timeout")
        def check_budget():
            check_cancelled()
            if time.monotonic() >= deadline:
                raise VisionUnavailable("local_vision_timeout")
        try:
            manifest, identity = _verify_checkpoint(model_dir, state_root, check_budget)
        except (OSError, ValueError, TypeError) as exc:
            raise VisionUnavailable("local_vision_checkpoint_mismatch") from exc
        check_budget()
        try:
            if ondevice.detect_memory_budget().usable_bytes < ondevice.QWEN38_27B_REQUIRED_BYTES:
                raise VisionUnavailable("local_vision_memory_insufficient")
        except VisionUnavailable:
            raise
        except Exception as exc:
            raise VisionUnavailable("local_vision_memory_unavailable") from exc
        if not _port_available():
            raise VisionUnavailable("local_vision_port_in_use")
        with tempfile.TemporaryDirectory(prefix="alden-owned-vision-") as temporary:
            key = secrets.token_urlsafe(32)
            env = os.environ.copy()
            env[KEY_ENV] = key
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env.pop(name, None)
            command = vision_command(executable, model_dir)
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--supervise", str(deadline),
                 str(Path(temporary) / "server.log"), json.dumps(command)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=env, close_fds=True, start_new_session=True,
            )
            finished = threading.Event()
            interrupted: list[BaseException] = []
            close_lock = threading.Lock()

            def close_input():
                with close_lock:
                    if process.stdin is not None and not process.stdin.closed:
                        process.stdin.close()

            def monitor():
                while not finished.wait(0.05):
                    try:
                        check_cancelled()
                        if time.monotonic() >= deadline:
                            raise VisionUnavailable("local_vision_timeout")
                    except BaseException as exc:
                        interrupted.append(exc)
                        close_input()
                        return

            watcher = threading.Thread(target=monitor, name="alden-vision-deadline", daemon=True)
            try:
                watcher.start()
                ready, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
                if not ready:
                    raise VisionUnavailable("local_vision_timeout")
                line = process.stdout.readline(64)
                try:
                    child_pid = int(line)
                except ValueError as exc:
                    raise VisionUnavailable("local_vision_launch_failed") from exc
                if child_pid <= 1:
                    raise VisionUnavailable("local_vision_launch_failed")
                transport = _bound_transport(child_pid, process, check_budget)
                while time.monotonic() < deadline:
                    check_cancelled()
                    if interrupted:
                        raise interrupted[0]
                    if process.poll() is not None:
                        raise VisionUnavailable("local_vision_launch_failed")
                    selected = None
                    try:
                        if not _owns_listener(child_pid):
                            time.sleep(0.05)
                            continue
                        rows = _catalog(key, min(1.0, max(0.01, deadline - time.monotonic())), transport)
                        for row in rows:
                            if (isinstance(row, dict) and row.get("id") in {MODEL_ID, "mlx/" + MODEL_ID, MODEL_ID.rsplit("/", 1)[1]}
                                    and row.get("loaded") is True and "vision" in row.get("capabilities", [])):
                                selected = row
                                break
                    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                        pass
                    if selected is not None:
                        check_budget()
                        yield BASE_URL, selected["id"], selected["capabilities"], key, max(0.01, deadline - time.monotonic()), transport
                        check_budget()
                        if interrupted:
                            raise interrupted[0]
                        if _checkpoint_identity(model_dir, manifest) != identity:
                            raise VisionUnavailable("local_vision_checkpoint_changed")
                        return
                    time.sleep(0.1)
                raise VisionUnavailable("local_vision_timeout")
            finally:
                close_input()
                finished.set()
                if watcher.ident is not None:
                    watcher.join(timeout=0.2)
                # The supervisor reaps only its own child before exiting.
                try:
                    status = process.wait(timeout=8.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        status = process.wait(timeout=8.0)
                    except subprocess.TimeoutExpired:
                        # Popen still owns an unreaped session leader. Its
                        # process group contains only this request's children.
                        os.killpg(process.pid, signal.SIGKILL)
                        status = process.wait(timeout=3.0)
                if process.stdout is not None:
                    process.stdout.close()
                if status != 0:
                    try:
                        reaped = _reap_stranded_group(process.pid, command)
                    except (OSError, subprocess.SubprocessError):
                        reaped = False
                    if not reaped:
                        # An uncertain cleanup fences later allocations, even
                        # in another worker process. No foreign PID is signaled.
                        (global_root / "mlx-vision-cleanup-required").touch(mode=0o600, exist_ok=True)
                    if interrupted:
                        raise interrupted[0]
                    raise VisionUnavailable("local_vision_cleanup_failed")


def _supervise(deadline: float, log_path: Path, command: list[str]) -> int:
    """Internal child owner: pipe EOF, deadline, or TERM always reaps MLX."""
    child = None

    def stop_signal(_signum, _frame):
        raise InterruptedError

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    try:
        readable, _, _ = select.select([sys.stdin.buffer], [], [], 0)
        if time.monotonic() >= deadline or (readable and not os.read(sys.stdin.fileno(), 1)):
            return 0
        with log_path.open("xb") as log:
            child = subprocess.Popen(
                ["/usr/bin/sandbox-exec", "-p", SANDBOX, *command],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True,
            )
            print(child.pid, flush=True)
            while child.poll() is None and time.monotonic() < deadline:
                readable, _, _ = select.select([sys.stdin.buffer], [], [], 0.1)
                if readable and not os.read(sys.stdin.fileno(), 1):
                    break
    except (OSError, InterruptedError):
        return 1
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3.0)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 5 or sys.argv[1] != "--supervise":
        raise SystemExit(2)
    raise SystemExit(_supervise(float(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[4])))
