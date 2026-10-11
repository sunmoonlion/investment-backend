"""Resolve a client address without trusting caller-supplied forwarding headers."""

import ipaddress
from collections.abc import Sequence


def source_ip(
    peer: str | None,
    forwarded_for: str | Sequence[str] | None,
    trusted_cidrs: str,
) -> str:
    try:
        direct = ipaddress.ip_address(peer or "")
    except ValueError:
        return "unknown"
    networks = []
    for item in trusted_cidrs.split(","):
        value = item.strip()
        if value:
            try:
                networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                # Invalid deployment configuration must never cause an untrusted
                # header to become authoritative.
                return str(direct)
    if not any(direct in network for network in networks):
        return str(direct)
    if not forwarded_for:
        return "unknown"
    headers = [forwarded_for] if isinstance(forwarded_for, str) else forwarded_for
    chain: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for header in headers:
        for item in header.split(","):
            try:
                chain.append(ipaddress.ip_address(item.strip()))
            except ValueError:
                return "unknown"
    if not chain:
        return "unknown"
    # Walk from the backend outward, discarding only hops explicitly trusted
    # by the operator. The first untrusted address is the client. If every hop
    # is trusted, retain the leftmost address as the best available source.
    for candidate in reversed(chain):
        if not any(candidate in network for network in networks):
            return str(candidate)
    return str(chain[0])
