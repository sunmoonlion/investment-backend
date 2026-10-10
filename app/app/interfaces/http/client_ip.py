"""Resolve a client address without trusting caller-supplied forwarding headers."""

import ipaddress


def source_ip(peer: str | None, forwarded_for: str | None, trusted_cidrs: str) -> str:
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
    # The final address is the one appended by the directly connected trusted
    # ingress. Earlier values may have been supplied by the caller.
    candidate = forwarded_for.split(",")[-1].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return "unknown"
