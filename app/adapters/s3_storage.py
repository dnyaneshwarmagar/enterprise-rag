"""Object storage adapter over app.ingestion.storage helpers."""

from __future__ import annotations

from app.ingestion import storage as storage_mod
from app.ingestion.storage import SourceDocument


class S3ObjectStorage:
    """Delegates to existing local/S3 storage helpers."""

    def enabled(self) -> bool:
        return storage_mod.s3_enabled()

    def hash_original(self, relative_path: str) -> str | None:
        return storage_mod.hash_stored_original(relative_path)

    def delete_document(self, source_type: str, relative_path: str) -> None:
        storage_mod.delete_stored_document(source_type, relative_path)

    def list_documents(self, target: str, explicit_source_type: str | None = None) -> list[SourceDocument]:
        return storage_mod.list_documents(target, explicit_source_type)
