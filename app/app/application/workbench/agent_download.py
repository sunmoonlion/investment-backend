"""Release transfer contract. Only an operator-selected immutable object is readable."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


class ReleaseUnavailable(Exception):
    pass


class InvalidRange(Exception):
    pass


def byte_range(value: str | None, size: int) -> tuple[int, int] | None:
    """Single byte range only. Reject malformed/multiple/unsatisfiable ranges."""
    if value is None:
        return None
    import re

    match = re.fullmatch(r"bytes=([0-9]{0,20})-([0-9]{0,20})", value)
    if not match or not any(match.groups()):
        raise InvalidRange
    start, end = match.groups()
    if not start:
        count = int(end)
        if count == 0:
            raise InvalidRange
        return max(0, size - count), size - 1
    first, last = int(start), min(int(end), size - 1) if end else size - 1
    if first >= size or first > last:
        raise InvalidRange
    return first, last


@dataclass(frozen=True)
class ReleaseIdentity:
    key: str
    size: int
    sha256: str
    manifest_sha256: str


class ReleaseTransfer(Protocol):
    def chunks(self) -> Iterator[bytes]: ...
    def close(self) -> None: ...


class ReleaseStore(Protocol):
    def check(self, release: ReleaseIdentity) -> str: ...
    def open(
        self, release: ReleaseIdentity, etag: str, span: tuple[int, int] | None
    ) -> ReleaseTransfer: ...
    def close(self) -> None: ...
