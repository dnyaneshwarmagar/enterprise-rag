"""Local and S3 document storage for ingestion.

Source files can be read from a directory or from ``s3://bucket/prefix``.
Parsed JSON, and local originals when a bucket is configured, are written to S3.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field

from app.config import settings
from app.observe import info, span


@dataclass
class SourceDocument:
    """One file to parse. ``open_path`` is a local filesystem path."""

    source_type: str
    relative_path: str
    local_path: str
    archive_key: str
    origin: str
    _temporary: str | None = field(default=None, repr=False)

    @property
    def filename(self) -> str:
        return os.path.basename(self.relative_path)

    def cleanup(self) -> None:
        if self._temporary and os.path.exists(self._temporary):
            os.remove(self._temporary)
            self._temporary = None


def infer_source_type(name: str) -> str:
    """Source label for a whole ingest target. Folder names are not corpora.

    ``true_data`` and ``noisy_data`` are mixed on purpose so retrieval quality
    can be judged later. They are not stored as separate source types.
    """
    return (name or "").strip() or "general"


def s3_enabled() -> bool:
    return bool(settings.S3_BUCKET)


def default_ingest_target() -> str:
    """Path used when ingestion is started with no directory argument.

    A configured bucket is the source of the files. Local ``DATA`` is only
    used when no bucket is set.
    """
    if s3_enabled():
        prefix = (settings.S3_PREFIX or "").strip("/")
        if prefix:
            return f"s3://{settings.S3_BUCKET}/{prefix}"
        return f"s3://{settings.S3_BUCKET}"
    return "DATA"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/prefix`` into bucket and prefix."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an S3 URI: {uri}")
    rest = uri[5:]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"S3 URI is missing a bucket: {uri}")
    return bucket, key.strip("/")


def list_documents(target: str, explicit_source_type: str | None = None) -> list[SourceDocument]:
    """List source files from a local directory or an ``s3://`` URI."""
    if target.startswith("s3://"):
        bucket, prefix = parse_s3_uri(target)
        return _list_s3(bucket, prefix, explicit_source_type)
    if not os.path.isdir(target):
        raise FileNotFoundError(f"path '{target}' does not exist.")
    return _list_local(target, explicit_source_type)


def upload_original(document: SourceDocument) -> str | None:
    """Copy a locally read file into the documents prefix. Returns the S3 URI."""
    if not s3_enabled() or document.origin.startswith("s3://"):
        return None
    key = _join_key(settings.S3_PREFIX, document.archive_key)
    _client().upload_file(document.local_path, settings.S3_BUCKET, key)
    return f"s3://{settings.S3_BUCKET}/{key}"


def hash_stored_original(relative_path: str) -> str | None:
    """SHA-256 of a stored original. Missing files return None."""
    import hashlib

    relative = relative_path.replace("\\", "/").lstrip("/")
    digest = hashlib.sha256()
    local_original = os.path.join("DATA", *relative.split("/"))
    if os.path.isfile(local_original):
        with open(local_original, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if not s3_enabled():
        return None
    try:
        obj = _client().get_object(Bucket=settings.S3_BUCKET, Key=_join_key(settings.S3_PREFIX, relative))
    except Exception:
        return None
    body = obj["Body"]
    for chunk in body.iter_chunks(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def delete_stored_document(source_type: str, relative_path: str) -> None:
    """Remove the original file and its parsed JSON from disk and S3."""
    relative = relative_path.replace("\\", "/").lstrip("/")
    with span("Delete storage", file=relative, source=source_type) as record:
        local_processed = os.path.join("processed_data", source_type, f"{relative}.json")
        removed_processed = os.path.isfile(local_processed)
        if removed_processed:
            os.remove(local_processed)
        local_original = os.path.join("DATA", *relative.split("/"))
        removed_original = os.path.isfile(local_original)
        if removed_original:
            os.remove(local_original)
        s3_deleted = False
        if s3_enabled():
            original_key = _join_key(settings.S3_PREFIX, relative)
            processed_key = _join_key(settings.S3_PROCESSED_PREFIX, source_type, f"{relative}.json")
            _client().delete_objects(
                Bucket=settings.S3_BUCKET,
                Delete={"Objects": [{"Key": original_key}, {"Key": processed_key}], "Quiet": True},
            )
            s3_deleted = True
        record.outputs(
            status="ok",
            local_processed=removed_processed,
            local_original=removed_original,
            s3=s3_deleted,
        )
        info(f"Deleted stored copies of {relative}.")


def upload_processed(source_type: str, relative_path: str, payload: bytes) -> str | None:
    """Write parsed JSON to the processed prefix. Returns the S3 URI."""
    if not s3_enabled():
        return None
    relative = relative_path.replace("\\", "/")
    key = _join_key(settings.S3_PROCESSED_PREFIX, source_type, f"{relative}.json")
    _client().put_object(
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=payload,
        ContentType="application/json",
    )
    return f"s3://{settings.S3_BUCKET}/{key}"


def _list_local(base_dir: str, explicit_source_type: str | None) -> list[SourceDocument]:
    """Walk the whole tree as one corpus. Subfolders stay in the relative path."""
    source_type = explicit_source_type or "general"
    documents: list[SourceDocument] = []
    for dirpath, dirnames, filenames in os.walk(base_dir):
        dirnames[:] = [name for name in dirnames if not name.startswith(".")]
        for filename in filenames:
            if filename.startswith("."):
                continue
            full_path = os.path.join(dirpath, filename)
            relative = os.path.relpath(full_path, base_dir).replace("\\", "/")
            documents.append(
                SourceDocument(
                    source_type=source_type,
                    relative_path=relative,
                    local_path=full_path,
                    archive_key=relative,
                    origin=full_path,
                )
            )
    return documents


def _list_s3(bucket: str, prefix: str, explicit_source_type: str | None) -> list[SourceDocument]:
    client = _client()
    list_prefix = f"{prefix}/" if prefix else ""
    documents: list[SourceDocument] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=list_prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if key.endswith("/") or os.path.basename(key).startswith("."):
                continue
            relative_key = key[len(list_prefix) :] if list_prefix and key.startswith(list_prefix) else key
            parts = [part for part in relative_key.split("/") if part]
            if not parts:
                continue
            source_type = explicit_source_type or "general"
            relative_path = "/".join(parts)
            suffix = os.path.splitext(relative_path)[1]
            temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            temporary.close()
            client.download_file(bucket, key, temporary.name)
            documents.append(
                SourceDocument(
                    source_type=source_type,
                    relative_path=relative_path,
                    local_path=temporary.name,
                    archive_key=relative_key,
                    origin=f"s3://{bucket}/{key}",
                    _temporary=temporary.name,
                )
            )
    return documents


def _client():
    import boto3

    kwargs = {}
    if settings.AWS_REGION:
        kwargs["region_name"] = settings.AWS_REGION
    return boto3.client("s3", **kwargs)


def _join_key(*parts: str) -> str:
    cleaned = [part.strip("/") for part in parts if part and part.strip("/")]
    return "/".join(cleaned)
