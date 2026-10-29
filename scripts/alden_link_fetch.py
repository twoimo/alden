"""Bounded, public-IP-pinned HTTP reads for Alden link evidence.

The caller owns its turn/URL budget and passes its absolute monotonic deadline.
No persistent state, worker imports, redirects through urllib, or ambient proxies.
"""

from __future__ import annotations

import http.client
import ipaddress
import math
import re
import socket
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable


MAX_BODY_BYTES = 1_000_000
MAX_DNS_ADDRESSES = 32
MAX_DNS_SECONDS = 8.0
MAX_REDIRECT_HOPS = 8
_READ_CHUNK_BYTES = 64 * 1024
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class LinkBodyBudgetExceeded(ValueError):
    """Bounded reads exceeded their byte budget, before any parsing."""


def _remaining(deadline: float) -> float:
    try:
        if isinstance(deadline, bool) or not math.isfinite(float(deadline)):
            raise ValueError
        seconds = float(deadline) - time.monotonic()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("link deadline invalid") from None
    if seconds <= 0:
        raise TimeoutError("link URL timeout")
    return seconds


def _has_control(value: str) -> bool:
    return any(ord(char) <= 0x20 or 0x7f <= ord(char) <= 0x9f for char in value)


def _public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_reserved
        and not ip.is_multicast
        and not ip.is_unspecified
        and "%" not in value
    )


def _hostname(host: str | None) -> str | None:
    if not host or _has_control(host) or "\\" in host or "%" in host:
        return None
    try:
        normalized = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return None
    if not normalized or len(normalized) > 253:
        return None
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        if (
            normalized in {"localhost", "localhost.localdomain", "localdomain"}
            or normalized.endswith((".localhost", ".local"))
            or not all(_HOST_LABEL.fullmatch(part) for part in normalized.split("."))
        ):
            return None
    else:
        if not _public_ip(normalized):
            return None
    return normalized


def _target(url: str) -> tuple[urllib.parse.SplitResult, str, int] | None:
    # urlsplit strips C0 and CR/LF/TAB; reject those *before* it can do so.
    if (not isinstance(url, str) or len(url.encode('utf-8', 'surrogatepass')) > 2048
            or _has_control(url) or "\\" in url):
        return None
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or "@" in parsed.netloc:
            return None
        hostname = _hostname(parsed.hostname)
        explicit_port = parsed.port
    except ValueError:
        return None
    if hostname is None or (explicit_port is not None and explicit_port not in (80, 443)):
        return None
    return parsed, hostname, explicit_port or (443 if parsed.scheme == "https" else 80)


def _checked_addresses(addresses: list[tuple], port: int) -> list[tuple]:
    if not isinstance(addresses, (list, tuple)) or not 1 <= len(addresses) <= MAX_DNS_ADDRESSES:
        raise OSError("link DNS address count invalid")
    result = []
    for entry in addresses:
        if not isinstance(entry, tuple) or len(entry) != 5:
            raise OSError("link DNS address malformed")
        family, socktype, protocol, _canonname, sockaddr = entry
        if (
            family not in (socket.AF_INET, socket.AF_INET6)
            or socktype != socket.SOCK_STREAM
            or protocol not in (0, socket.IPPROTO_TCP)
            or not isinstance(sockaddr, tuple)
            or len(sockaddr) < 2
            or not isinstance(sockaddr[0], str)
            or isinstance(sockaddr[1], bool)
            or sockaddr[1] != port
        ):
            raise OSError("link DNS address malformed")
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            raise OSError("link DNS address malformed") from None
        if ip.version != (4 if family == socket.AF_INET else 6):
            raise OSError("link DNS address malformed")
        if not _public_ip(sockaddr[0]):
            raise OSError("link DNS address not public")
        if family == socket.AF_INET6 and (len(sockaddr) != 4 or sockaddr[3] != 0):
            raise OSError("link DNS address malformed")
        result.append(entry)
    return result


def resolve_addresses(hostname: str, port: int, timeout: float) -> list[tuple]:
    """Resolve at most 32 public address candidates within a bounded wait.

    A timed-out getaddrinfo may complete later in its daemon thread; it is never
    used after the timeout. Callers additionally validate injected resolvers.
    """
    if _hostname(hostname) is None or port not in (80, 443):
        raise ValueError("link DNS input invalid")
    try:
        seconds = min(float(timeout), MAX_DNS_SECONDS)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("link DNS timeout invalid") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise TimeoutError("link DNS timeout")
    result: list[list[tuple]] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM))
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True, name="alden-link-dns")
    thread.start()
    thread.join(seconds)
    if thread.is_alive():
        raise TimeoutError("link DNS timeout")
    if errors:
        raise OSError("link DNS resolution failed")
    return _checked_addresses(result[0] if result else [], port)


class _PinnedSocketMixin:
    def __init__(
        self, address: str, port: int, *, hostname: str, family: int,
        protocol: int, sockaddr: tuple, deadline: float, **kwargs: object,
    ) -> None:
        self._pinned_hostname = hostname
        self._pinned_family = family
        self._pinned_protocol = protocol
        self._pinned_sockaddr = sockaddr
        self._pinned_deadline = deadline
        super().__init__(address, port, timeout=_remaining(deadline), **kwargs)

    def _pinned_socket(self) -> socket.socket:
        sock = socket.socket(self._pinned_family, socket.SOCK_STREAM, self._pinned_protocol)
        try:
            sock.settimeout(_remaining(self._pinned_deadline))
            sock.connect(self._pinned_sockaddr)
        except BaseException:
            sock.close()
            raise
        return sock


class _PinnedHTTPConnection(_PinnedSocketMixin, http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = self._pinned_socket()


class _PinnedHTTPSConnection(_PinnedSocketMixin, http.client.HTTPSConnection):
    def connect(self) -> None:
        sock = self._pinned_socket()
        try:
            # Default context checks the certificate against the original host.
            self.sock = self._context.wrap_socket(sock, server_hostname=self._pinned_hostname)
        except BaseException:
            sock.close()
            raise


def open_pinned(
    parsed: urllib.parse.SplitResult | urllib.parse.ParseResult,
    hostname: str,
    port: int,
    addresses: list[tuple],
    deadline: float,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    """Connect by a validated numeric sockaddr, retaining Host and TLS SNI."""
    if parsed.scheme not in {"http", "https"} or _hostname(hostname) != hostname:
        raise ValueError("link request target invalid")
    target = urllib.parse.quote(parsed.path or "/", safe="/:@-._~!$&'()*+,;=%")
    if not target.startswith("/"):
        target = "/" + target
    if parsed.query:
        target += "?" + urllib.parse.quote(parsed.query, safe="/?:@-._~!$&'()*+,;=%")
    if _has_control(target) or "\\" in target:
        raise ValueError("link request target invalid")
    candidates = _checked_addresses(addresses, port)
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if port != (443 if parsed.scheme == "https" else 80):
        host_header += f":{port}"
    last_error: Exception | None = None
    for family, _type, protocol, _canonname, sockaddr in candidates:
        connection: http.client.HTTPConnection | None = None
        try:
            remaining = _remaining(deadline)
            args = dict(hostname=hostname, family=family, protocol=protocol,
                        sockaddr=sockaddr, deadline=deadline)
            if parsed.scheme == "https":
                connection = _PinnedHTTPSConnection(
                    str(sockaddr[0]), port, context=ssl.create_default_context(), **args
                )
            else:
                connection = _PinnedHTTPConnection(str(sockaddr[0]), port, **args)
            connection.request(
                "GET", target,
                headers={"Host": host_header, "User-Agent": "openkakao-auto-reply/1.0",
                         "Connection": "close", "Accept-Encoding": "identity"},
            )
            if connection.sock is not None:
                connection.sock.settimeout(_remaining(deadline))
            response = connection.getresponse()
            _remaining(deadline)
            return connection, response
        except (TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
            last_error = exc
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
            if _remaining(deadline) <= 0:  # raises if expired
                break
    if isinstance(last_error, TimeoutError):
        raise TimeoutError("link connection timeout")
    if isinstance(last_error, (ValueError, http.client.HTTPException)):
        raise ValueError("link HTTP request failed")
    raise OSError("link connection unavailable")


def read_body(
    response: object, deadline: float, *, cancel: Callable[[], None] = lambda: None,
    max_bytes: int = MAX_BODY_BYTES,
) -> bytes:
    """Read at most max_bytes, including one byte to detect overflow."""
    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_BODY_BYTES:
        raise ValueError("link body budget invalid")
    chunks: list[bytes] = []
    total = 0
    while True:
        cancel()
        remaining = _remaining(deadline)
        # HTTPResponse uses a socket.makefile() wrapper; refresh its socket
        # timeout before each blocking read as the absolute deadline shrinks.
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        try:
            if sock is not None:
                sock.settimeout(remaining)
            chunk = response.read(min(_READ_CHUNK_BYTES, max_bytes - total + 1))
        except TimeoutError:
            raise TimeoutError("link read timeout") from None
        except OSError:
            raise OSError("link read unavailable") from None
        except ValueError:
            raise ValueError("link body read failed") from None
        cancel()
        _remaining(deadline)
        if not isinstance(chunk, (bytes, bytearray)):
            raise ValueError("link response body is not bytes")
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise LinkBodyBudgetExceeded("link body exceeds bounded retrieval size")
        chunks.append(bytes(chunk))
    cancel()
    return b"".join(chunks)


def _header(response: object, name: str) -> str | None:
    getter = getattr(response, "getheader", None)
    if not callable(getter):
        raise ValueError("link response headers unavailable")
    try:
        result = getter(name)
    except (OSError, ValueError, http.client.HTTPException):
        raise ValueError("link response headers invalid") from None
    if result is not None and not isinstance(result, str):
        raise ValueError("link response headers invalid")
    return result


def _kakao_daum(hostname: str) -> bool:
    return any(hostname == domain or hostname.endswith("." + domain)
               for domain in ("kakao.com", "daum.net"))


def fetch_bytes(
    url: str,
    deadline: float,
    *,
    redirects_left: int = 1,
    cancel: Callable[[], None] = lambda: None,
    resolver: Callable = resolve_addresses,
    opener: Callable = open_pinned,
    reader: Callable | None = None,
    allow_redirect: Callable[[str, str], bool] | None = None,
    trace: list | None = None,
) -> tuple[bytes, int] | None:
    """Fetch a validated public URL, closing each connection on every path.

    Return None for URL/host/DNS-public policy refusals. Transport and protocol
    failures use fixed error messages; injected reader receives two arguments.
    """
    if type(redirects_left) is not int or not 0 <= redirects_left <= MAX_REDIRECT_HOPS:
        raise ValueError("link redirect budget invalid")
    if trace is not None and (not isinstance(trace, list) or trace):
        raise ValueError("link trace invalid")
    current_url = url
    for hops_remaining in range(redirects_left, -1, -1):
        cancel()
        target = _target(current_url)
        if target is None:
            return None
        parsed, hostname, port = target
        remaining = _remaining(deadline)
        try:
            addresses = resolver(hostname, port, min(MAX_DNS_SECONDS, remaining))
        except TimeoutError:
            raise TimeoutError("link DNS timeout") from None
        except OSError:
            raise OSError("link DNS resolution failed") from None
        except ValueError:
            raise ValueError("link DNS lookup invalid") from None
        cancel()
        try:
            addresses = _checked_addresses(addresses, port)
        except OSError as exc:
            if str(exc) == "link DNS address not public":
                return None
            raise
        _remaining(deadline)
        cancel()
        try:
            connection, response = opener(parsed, hostname, port, addresses, deadline)
        except TimeoutError:
            raise TimeoutError("link connection timeout") from None
        except OSError:
            raise OSError("link connection unavailable") from None
        except (ValueError, http.client.HTTPException):
            raise ValueError("link HTTP request failed") from None
        redirect_to: str | None = None
        try:
            cancel()
            _remaining(deadline)
            status = getattr(response, "status", None)
            if type(status) is not int:
                raise ValueError("link response status invalid")
            if trace is not None:
                trace.append({'url': current_url, 'status': status})
            if 300 <= status < 400:
                if hops_remaining == 0:
                    raise ValueError("link redirect not allowed")
                location = _header(response, "Location")
                if not location or _has_control(location) or "\\" in location:
                    raise ValueError("link redirect invalid")
                try:
                    redirect_to = urllib.parse.urljoin(current_url, location)
                except ValueError:
                    raise ValueError("link redirect invalid") from None
                next_target = _target(redirect_to)
                if next_target is None:
                    return None
                if allow_redirect is None:
                    permitted = (_kakao_daum(hostname) and
                                 next_target[0].scheme == "https" and
                                 _kakao_daum(next_target[1]))
                else:
                    try:
                        permitted = allow_redirect(current_url, redirect_to)
                    except (ValueError, OSError, TimeoutError):
                        raise ValueError("link redirect policy failed") from None
                if not permitted:
                    raise ValueError("link redirect not allowed")
            elif not 200 <= status < 300:
                raise ValueError("link response not successful")
            else:
                encoding = _header(response, "Content-Encoding")
                if encoding is not None and encoding.strip().lower() != "identity":
                    raise ValueError("link content encoding not supported")
                try:
                    body = reader(response, deadline) if reader is not None else read_body(
                        response, deadline, cancel=cancel
                    )
                except TimeoutError:
                    raise TimeoutError("link read timeout") from None
                except OSError:
                    raise OSError("link read unavailable") from None
                except LinkBodyBudgetExceeded:
                    raise
                except ValueError:
                    raise ValueError("link body read failed") from None
                cancel()
                _remaining(deadline)
                if not isinstance(body, (bytes, bytearray)):
                    raise ValueError("link response body is not bytes")
                if len(body) > MAX_BODY_BYTES:
                    raise LinkBodyBudgetExceeded("link body exceeds bounded retrieval size")
                return bytes(body), len(body)
        finally:
            try:
                connection.close()
            except Exception:
                pass
        cancel()
        _remaining(deadline)
        if redirect_to is not None:
            current_url = redirect_to
    raise ValueError("link redirect not allowed")


_LINK_FAILURES = {
    "link DNS timeout": "link_dns_timeout",
    "link DNS resolution failed": "link_dns_unavailable",
    "link DNS lookup invalid": "link_dns_unavailable",
    "link DNS input invalid": "link_dns_unavailable",
    "link DNS timeout invalid": "link_dns_timeout",
    "link DNS address count invalid": "link_dns_unavailable",
    "link DNS address malformed": "link_dns_unavailable",
    "link DNS address not public": "link_not_public",
    "link connection timeout": "link_connection_timeout",
    "link connection unavailable": "link_connection_unavailable",
    "link HTTP request failed": "link_http_unavailable",
    "link response status invalid": "link_http_unavailable",
    "link response not successful": "link_http_unavailable",
    "link response headers unavailable": "link_http_unavailable",
    "link response headers invalid": "link_http_unavailable",
    "link redirect not allowed": "link_redirect_unavailable",
    "link redirect invalid": "link_redirect_unavailable",
    "link redirect policy failed": "link_redirect_unavailable",
    "link redirect budget invalid": "link_redirect_unavailable",
    "link content encoding not supported": "link_encoding_unavailable",
    "link read timeout": "link_read_timeout",
    "link read unavailable": "link_read_unavailable",
    "link body read failed": "link_read_unavailable",
    "link response body is not bytes": "link_read_unavailable",
    "link body exceeds bounded retrieval size": "link_response_budget",
    "link body budget invalid": "link_response_budget",
    "link URL timeout": "link_timeout",
    "link deadline invalid": "link_timeout",
}


def link_failure_reason(error: BaseException) -> str:
    """Map a fixed fetch failure to a stable reason. Unknown errors stay generic."""
    if not isinstance(error, (TimeoutError, ValueError, OSError)):
        return "link_unavailable"
    return _LINK_FAILURES.get(str(error), "link_timeout" if isinstance(error, TimeoutError) else "link_unavailable")


LINK_FAILURE_REASONS = frozenset(_LINK_FAILURES.values()) | {
    "link_unavailable",
    "link_source_budget", "link_source_identity_invalid", "link_format_unavailable",
    "link_binary_content", "link_encoding_unavailable",
}
