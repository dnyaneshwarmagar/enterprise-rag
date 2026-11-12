import hashlib
import uuid

_NAMESPACE = uuid.UUID("6f1b9a3e-8c4d-4e2a-9b7f-1d5c0a8e4f21")


def content_sha256(content: bytes) -> str:
    """Hash of the original file bytes. Same bytes are the same document."""
    return hashlib.sha256(content).hexdigest()


def file_sha256(path: str) -> str:
    """Hash a file on disk without loading it all at once."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def document_key(source_type: str, relative_path: str) -> str:
    """Stable identity for one source file, used to replace its old vectors."""
    relative = relative_path.replace("\\", "/").lstrip("/")
    return f"{source_type}/{relative}"


def point_id(key: str, chunk_index: int) -> str:
    """Deterministic Qdrant point id. Re-ingesting the same chunk updates it."""
    return str(uuid.uuid5(_NAMESPACE, f"{key}#{chunk_index}"))
