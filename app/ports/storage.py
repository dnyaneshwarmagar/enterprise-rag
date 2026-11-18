"""Object storage port for document originals / processed JSON."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.ingestion.storage import SourceDocument


class ObjectStorage(Protocol):
    """List / hash / delete stored source documents."""

    def enabled(self) -> bool:
        """True when a remote bucket is configured."""

    def hash_original(self, relative_path: str) -> str | None:
        """SHA-256 of a stored original, or None if missing."""

    def delete_document(self, source_type: str, relative_path: str) -> None:
        """Remove original + processed copies."""

    def list_documents(self, target: str, explicit_source_type: str | None = None) -> list[Any]:
        """List ingestible files from a local path or s3:// URI."""
