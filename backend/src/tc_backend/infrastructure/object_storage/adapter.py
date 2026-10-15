"""Small AWS Signature V4 client shared by Cloudflare R2 and local MinIO.

Only the operations required by private attachments are implemented. Credentials
never leave the API process; browsers receive a short-lived, method-bound PUT URL.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx

from tc_backend.infrastructure.object_storage.errors import (
    ObjectNotFound,
    ObjectStorageTransportError,
)


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    key: str
    content_type: str
    size_bytes: int
    sha256: str | None


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size_bytes: int
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class StoredObjectPage:
    items: tuple[StoredObject, ...]
    next_cursor: str | None


class S3CompatibleObjectStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        browser_endpoint_url: str | None,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        timeout_seconds: float = 30.0,
        request_observer: Callable[[str], None] | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url.rstrip("/")
        self.browser_endpoint_url = (browser_endpoint_url or endpoint_url).rstrip("/")
        self.bucket = bucket.strip()
        self.region = region.strip()
        self.access_key_id = access_key_id.strip()
        self._secret_access_key = secret_access_key
        self.timeout_seconds = timeout_seconds
        self._request_observer = request_observer
        if not all((self.endpoint_url, self.bucket, self.region, self.access_key_id, secret_access_key)):
            raise ValueError("object storage configuration values cannot be blank")

    def presign_put(
        self,
        *,
        key: str,
        content_type: str,
        sha256: str,
        expires_seconds: int,
        now: datetime | None = None,
    ) -> tuple[str, dict[str, str], datetime]:
        issued_at = (now or datetime.now(UTC)).astimezone(UTC)
        timestamp = issued_at.strftime("%Y%m%dT%H%M%SZ")
        datestamp = issued_at.strftime("%Y%m%d")
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        url = self._object_url(self.browser_endpoint_url, key)
        parts = urlsplit(url)
        signed_headers = "content-type;host;x-amz-meta-sha256"
        query = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key_id}/{scope}",
            "X-Amz-Date": timestamp,
            "X-Amz-Expires": str(expires_seconds),
            "X-Amz-SignedHeaders": signed_headers,
        }
        canonical_query = _canonical_query(query)
        canonical_headers = (
            f"content-type:{content_type.strip().lower()}\n"
            f"host:{parts.netloc.lower()}\n"
            f"x-amz-meta-sha256:{sha256}\n"
        )
        canonical_request = "\n".join(
            (
                "PUT",
                parts.path,
                canonical_query,
                canonical_headers,
                signed_headers,
                "UNSIGNED-PAYLOAD",
            )
        )
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                timestamp,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signature = hmac.new(
            self._signing_key(datestamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        query["X-Amz-Signature"] = signature
        signed_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        expires_at = issued_at.replace(microsecond=0) + _seconds(expires_seconds)
        return signed_url, {"content-type": content_type, "x-amz-meta-sha256": sha256}, expires_at

    def head(self, *, key: str) -> ObjectMetadata:
        response = self._request("HEAD", key)
        if response.status_code == 404:
            raise ObjectNotFound(f"object was not found: {key}")
        self._raise_for_status(response)
        sha256 = response.headers.get("x-amz-meta-sha256")
        return ObjectMetadata(
            key=key,
            content_type=response.headers.get("content-type", "application/octet-stream").split(";", 1)[0],
            size_bytes=int(response.headers.get("content-length", "0")),
            sha256=sha256.lower() if sha256 else None,
        )

    def list_objects(
        self,
        *,
        prefix: str,
        cursor: str | None = None,
        limit: int = 1000,
    ) -> StoredObjectPage:
        if not 1 <= limit <= 1000:
            raise ValueError("object list limit must be between 1 and 1000")
        query = {
            "list-type": "2",
            "max-keys": str(limit),
            "prefix": prefix,
        }
        if cursor:
            query["continuation-token"] = cursor
        response = self._request_url(
            "GET",
            self._bucket_url(self.endpoint_url),
            query=query,
            request_kind="CLASS_A_LIST",
        )
        self._raise_for_status(response)
        try:
            root = ElementTree.fromstring(response.content)
            items = tuple(
                StoredObject(
                    key=_required_xml_text(item, "{*}Key"),
                    size_bytes=int(_required_xml_text(item, "{*}Size")),
                    last_modified=datetime.fromisoformat(
                        _required_xml_text(item, "{*}LastModified").replace("Z", "+00:00")
                    ).astimezone(UTC),
                )
                for item in root.findall("{*}Contents")
            )
            token = root.findtext("{*}NextContinuationToken")
            truncated = root.findtext("{*}IsTruncated") == "true"
        except (ElementTree.ParseError, TypeError, ValueError) as error:
            raise ObjectStorageTransportError("object list response was invalid") from error
        return StoredObjectPage(items=items, next_cursor=token if truncated else None)

    def list_buckets(self) -> tuple[str, ...]:
        """Return the authenticated bucket inventory in a stable order."""

        response = self._request_url(
            "GET",
            self.endpoint_url,
            request_kind="CLASS_A_LIST",
        )
        self._raise_for_status(response)
        try:
            root = ElementTree.fromstring(response.content)
            names = tuple(
                sorted(
                    _required_xml_text(bucket, "{*}Name")
                    for bucket in root.findall("{*}Buckets/{*}Bucket")
                )
            )
        except (ElementTree.ParseError, TypeError, ValueError) as error:
            raise ObjectStorageTransportError(
                "bucket list response was invalid"
            ) from error
        if len(names) != len(set(names)):
            raise ObjectStorageTransportError("bucket list contained duplicate names")
        return names

    def bucket_exists(self) -> bool:
        response = self._request_url(
            "HEAD",
            self._bucket_url(self.endpoint_url),
            request_kind="CLASS_B_HEAD",
        )
        if response.status_code == 404:
            return False
        self._raise_for_status(response)
        return True

    def create_bucket(self) -> None:
        response = self._request_url(
            "PUT",
            self._bucket_url(self.endpoint_url),
            request_kind="CLASS_A_PUT",
        )
        self._raise_for_status(response)

    def delete_bucket(self) -> None:
        response = self._request_url(
            "DELETE",
            self._bucket_url(self.endpoint_url),
            request_kind="FREE_DELETE",
        )
        if response.status_code == 404:
            raise ObjectNotFound(f"bucket was not found: {self.bucket}")
        self._raise_for_status(response)

    def delete(self, *, key: str) -> None:
        response = self._request("DELETE", key)
        if response.status_code != 404:
            self._raise_for_status(response)

    def download(self, *, key: str) -> Iterator[bytes]:
        url = self._object_url(self.endpoint_url, key)
        headers = self._authorization_headers("GET", url)
        self._observe("CLASS_B_GET")
        try:
            with httpx.stream("GET", url, headers=headers, timeout=self.timeout_seconds) as response:
                if response.status_code == 404:
                    raise ObjectNotFound(f"object was not found: {key}")
                self._raise_for_status(response)
                yield from response.iter_bytes()
        except httpx.HTTPError as error:
            raise ObjectStorageTransportError("object download failed") from error

    def _request(self, method: str, key: str) -> httpx.Response:
        url = self._object_url(self.endpoint_url, key)
        request_kind = {
            "HEAD": "CLASS_B_HEAD",
            "DELETE": "FREE_DELETE",
        }.get(method, "OTHER")
        return self._request_url(method, url, request_kind=request_kind)

    def _request_url(
        self,
        method: str,
        url: str,
        *,
        query: dict[str, str] | None = None,
        request_kind: str,
    ) -> httpx.Response:
        if query:
            parts = urlsplit(url)
            url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, _canonical_query(query), "")
            )
        headers = self._authorization_headers(method, url)
        self._observe(request_kind)
        try:
            return httpx.request(method, url, headers=headers, timeout=self.timeout_seconds)
        except httpx.HTTPError as error:
            raise ObjectStorageTransportError(f"object storage {method.lower()} failed") from error

    def _authorization_headers(self, method: str, url: str) -> dict[str, str]:
        now = datetime.now(UTC)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        parts = urlsplit(url)
        payload_hash = hashlib.sha256(b"").hexdigest()
        canonical_headers = (
            f"host:{parts.netloc.lower()}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{timestamp}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            (
                method,
                parts.path or "/",
                parts.query,
                canonical_headers,
                signed_headers,
                payload_hash,
            )
        )
        scope = f"{datestamp}/{self.region}/s3/aws4_request"
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                timestamp,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signature = hmac.new(
            self._signing_key(datestamp), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        return {
            "Authorization": (
                "AWS4-HMAC-SHA256 "
                f"Credential={self.access_key_id}/{scope},"
                f"SignedHeaders={signed_headers},Signature={signature}"
            ),
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": timestamp,
        }

    def _signing_key(self, datestamp: str) -> bytes:
        date_key = hmac.new(f"AWS4{self._secret_access_key}".encode(), datestamp.encode(), hashlib.sha256).digest()
        region_key = hmac.new(date_key, self.region.encode(), hashlib.sha256).digest()
        service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
        return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()

    def _object_url(self, endpoint: str, key: str) -> str:
        return f"{self._bucket_url(endpoint)}/{quote(key, safe='/-_.~')}"

    def _bucket_url(self, endpoint: str) -> str:
        parts = urlsplit(endpoint)
        prefix = parts.path.rstrip("/")
        path = f"{prefix}/{quote(self.bucket, safe='-_.~')}"
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    def _observe(self, request_kind: str) -> None:
        if self._request_observer is not None:
            self._request_observer(request_kind)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        raise ObjectStorageTransportError(
            f"object storage returned HTTP {response.status_code}"
        )


def _canonical_query(values: dict[str, str]) -> str:
    return "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
        for key, value in sorted(values.items())
    )


def _seconds(value: int):
    from datetime import timedelta

    return timedelta(seconds=value)


def _required_xml_text(element: ElementTree.Element, path: str) -> str:
    value = element.findtext(path)
    if value is None or not value:
        raise ValueError(f"object list item omitted {path}")
    return value
