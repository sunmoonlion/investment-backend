"""Public fixture configuration only; no hosted installer or production credentials."""

from core.config import AgentDownload


def preview_download(scenario):
    if scenario == "empty":
        return None
    return AgentDownload(
        url="https://downloads.example/agent/0.2.1/windows-x64.zip",
        version="0.2.1",
        codex_version="0.155.1",
        size_bytes=174243923,
        zip_sha256="a" * 64,
        manifest_sha256="b" * 64,
    )
