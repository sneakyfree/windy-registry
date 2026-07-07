"""r2_upload.py — server-side bundle byte upload to R2.

Closes the v1 gap where SDK `publish` recorded bundle_url + sha256 but never
moved bytes (official drops were seeded to R2 by hand). Publishers now PUT
the zip to the registry after publish; the registry re-verifies the SHA-256
against the published DropVersion row, safely extracts the archive, and
uploads both the zip and its extracted files under ``{drop_id}/{version}/``
so the public CDN (drops.windydrops.com) serves render.html et al. directly.

Handing R2 credentials to clients (the older /.well-known/r2-config idea in
the SDK comment) is deliberately NOT done — bytes flow through the registry
so authz, integrity, and layout stay server-controlled.
"""

from __future__ import annotations

import io
import mimetypes
import zipfile

from ..config import Settings

MAX_BUNDLE_BYTES = 32 * 1024 * 1024  # zip cap; drops are template/skill-sized
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024  # zip-bomb guard
MAX_MEMBERS = 512


class BundleUploadError(ValueError):
    """Raised for client-correctable problems with the uploaded archive."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(message)
        self.error = error
        self.message = message


def r2_client(settings: Settings):  # pragma: no cover — network client factory
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def _safe_member_name(name: str) -> str:
    """Return the normalized member path or raise on traversal attempts."""
    if name.startswith(("/", "\\")) or "\\" in name:
        raise BundleUploadError("unsafe_zip_member", f"absolute or backslash path: {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise BundleUploadError("unsafe_zip_member", f"path traversal in member: {name!r}")
    return name


def validate_and_extract(zip_bytes: bytes) -> dict[str, bytes]:
    """Extract a bundle zip into {member_name: content}, enforcing safety caps."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise BundleUploadError("not_a_zip", "body is not a valid zip archive") from e

    members = [m for m in zf.infolist() if not m.is_dir()]
    if not members:
        raise BundleUploadError("empty_bundle", "zip contains no files")
    if len(members) > MAX_MEMBERS:
        raise BundleUploadError("too_many_members", f"zip has more than {MAX_MEMBERS} files")

    total = 0
    out: dict[str, bytes] = {}
    for m in members:
        name = _safe_member_name(m.filename)
        total += m.file_size
        if total > MAX_EXTRACTED_BYTES:
            raise BundleUploadError("bundle_too_large", "decompressed size exceeds cap")
        out[name] = zf.read(m)
    return out


def upload_bundle(
    settings: Settings, drop_id: str, version: str, zip_bytes: bytes
) -> list[str]:
    """Upload the zip + its extracted members to R2. Returns uploaded keys.

    Blocking (boto3) — call via run_in_threadpool from async routes.
    """
    files = validate_and_extract(zip_bytes)
    client = r2_client(settings)
    prefix = f"{drop_id}/{version}"
    keys: list[str] = []

    zip_key = f"{prefix}/{drop_id}-{version}.zip"
    client.put_object(
        Bucket=settings.r2_bucket,
        Key=zip_key,
        Body=zip_bytes,
        ContentType="application/zip",
    )
    keys.append(zip_key)

    for name, content in files.items():
        key = f"{prefix}/{name}"
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        client.put_object(
            Bucket=settings.r2_bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
        keys.append(key)
    return keys
