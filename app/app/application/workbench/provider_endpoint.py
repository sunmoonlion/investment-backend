"""Validation for user-selected OpenAI-compatible model endpoints."""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlsplit


def validate_endpoint_syntax(value: str | None) -> str | None:
    if value is None:
        return None
    if value != value.strip() or any(char.isspace() for char in value):
        raise ValueError("invalid model endpoint")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid model endpoint") from exc
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or "%" in host
    ):
        raise ValueError("model endpoint must be a public HTTPS URL")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("invalid model endpoint port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("model endpoint must use a public hostname")
    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("invalid model endpoint hostname") from exc
    labels = ascii_host.split(".")
    label_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    if (
        len(ascii_host) > 253
        or "." not in ascii_host
        or ascii_host in {"localhost", "localhost.localdomain"}
        or ascii_host.endswith(
            (".localhost", ".local", ".lan", ".internal", ".home", ".home.arpa")
        )
        or any(not label_pattern.fullmatch(label) for label in labels)
    ):
        raise ValueError("model endpoint must use a public hostname")
    return value.rstrip("/")


def require_public_endpoint_dns(base_url: str) -> None:
    """Reject names that currently resolve to any loopback or non-public address."""
    host = urlsplit(base_url).hostname
    if host is None:
        raise ValueError("invalid model endpoint")
    try:
        answers = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("model endpoint hostname could not be resolved") from exc
    addresses = {answer[4][0] for answer in answers}
    if not addresses or any(
        not ipaddress.ip_address(address.split("%", 1)[0]).is_global
        for address in addresses
    ):
        raise ValueError("model endpoint hostname must resolve only to public addresses")
