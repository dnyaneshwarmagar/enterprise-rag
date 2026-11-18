"""Application service for document admin routes."""

from __future__ import annotations

from typing import Any

from app import documents as documents_mod
from app.documents import DuplicateDocument


class DocumentService:
    """Thin façade over documents helpers (looked up dynamically for test patches)."""

    def require_manager(self, roles: tuple[str, ...], *, changes: bool = True) -> None:
        documents_mod.require_document_manager(roles, changes=changes)

    def list_documents(self) -> list[dict[str, Any]]:
        return documents_mod.list_indexed_documents()

    def add_upload(self, filename: str, content: bytes, audience: str) -> dict[str, Any]:
        return documents_mod.index_upload(filename, content, audience)

    def delete(self, relative_path: str, source_type: str) -> dict[str, Any]:
        return documents_mod.remove_document(relative_path, source_type)


__all__ = ["DocumentService", "DuplicateDocument"]
