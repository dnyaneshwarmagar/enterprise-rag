import json
import os
import sys
from dataclasses import dataclass, field

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.access import roles_for_document
from app.config import settings
from app.observe import error, info, span, warning
from app.ingestion.chunking.splitter import chunk_pages
from app.ingestion.identity import document_key, file_sha256, point_id
from app.ingestion.loaders.html import parse_html
from app.ingestion.loaders.pdf import parse_pdf
from app.ingestion.loaders.text import parse_text
from app.ingestion.storage import (
    SourceDocument,
    default_ingest_target,
    list_documents,
    upload_original,
    upload_processed,
)
from app.services.retrieval.embedding import embed_texts, get_embedding_dim

PROCESSED_DATA_DIR = "processed_data"
UPSERT_BATCH = 64

qdrant_client = QdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY,
    timeout=120,
)


@dataclass
class FileResult:
    path: str
    status: str
    chunks: int = 0
    message: str = ""
    warnings: list[str] = field(default_factory=list)


def save_processed_locally(data: dict, source_type: str, relative_path: str) -> str:
    """Save parsed chunk metadata as JSON under processed_data/."""
    relative = relative_path.replace("\\", "/")
    dest = os.path.join(PROCESSED_DATA_DIR, source_type, f"{relative}.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return dest


def load_pages(document: SourceDocument) -> tuple[list[tuple[int | None, str]], list[str]]:
    """Return ordered (page, text) pairs. Non-PDF files use a single unpaged body."""
    ext = document.filename.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        return parse_pdf(document.local_path)
    if ext in ("html", "htm"):
        return [(None, parse_html(document.local_path))], []
    if ext == "txt":
        return [(None, parse_text(document.local_path))], []
    if ext in ("docx", "pptx"):
        from app.ingestion.loaders.office import parse_office

        return [(None, parse_office(document.local_path))], []
    raise ValueError(f"unsupported file type: {document.filename}")


def process_document(
    document: SourceDocument,
    access_roles: list[str] | None = None,
    content_hash: str | None = None,
) -> FileResult:
    """Parse, chunk, store, and replace this file's vectors in Qdrant."""
    label = document.origin
    with span("Processing File", file=document.relative_path, source=document.source_type) as record:
        try:
            try:
                with span("Parse", file=document.relative_path) as parse_record:
                    pages, warnings = load_pages(document)
                    pages = [(page, text) for page, text in pages if text and text.strip()]
                    parse_record.outputs(
                        status="ok" if pages else "failed",
                        pages=len(pages),
                        warnings=len(warnings),
                    )
            except ValueError as exc:
                record.outputs(status="skipped", message=str(exc))
                return FileResult(label, "skipped", message=str(exc))

            if not pages:
                detail = "; ".join(warnings) or "no text extracted"
                record.outputs(status="failed", stage="parse", message=detail)
                return FileResult(label, "failed", message=detail, warnings=warnings)

            chunks = chunk_pages(pages)
            if not chunks:
                record.outputs(status="failed", stage="chunk", message="chunking produced no text")
                return FileResult(label, "failed", message="chunking produced no text", warnings=warnings)

            key = document_key(document.source_type, document.relative_path)
            if access_roles is None:
                access_roles = roles_for_document(document.source_type, document.relative_path)
            if not content_hash and os.path.isfile(document.local_path):
                content_hash = file_sha256(document.local_path)
            processed = {
                "filename": document.filename,
                "relative_path": document.relative_path,
                "source_type": document.source_type,
                "document_key": key,
                "content_sha256": content_hash,
                "access_roles": access_roles,
                "chunks": [
                    {
                        "text": chunk.text,
                        "page_start": chunk.page_start,
                        "page_end": chunk.page_end,
                    }
                    for chunk in chunks
                ],
            }
            payload = json.dumps(processed, ensure_ascii=False, indent=2).encode("utf-8")
            with span("Upload", file=document.relative_path) as upload_record:
                local_path = save_processed_locally(processed, document.source_type, document.relative_path)
                stored = upload_processed(document.source_type, document.relative_path, payload)
                original = upload_original(document)
                upload_record.outputs(
                    status="ok",
                    processed=stored or local_path,
                    original=original or "already stored",
                )
            info(f"Saved processed data → {stored or local_path}")

            _replace_document_points(document, key, chunks, access_roles, content_hash)
            message = stored or local_path
            if original:
                message = f"{message}; original at {original}"
            record.outputs(status="indexed", chunks=len(chunks), points=len(chunks))
            return FileResult(label, "indexed", chunks=len(chunks), message=message, warnings=warnings)
        except Exception as exc:
            error(f"Failed to process {label}: {exc}")
            record.outputs(status="failed", message=str(exc))
            return FileResult(label, "failed", message=str(exc))
        finally:
            document.cleanup()


def document_points_filter(source_type: str, relative_path: str, key: str | None = None):
    """Match every point that belongs to one file."""
    identity = key or document_key(source_type, relative_path)
    return models.Filter(
        should=[
            models.FieldCondition(key="document_key", match=models.MatchValue(value=identity)),
            models.Filter(
                must=[
                    models.FieldCondition(
                        key="source",
                        match=models.MatchValue(value=relative_path),
                    ),
                    models.FieldCondition(
                        key="source_type",
                        match=models.MatchValue(value=source_type),
                    ),
                ]
            ),
        ]
    )


def delete_document_points(source_type: str, relative_path: str) -> int:
    """Remove one file's vectors. Returns how many points were stored before deletion."""
    with span("Delete index", file=relative_path, source=source_type, collection=settings.QDRANT_COLLECTION) as record:
        if not qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
            record.outputs(status="ok", points=0)
            return 0
        selector = document_points_filter(source_type, relative_path)
        counted = qdrant_client.count(
            collection_name=settings.QDRANT_COLLECTION,
            count_filter=selector,
            exact=True,
        )
        removed = int(counted.count)
        if removed:
            qdrant_client.delete(
                collection_name=settings.QDRANT_COLLECTION,
                points_selector=selector,
            )
        record.outputs(status="ok", points=removed)
        info(f"Deleted {removed} points for {relative_path}.")
        return removed


def _replace_document_points(
    document: SourceDocument,
    key: str,
    chunks,
    access_roles: list[str],
    content_hash: str | None = None,
) -> None:
    """Embed the new chunks, then replace this file's points in Qdrant."""
    with span("Embedding", file=document.relative_path, chunks=len(chunks)) as embed_record:
        embeddings = embed_texts([chunk.text for chunk in chunks])
        if len(embeddings) != len(chunks):
            raise RuntimeError(f"embedding count {len(embeddings)} != chunk count {len(chunks)}")
        embed_record.outputs(status="ok", vectors=len(embeddings))

    points = []

    for index, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        points.append(
            models.PointStruct(
                id=point_id(key, index),
                vector=vector,
                payload={
                    "text": chunk.text,
                    "source": document.relative_path,
                    "source_type": document.source_type,
                    "document_key": key,
                    "page": chunk.page_start,
                    "page_end": chunk.page_end,
                    "chunk_index": index,
                    "access_roles": access_roles,
                    "content_sha256": content_hash,
                },
            )
        )

    with span("Indexing", file=document.relative_path, collection=settings.QDRANT_COLLECTION, points=len(points)) as index_record:
        qdrant_client.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=document_points_filter(document.source_type, document.relative_path, key),
        )
        for start in range(0, len(points), UPSERT_BATCH):
            qdrant_client.upsert(
                collection_name=settings.QDRANT_COLLECTION,
                points=points[start : start + UPSERT_BATCH],
            )
        index_record.outputs(status="ok", points=len(points))
    info(f"Indexed {len(points)} points to Qdrant from {document.relative_path}.")


def _ensure_access_index() -> None:
    """Index payload fields used by role filters and document replacement.

    Qdrant Cloud rejects a filtered delete unless the filtered field is indexed.
    """
    for field_name in ("access_roles", "document_key", "source", "source_type", "content_sha256"):
        try:
            qdrant_client.create_payload_index(
                collection_name=settings.QDRANT_COLLECTION,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:
            warning(f"{field_name} index was not created: {exc}")


def ensure_collection(wipe: bool) -> None:
    """Drop the collection when requested, then create it if it is missing."""
    if wipe and qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
        qdrant_client.delete_collection(settings.QDRANT_COLLECTION)
        info(f"Collection '{settings.QDRANT_COLLECTION}' deleted.")

    if not qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
        dim = get_embedding_dim()
        qdrant_client.create_collection(
            collection_name=settings.QDRANT_COLLECTION,
            vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
        )
        info(f"Created collection '{settings.QDRANT_COLLECTION}' ({dim}-dim, Cosine).")

    _ensure_access_index()


def run_universal_ingestion(base_dir: str, explicit_source_type: str = None, wipe: bool = False) -> int:
    """Ingest every supported file under a directory or ``s3://`` prefix.

    Returns 0 when every supported file was indexed, 1 when any file failed.
    """
    with span("Universal Ingestion Started", base_directory=base_dir) as record:
        ensure_collection(wipe)
        documents = list_documents(base_dir, explicit_source_type)
        info(f"Found {len(documents)} files under {base_dir}.")

        results = [process_document(document) for document in documents]
        _print_summary(results)
        failed = [result for result in results if result.status == "failed"]
        indexed = [result for result in results if result.status == "indexed"]
        skipped = [result for result in results if result.status == "skipped"]
        record.outputs(
            status="failed" if failed else "ok",
            files=len(documents),
            indexed=len(indexed),
            skipped=len(skipped),
            failed=len(failed),
        )
        return 1 if failed else 0


def _print_summary(results: list[FileResult]) -> None:
    indexed = [result for result in results if result.status == "indexed"]
    skipped = [result for result in results if result.status == "skipped"]
    failed = [result for result in results if result.status == "failed"]
    lines = [
        f"Ingestion summary: indexed {len(indexed)}, skipped {len(skipped)}, failed {len(failed)}.",
    ]
    for result in results:
        if result.status == "indexed" and not result.warnings:
            continue
        detail = result.message or ", ".join(result.warnings)
        lines.append(f"{result.status.upper()} {result.path}: {detail}")
    summary = "\n".join(lines)
    print(summary)
    info(summary)


if __name__ == "__main__":
    # Usage:
    #   python -m app.ingestion.processor
    #   python -m app.ingestion.processor s3://bucket/documents --wipe
    #   python -m app.ingestion.processor DATA general
    wipe_requested = "--wipe" in sys.argv
    clean_args = [arg for arg in sys.argv if arg != "--wipe"]

    target_dir = clean_args[1] if len(clean_args) > 1 else default_ingest_target()
    explicit_type = clean_args[2] if len(clean_args) > 2 else None

    if not target_dir.startswith("s3://") and not os.path.exists(target_dir):
        print(f"Error: path '{target_dir}' does not exist.")
        sys.exit(1)

    sys.exit(run_universal_ingestion(target_dir, explicit_source_type=explicit_type, wipe=wipe_requested))
