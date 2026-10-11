"""S3 streaming adapter. No default credential chain, public URLs or redirects."""

import hashlib
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.application.workbench.agent_download import ReleaseIdentity, ReleaseUnavailable

BUCKET = "agent-releases"
CHUNK = 64 * 1024


class Transfer:
    def __init__(self, body, client, length, checksum=None):
        self.body, self.client = body, client
        self.length, self.checksum = length, checksum
        self.closed = False

    def chunks(self):
        digest, count = hashlib.sha256(), 0
        pending = b""
        try:
            while chunk := self.body.read(CHUNK):
                count += len(chunk)
                if count > self.length:
                    raise ReleaseUnavailable("release length changed")
                digest.update(chunk)
                if pending:
                    yield pending
                pending = chunk
            if count != self.length or (
                self.checksum and digest.hexdigest() != self.checksum
            ):
                raise ReleaseUnavailable("release integrity mismatch")
            if pending:
                yield pending
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.closed = True
            self.body.close()
            self.client.close()


class S3ReleaseStore:
    def __init__(self, *, endpoint, region, ca_file, access_key, secret_key):
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            verify=ca_file,
            config=Config(
                s3={"addressing_style": "path"},
                proxies={},
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 1},
            ),
        )
        # SDK region redirects must not send a signed request to a different host/path.
        self.endpoint = urlsplit(endpoint)
        self.client.meta.events.register("before-send.s3", self._same_endpoint)
        self.client.meta.events.register_first("needs-retry.s3", self._no_redirect)

    def _no_redirect(self, response=None, **kwargs):
        if response is not None and response[0].status_code in (
            301,
            302,
            303,
            307,
            308,
        ):
            raise ReleaseUnavailable("storage redirect refused")

    def _same_endpoint(self, request, **kwargs):
        actual = urlsplit(request.url)
        if (actual.scheme, actual.netloc) != (
            self.endpoint.scheme,
            self.endpoint.netloc,
        ):
            raise ReleaseUnavailable("storage redirect refused")

    def check(self, release: ReleaseIdentity) -> str:
        try:
            head = self.client.head_object(Bucket=BUCKET, Key=release.key)
            if (
                head["ContentLength"] != release.size
                or head.get("Metadata", {}).get("sha256") != release.sha256
                or head.get("Metadata", {}).get("manifest-sha256")
                != release.manifest_sha256
                or not head.get("ETag")
            ):
                raise ReleaseUnavailable("release metadata mismatch")
            return head["ETag"]
        except (BotoCoreError, ClientError) as exc:
            raise ReleaseUnavailable("release storage unavailable") from exc

    def open(self, release, etag, span):
        args = dict(Bucket=BUCKET, Key=release.key, IfMatch=etag)
        if span:
            args["Range"] = f"bytes={span[0]}-{span[1]}"
        try:
            response = self.client.get_object(**args)
        except (BotoCoreError, ClientError) as exc:
            raise ReleaseUnavailable("release storage unavailable") from exc
        length = span[1] - span[0] + 1 if span else release.size
        expected_range = f"bytes {span[0]}-{span[1]}/{release.size}" if span else None
        if (
            response.get("ContentLength") != length
            or response.get("ETag") != etag
            or response.get("ContentRange") != expected_range
        ):
            response["Body"].close()
            raise ReleaseUnavailable("release changed during transfer")
        return Transfer(
            response["Body"], self.client, length, None if span else release.sha256
        )

    def close(self):
        self.client.close()
