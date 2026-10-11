import hashlib
import io
from unittest.mock import Mock

import httpx
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber
from pydantic import ValidationError
from test_agent_onboarding import RELEASE
from test_workbench_routes import A, principal

from app.application.workbench.agent_download import (
    InvalidRange,
    ReleaseIdentity,
    ReleaseUnavailable,
    byte_range,
)
from app.bootstrap.api import create_app
from app.infrastructure.workbench.agent_release import S3ReleaseStore, Transfer
from app.interfaces.endpoints import workbench_routes as routes
from app.interfaces.http.middleware.auth import get_web_current_user
from core.config import Settings

PAYLOAD = b"installer-example"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()
FORWARD = RELEASE | dict(
    mode="object-storage",
    object_key="windows-x64/0.2.1/agent.zip",
    url="https://investment.example/api/workbench/agent/package",
    zip_sha256=DIGEST,
    size_bytes=len(PAYLOAD),
)
STORAGE = dict(
    endpoint="https://s3.example",
    region="us-east-1",
    ca_file="/ca.pem",
    access_key="fixture-reader",
    secret_key="fixture-secret",
)


def configured():
    return Settings(
        web_frontend_base_url="https://investment.example",
        WORKBENCH_AGENT_DOWNLOAD=FORWARD,
        WORKBENCH_AGENT_RELEASE_STORAGE=STORAGE,
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("bytes=0-0", (0, 0)),
        ("bytes=2-", (2, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=0-999", (0, 9)),
        ("bytes=-999", (0, 9)),
    ],
)
def test_ranges(value, expected):
    assert byte_range(value, 10) == expected


@pytest.mark.parametrize(
    "value",
    [
        "bytes=",
        "bytes=-",
        "bytes=-0",
        "bytes=10-",
        "bytes=5-2",
        "bytes=0-1,3-4",
        "items=0-1",
        "bytes=+1-",
        "bytes=" + "9" * 30 + "-",
    ],
)
def test_invalid_ranges(value):
    with pytest.raises(InvalidRange):
        byte_range(value, 10)


@pytest.mark.parametrize(
    "patch",
    [
        dict(object_key="../secret"),
        dict(object_key=None),
        dict(url="https://elsewhere/api/workbench/agent/package"),
    ],
)
def test_storage_config_fail_closed(patch):
    with pytest.raises(ValidationError):
        Settings(
            web_frontend_base_url="https://investment.example",
            WORKBENCH_AGENT_DOWNLOAD=FORWARD | patch,
            WORKBENCH_AGENT_RELEASE_STORAGE=STORAGE,
        )


@pytest.mark.parametrize(
    "patch",
    [
        dict(endpoint="http://s3.example"),
        dict(endpoint="https://s3.example/path"),
        dict(access_key=""),
        dict(secret_key=""),
        dict(ca_file=""),
    ],
)
def test_credentials_and_tls_required(patch):
    with pytest.raises(ValidationError):
        Settings(
            web_frontend_base_url="https://investment.example",
            WORKBENCH_AGENT_DOWNLOAD=FORWARD,
            WORKBENCH_AGENT_RELEASE_STORAGE=STORAGE | patch,
        )


@pytest.mark.parametrize(
    "method,headers,status,expected",
    [
        ("GET", {}, 200, PAYLOAD),
        ("HEAD", {}, 200, b""),
        ("GET", {"Range": "bytes=2-5"}, 206, PAYLOAD[2:6]),
        ("GET", {"Range": "bytes=-3", "If-Range": f'"{DIGEST}"'}, 206, PAYLOAD[-3:]),
        ("GET", {"Range": "bytes=2-5", "If-Range": '"old"'}, 200, PAYLOAD),
        ("GET", {"Range": "bytes=999-"}, 416, None),
    ],
)
async def test_authenticated_stream(monkeypatch, method, headers, status, expected):
    settings = configured()
    store = Mock()
    store.check.return_value = '"object-etag"'

    def opened(release, etag, span):
        assert etag == '"object-etag"'
        content = PAYLOAD[span[0] : span[1] + 1] if span else PAYLOAD
        return Transfer(
            io.BytesIO(content), store, len(content), None if span else DIGEST
        )

    store.open.side_effect = opened
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    factory = Mock(return_value=store)
    monkeypatch.setattr(routes, "agent_release_store", factory)
    app = create_app(settings)
    app.dependency_overrides[routes.require_workbench_enabled] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        url = "/api/workbench/agent/package"
        assert (await client.get(url)).status_code in (401, 403)
        factory.assert_not_called()
        app.dependency_overrides[get_web_current_user] = lambda: principal(A)
        assert (await client.get(url + "?key=secret")).status_code == 400
        factory.assert_not_called()
        descriptor = (await client.get("/api/workbench/agent/download")).json()[
            "download"
        ]
        assert descriptor == {
            k: v for k, v in FORWARD.items() if k not in ("mode", "object_key")
        }
        result = await client.request(method, url, headers=headers)
        assert result.status_code == status
        assert result.headers["cache-control"] == "no-store"
        assert result.headers["x-checksum-sha256"] == DIGEST
        if expected is not None:
            assert result.content == expected
            assert int(result.headers["content-length"]) == (
                len(PAYLOAD) if method == "HEAD" else len(expected)
            )
            store.close.assert_called()
        if status == 416:
            assert result.headers["content-range"] == f"bytes */{len(PAYLOAD)}"
            factory.assert_not_called()


def test_real_sdk_metadata_and_if_match():
    store = S3ReleaseStore(**STORAGE)
    identity = ReleaseIdentity(
        FORWARD["object_key"], len(PAYLOAD), DIGEST, RELEASE["manifest_sha256"]
    )
    metadata = {"sha256": DIGEST, "manifest-sha256": identity.manifest_sha256}
    with Stubber(store.client) as stub:
        stub.add_response(
            "head_object",
            {"ContentLength": len(PAYLOAD), "ETag": '"etag"', "Metadata": metadata},
            {"Bucket": "agent-releases", "Key": identity.key},
        )
        stub.add_response(
            "get_object",
            {
                "ContentLength": 3,
                "ContentRange": f"bytes 2-4/{len(PAYLOAD)}",
                "ETag": '"etag"',
                "Body": StreamingBody(io.BytesIO(PAYLOAD[2:5]), 3),
            },
            {
                "Bucket": "agent-releases",
                "Key": identity.key,
                "IfMatch": '"etag"',
                "Range": "bytes=2-4",
            },
        )
        etag = store.check(identity)
        assert b"".join(store.open(identity, etag, (2, 4)).chunks()) == PAYLOAD[2:5]


def test_sdk_failures_and_redirect():
    store = S3ReleaseStore(**STORAGE)
    identity = ReleaseIdentity("windows-x64/0.2.1/agent.zip", 10, DIGEST, "b" * 64)
    with Stubber(store.client) as stub:
        stub.add_response("head_object", {"ContentLength": 1, "ETag": '"etag"'})
        with pytest.raises(ReleaseUnavailable):
            store.check(identity)
        stub.add_client_error("get_object", "PreconditionFailed", http_status_code=412)
        with pytest.raises(ReleaseUnavailable):
            store.open(identity, '"old"', None)
    with pytest.raises(ReleaseUnavailable):
        store._same_endpoint(Mock(url="https://other.example/object"))
    for status in (301, 302, 303, 307, 308):
        with pytest.raises(ReleaseUnavailable):
            store._no_redirect((Mock(status_code=status), {}))
    store.close()


@pytest.mark.parametrize("release", [None, RELEASE])
async def test_package_unavailable_outside_forward_mode(monkeypatch, release):
    settings = Settings(WORKBENCH_AGENT_DOWNLOAD=release)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    factory = Mock()
    monkeypatch.setattr(routes, "agent_release_store", factory)
    app = create_app(settings)
    app.dependency_overrides[routes.require_workbench_enabled] = lambda: None
    app.dependency_overrides[get_web_current_user] = lambda: principal(A)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        result = await client.get("/api/workbench/agent/package")
    assert result.status_code == 404
    assert result.headers["cache-control"] == "no-store"
    factory.assert_not_called()


async def test_store_error_is_closed_and_redacted(monkeypatch):
    settings = configured()
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    store = Mock()
    store.check.side_effect = ReleaseUnavailable("sensitive upstream body")
    monkeypatch.setattr(routes, "agent_release_store", lambda _: store)
    app = create_app(settings)
    app.dependency_overrides[routes.require_workbench_enabled] = lambda: None
    app.dependency_overrides[get_web_current_user] = lambda: principal(A)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        result = await client.get("/api/workbench/agent/package")
    assert result.status_code == 503
    assert "sensitive" not in result.text
    store.close.assert_called_once()


def test_transfer_bounded_and_closes_on_failure():
    for data, size, sha in [
        (b"short", 10, None),
        (b"too-long", 1, None),
        (b"bad", 3, DIGEST),
    ]:
        body, client = io.BytesIO(data), Mock()
        transfer = Transfer(body, client, size, sha)
        with pytest.raises(ReleaseUnavailable):
            list(transfer.chunks())
        assert body.closed
        client.close.assert_called_once()
    body, client = io.BytesIO(b"a" * 200000), Mock()
    transfer = Transfer(body, client, 200000)
    chunks = list(transfer.chunks())
    assert max(map(len, chunks)) <= 65536
    assert body.closed


async def test_response_closes_even_when_never_iterated():
    from starlette.requests import ClientDisconnect

    transfer = Mock()
    response = routes._PackageResponse(iter([b"x"]), transfer=transfer)

    async def disconnected(message):
        raise OSError("closed")

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, Mock(), disconnected
        )
    transfer.close.assert_called_once()
