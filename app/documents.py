"""Manager upload and delete for indexed documents.

The API and the UI both stay closed unless DOCUMENT_ADMIN_ENABLED is true.
Only the manager role, or an admin token, may add or remove a file.
"""

from __future__ import annotations

import os
import tempfile

from fastapi import HTTPException, status
from qdrant_client.http import models

from app.access import ADMIN_ROLE
from app.config import document_admin_enabled, settings
from app.ingestion.identity import content_sha256, document_key
from app.ingestion.processor import (
    FileResult,
    delete_document_points,
    ensure_collection,
    process_document,
    qdrant_client,
)
from app.ingestion.storage import SourceDocument, delete_stored_document, hash_stored_original
from app.observe import info, span

PUBLIC_ROLES = ("employee", "hr", "manager")
AUDIENCES = {
    "public": list(PUBLIC_ROLES),
    "employee": ["employee"],
    "hr": ["hr"],
    "manager": ["manager"],
}
ALLOWED_EXTENSIONS = {"pdf", "html", "htm", "txt", "docx", "pptx"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_MANAGER_ROLE = "manager"
_hashes_backfilled = False


class DuplicateDocument(Exception):
    """The uploaded bytes are already stored under another path."""

    def __init__(self, relative_path: str):
        self.relative_path = relative_path
        super().__init__(f"This file is already indexed as {relative_path}.")


def roles_for_audience(audience: str) -> list[str]:
    """Roles stored on a file for a chosen category. Public is every demo role."""
    chosen = (audience or "").strip().lower()
    if chosen not in AUDIENCES:
        raise ValueError("Choose public, employee, hr, or manager.")
    return list(AUDIENCES[chosen])


def audience_name(roles: list[str] | tuple[str, ...]) -> str:
    """Label stored roles. A file readable by every demo role is public."""
    cleaned = [role for role in roles if role]
    if set(cleaned) == set(PUBLIC_ROLES):
        return "public"
    if len(cleaned) == 1 and cleaned[0] in AUDIENCES:
        return cleaned[0]
    return ", ".join(cleaned) if cleaned else "employee"


def require_document_manager(roles: tuple[str, ...], *, changes: bool = True) -> None:
    """Reject a caller who is not a manager.

    Upload and delete also require DOCUMENT_ADMIN_ENABLED. Listing stays
    available so those buttons can be shown disabled while the flag is off.
    """
    if changes and not document_admin_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Document management is turned off.",
        )
    if ADMIN_ROLE not in roles and _MANAGER_ROLE not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a manager can add or delete documents.",
        )


def safe_upload_name(filename: str) -> str:
    """Keep the file name and drop any directory the client tried to send."""
    base = os.path.basename((filename or "").replace("\\", "/")).strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in base).strip(" .")
    if not cleaned or cleaned.startswith("."):
        raise ValueError("Choose a file with a name.")
    extension = cleaned.lower().rsplit(".", 1)[-1] if "." in cleaned else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Use a pdf, txt, html, docx, or pptx file.")
    return cleaned


def safe_relative_path(relative_path: str) -> str:
    """Reject paths that could leave the document tree."""
    relative = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in relative.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("Choose a document to delete.")
    return "/".join(parts)


def index_upload(filename: str, content: bytes, audience: str) -> dict:
    """Store one file and index it for the selected audience."""
    if not content:
        raise ValueError("The file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The file is larger than 25 MB.")
    name = safe_upload_name(filename)
    access_roles = roles_for_audience(audience)
    digest = content_sha256(content)
    existing = find_duplicate(digest)
    if existing:
        raise DuplicateDocument(existing["relative_path"])
    relative = f"uploads/{name}"
    suffix = os.path.splitext(name)[1]
    temporary = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    temporary.write(content)
    temporary.close()
    document = SourceDocument(
        source_type="general",
        relative_path=relative,
        local_path=temporary.name,
        archive_key=relative,
        origin=temporary.name,
        _temporary=temporary.name,
    )
    ensure_collection(False)
    result = process_document(document, access_roles=access_roles, content_hash=digest)
    if result.status != "indexed":
        raise ValueError(result.message or f"The file was {result.status}.")
    return _upload_payload(relative, access_roles, result)


def find_duplicate(content_hash: str) -> dict | None:
    """Return the indexed file whose bytes match, if one exists.

    Older files may not have a hash yet. Those originals are hashed once and
    the hash is saved on their points, so the next check is a lookup.
    """
    global _hashes_backfilled
    if not content_hash or not qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
        return None
    ensure_collection(False)
    found = _document_with_hash(content_hash)
    if found or _hashes_backfilled:
        return found
    _remember_missing_hashes()
    _hashes_backfilled = True
    return _document_with_hash(content_hash)


def _document_with_hash(content_hash: str) -> dict | None:
    points, _ = qdrant_client.scroll(
        collection_name=settings.QDRANT_COLLECTION,
        scroll_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="content_sha256",
                    match=models.MatchValue(value=content_hash),
                )
            ]
        ),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return None
    payload = points[0].payload or {}
    roles = [str(role) for role in (payload.get("access_roles") or [])]
    return {
        "relative_path": str(payload.get("source") or ""),
        "source_type": str(payload.get("source_type") or "general"),
        "access_roles": roles,
        "audience": audience_name(roles),
    }


def _remember_missing_hashes() -> None:
    """Hash stored originals that were indexed before content hashes existed."""
    missing: dict[str, str] = {}
    offset = None
    while True:
        points, offset = qdrant_client.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get("content_sha256"):
                continue
            relative = str(payload.get("source") or "")
            source_type = str(payload.get("source_type") or "general")
            key = str(payload.get("document_key") or document_key(source_type, relative))
            if relative and key not in missing:
                missing[key] = relative
        if offset is None:
            break
    for key, relative in missing.items():
        digest = hash_stored_original(relative)
        if not digest:
            continue
        qdrant_client.set_payload(
            collection_name=settings.QDRANT_COLLECTION,
            payload={"content_sha256": digest},
            points=models.Filter(
                must=[
                    models.FieldCondition(key="document_key", match=models.MatchValue(value=key))
                ]
            ),
        )


def list_indexed_documents() -> list[dict]:
    """One row per indexed file, with the audience stored on its chunks."""
    if not qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
        return []
    found: dict[str, dict] = {}
    offset = None
    while True:
        points, offset = qdrant_client.scroll(
            collection_name=settings.QDRANT_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            relative = str(payload.get("source") or "")
            source_type = str(payload.get("source_type") or "general")
            key = str(payload.get("document_key") or document_key(source_type, relative))
            if not relative or key in found:
                continue
            roles = [str(role) for role in (payload.get("access_roles") or [])]
            found[key] = {
                "relative_path": relative,
                "source_type": source_type,
                "access_roles": roles,
                "audience": audience_name(roles),
            }
        if offset is None:
            break
    return sorted(found.values(), key=lambda item: item["relative_path"])


def remove_document(relative_path: str, source_type: str = "general") -> dict:
    """Delete one file from the index and from storage."""
    relative = safe_relative_path(relative_path)
    source = (source_type or "general").strip() or "general"
    with span("Delete document", file=relative, source=source) as record:
        try:
            removed = delete_document_points(source, relative)
            if removed == 0:
                record.outputs(status="not_found", points=0)
                missing = True
            else:
                delete_stored_document(source, relative)
                record.outputs(status="deleted", points=removed)
                info(f"Deleted {relative}: {removed} points.")
                missing = False
        except Exception as exc:
            record.outputs(status="failed", message=str(exc))
            raise
    if missing:
        raise ValueError("That document is not in the index.")
    return {"relative_path": relative, "source_type": source, "removed_points": removed}


def _upload_payload(relative: str, access_roles: list[str], result: FileResult) -> dict:
    return {
        "relative_path": relative,
        "source_type": "general",
        "audience": audience_name(access_roles),
        "access_roles": access_roles,
        "chunks": result.chunks,
        "warnings": result.warnings,
    }
