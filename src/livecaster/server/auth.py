"""Session token for a server bound past loopback (FR-42).

On the default `127.0.0.1` bind there is no token and nothing here runs: the only
thing that can reach the port is already running as the host. Bind to the network
so a tablet can see the map and the calculus changes — `/ws` and `/api/control`
are a control plane, and anyone who reaches them can press Finish, repoint the
microphones or read the transcript. So a non-loopback client must present a
per-session token.

The host's own browser stays exempt, because it connects over loopback even when
the bind is `0.0.0.0`. That keeps `--open`, `start.sh` and every existing script
working unchanged, and gives away nothing that a process on the machine could not
already read off the disk.
"""

from __future__ import annotations

import ipaddress
import secrets

COOKIE_NAME = "lc_token"
HEADER_NAME = "X-Livecaster-Token"
QUERY_NAME = "k"

#: Health says "ok" and nothing else. Leaving it open keeps uptime checks and
#: `start.sh`-style probes honest without exposing anything.
OPEN_PATHS = frozenset({"/api/health"})


def new_token() -> str:
    """A token short enough to type off a screen, long enough not to be guessed."""
    return secrets.token_urlsafe(16)


def is_loopback(host: str | None) -> bool:
    """True for a bind address or peer address that cannot be reached off-machine."""
    if not host:
        return False
    if host in {"localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def needs_token(bind_host: str) -> bool:
    """Whether this bind exposes the control plane to anything but the host."""
    return not is_loopback(bind_host)


def token_from(*, query: str | None, header: str | None, cookie: str | None) -> str | None:
    for candidate in (query, header, cookie):
        if candidate:
            return candidate
    return None


def token_ok(expected: str, presented: str | None) -> bool:
    return bool(presented) and secrets.compare_digest(expected, presented or "")


def client_url(bind_host: str, port: int, token: str | None) -> str:
    """The URL to type into a tablet: a routable address, with the token on it."""
    host = lan_address() if bind_host in ("", "0.0.0.0", "::") else bind_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    url = f"http://{host}:{port}/"
    return f"{url}?{QUERY_NAME}={token}" if token else url


def lan_address() -> str:
    """This machine's address on the local network.

    Opening a UDP socket to a routable address picks the interface the kernel would
    actually route through, without sending anything or needing a DNS lookup.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))  # TEST-NET-1: reserved, never routed
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"
