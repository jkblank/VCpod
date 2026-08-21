from __future__ import annotations

import socket

# This host's IPv6 address is a non-routable ULA (fd00::/8, no real uplink),
# but music.apple.com (and most CDNs) publish real global IPv6 records too.
# gamdl's internal httpx client (no configurable timeout, not something we
# control from here) tries the IPv6 route first and hangs against the
# broken route until httpx's hardcoded ~5s ConnectTimeout fires, before ever
# falling back to IPv4 -- so every gamdl-driven Apple Music call failed
# outright with "Error fetching Apple Music homepage". curl succeeds
# instantly on the same host because it implements real Happy Eyeballs;
# httpx/anyio's async backend doesn't fall back fast enough here. Confirmed
# live 2026-08-21, see notes.md. Forcing getaddrinfo to only return AF_INET
# results sidesteps this without touching this host's system network config.
_real_getaddrinfo = socket.getaddrinfo


def force_ipv4_dns() -> None:
    def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return _real_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _ipv4_only_getaddrinfo
