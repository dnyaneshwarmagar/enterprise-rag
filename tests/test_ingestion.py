import os

from app.config import settings
from app.ingestion.chunking.splitter import chunk_pages, chunk_text
from app.ingestion.identity import document_key, point_id
from app.ingestion.storage import default_ingest_target, infer_source_type, list_documents, parse_s3_uri


def test_chunks_never_exceed_the_size_limit():
    text = "word " * 1000
    chunks = chunk_text(text, chunk_size=1500, overlap=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1500 for chunk in chunks)


def test_single_newlines_are_split():
    text = "\n".join(f"line {index} " + ("x" * 40) for index in range(80))
    chunks = chunk_text(text, chunk_size=500, overlap=0)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_page_order_is_kept_on_chunks():
    pages = [(1, "A" * 1400), (2, "B" * 1400), (3, "C" * 200)]
    chunks = chunk_pages(pages, chunk_size=1500, overlap=0)
    assert [chunk.page_start for chunk in chunks] == [1, 2, 3]
    assert chunks[0].text.startswith("A")
    assert chunks[1].text.startswith("B")
    assert chunks[2].text.startswith("C")


def test_point_ids_are_stable_and_replace_the_same_chunk():
    key = document_key("true", "guide.pdf")
    assert point_id(key, 0) == point_id(key, 0)
    assert point_id(key, 0) != point_id(key, 1)
    assert point_id(document_key("noisy", "guide.pdf"), 0) != point_id(key, 0)


def test_nested_files_are_found_and_typed():
    import tempfile

    with tempfile.TemporaryDirectory() as root:
        nested = os.path.join(root, "true_data", "nested")
        os.makedirs(nested)
        os.makedirs(os.path.join(root, "noisy_data"))
        open(os.path.join(nested, "deep.txt"), "w", encoding="utf-8").close()
        open(os.path.join(root, "noisy_data", "note.txt"), "w", encoding="utf-8").close()

        documents = list_documents(root)
        found = {(item.source_type, item.relative_path) for item in documents}
        assert ("general", "true_data/nested/deep.txt") in found
        assert ("general", "noisy_data/note.txt") in found
        assert {item.source_type for item in documents} == {"general"}


def test_s3_uri_parse_and_source_names():
    assert parse_s3_uri("s3://docs-bucket/incoming/hr") == ("docs-bucket", "incoming/hr")
    assert infer_source_type("true_data") == "true_data"
    assert infer_source_type("noisy_data") == "noisy_data"


def test_default_ingest_target_is_the_bucket(monkeypatch):
    monkeypatch.setattr(settings, "S3_BUCKET", "docs-bucket")
    monkeypatch.setattr(settings, "S3_PREFIX", "documents")
    assert default_ingest_target() == "s3://docs-bucket/documents"


def test_default_ingest_target_is_local_without_a_bucket(monkeypatch):
    monkeypatch.setattr(settings, "S3_BUCKET", None)
    assert default_ingest_target() == "DATA"
