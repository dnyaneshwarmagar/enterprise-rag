import pytest
from fastapi.testclient import TestClient

from app.config import document_admin_enabled, settings
from app.documents import (
    DuplicateDocument,
    audience_name,
    index_upload,
    remove_document,
    require_document_manager,
    roles_for_audience,
    safe_relative_path,
    safe_upload_name,
)
from app.ingestion.identity import content_sha256
from app.main import app


@pytest.fixture(autouse=True)
def _document_flag_follows_settings(monkeypatch):
    """Keep these tests on the settings value. The live `.env` is read separately."""
    monkeypatch.setattr("app.config._document_admin_raw", lambda: None)


def test_document_admin_flag_follows_the_env_file(monkeypatch):
    monkeypatch.setattr("app.config._document_admin_raw", lambda: "false")
    assert document_admin_enabled() is False
    monkeypatch.setattr("app.config._document_admin_raw", lambda: "true")
    assert document_admin_enabled() is True
    monkeypatch.setattr("app.config._document_admin_raw", lambda: " fasle ")
    assert document_admin_enabled() is False


def test_audience_roles_include_everyone_when_public():
    assert roles_for_audience("public") == ["employee", "hr", "manager"]
    assert roles_for_audience("HR") == ["hr"]
    assert audience_name(["employee", "hr", "manager"]) == "public"
    assert audience_name(["manager"]) == "manager"


def test_upload_names_and_delete_paths_stay_inside_the_tree():
    assert safe_upload_name(r"..\notes.TXT") == "notes.TXT"
    try:
        safe_upload_name("notes.exe")
    except ValueError as exc:
        assert "pdf" in str(exc)
    else:
        raise AssertionError("an unsupported file was accepted")
    try:
        safe_relative_path("../cronjobs.docx")
    except ValueError:
        pass
    else:
        raise AssertionError("a parent path was accepted")


def test_same_bytes_are_not_uploaded_again(monkeypatch):
    digest = content_sha256(b"same bytes")
    assert digest == content_sha256(b"same bytes")
    monkeypatch.setattr(
        "app.documents.find_duplicate",
        lambda content_hash: {"relative_path": "true_data/note.txt"} if content_hash == digest else None,
    )
    called = {"processed": False}
    monkeypatch.setattr(
        "app.documents.process_document",
        lambda *args, **kwargs: called.__setitem__("processed", True),
    )
    try:
        index_upload("renamed.txt", b"same bytes", "public")
    except DuplicateDocument as exc:
        assert exc.relative_path == "true_data/note.txt"
        assert "already indexed" in str(exc)
    else:
        raise AssertionError("a duplicate file was uploaded")
    assert called["processed"] is False


def test_document_admin_is_closed_unless_enabled_for_a_manager():
    original = settings.DOCUMENT_ADMIN_ENABLED
    try:
        settings.DOCUMENT_ADMIN_ENABLED = False
        try:
            require_document_manager(("manager",))
        except Exception as exc:
            assert exc.status_code == 403
            assert "turned off" in exc.detail
        else:
            raise AssertionError("a disabled flag still allowed a manager")

        settings.DOCUMENT_ADMIN_ENABLED = True
        require_document_manager(("manager",))
        require_document_manager(("*",))
        try:
            require_document_manager(("employee",))
        except Exception as exc:
            assert "Only a manager" in exc.detail
        else:
            raise AssertionError("an employee was allowed to manage documents")
    finally:
        settings.DOCUMENT_ADMIN_ENABLED = original


def _open_api():
    settings.API_KEY = None
    settings.RAG_ROLE_KEYS = ""
    settings.RBAC_DEMO = True
    return TestClient(app)


def test_employee_and_disabled_flag_cannot_upload():
    original_key = settings.API_KEY
    original_map = settings.RAG_ROLE_KEYS
    original_demo = settings.RBAC_DEMO
    original_flag = settings.DOCUMENT_ADMIN_ENABLED
    try:
        client = _open_api()
        settings.DOCUMENT_ADMIN_ENABLED = False
        disabled = client.post(
            "/documents",
            data={"audience": "public", "demo_role": "manager"},
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
        assert disabled.status_code == 403
        assert "turned off" in disabled.json()["detail"]

        settings.DOCUMENT_ADMIN_ENABLED = True
        employee = client.post(
            "/documents",
            data={"audience": "public", "demo_role": "employee"},
            files={"file": ("note.txt", b"hello", "text/plain")},
        )
        assert employee.status_code == 403
        assert "Only a manager" in employee.json()["detail"]
    finally:
        settings.API_KEY = original_key
        settings.RAG_ROLE_KEYS = original_map
        settings.RBAC_DEMO = original_demo
        settings.DOCUMENT_ADMIN_ENABLED = original_flag


def test_manager_can_list_while_changes_are_turned_off(monkeypatch):
    original_key = settings.API_KEY
    original_map = settings.RAG_ROLE_KEYS
    original_demo = settings.RBAC_DEMO
    original_flag = settings.DOCUMENT_ADMIN_ENABLED
    try:
        settings.DOCUMENT_ADMIN_ENABLED = False
        client = _open_api()
        monkeypatch.setattr(
            "app.main.list_indexed_documents",
            lambda: [{"relative_path": "uploads/note.txt", "source_type": "general", "audience": "public"}],
        )
        listed = client.get("/documents", params={"demo_role": "manager"})
        assert listed.status_code == 200
        assert listed.json()["documents"][0]["relative_path"] == "uploads/note.txt"
        blocked = client.request(
            "DELETE",
            "/documents",
            json={"relative_path": "uploads/note.txt", "demo_role": "manager"},
        )
        assert blocked.status_code == 403
    finally:
        settings.API_KEY = original_key
        settings.RAG_ROLE_KEYS = original_map
        settings.RBAC_DEMO = original_demo
        settings.DOCUMENT_ADMIN_ENABLED = original_flag


def test_manager_upload_list_and_delete_use_the_selected_audience(monkeypatch):
    original_key = settings.API_KEY
    original_map = settings.RAG_ROLE_KEYS
    original_demo = settings.RBAC_DEMO
    original_flag = settings.DOCUMENT_ADMIN_ENABLED
    captured = {}

    def fake_index(filename, content, audience):
        captured["filename"] = filename
        captured["content"] = content
        captured["audience"] = audience
        return {
            "relative_path": "uploads/note.txt",
            "source_type": "general",
            "audience": audience,
            "access_roles": roles_for_audience(audience),
            "chunks": 1,
            "warnings": [],
        }

    try:
        settings.DOCUMENT_ADMIN_ENABLED = True
        client = _open_api()
        monkeypatch.setattr("app.main.index_upload", fake_index)
        monkeypatch.setattr(
            "app.main.list_indexed_documents",
            lambda: [
                {
                    "relative_path": "uploads/note.txt",
                    "source_type": "general",
                    "access_roles": ["hr"],
                    "audience": "hr",
                }
            ],
        )
        monkeypatch.setattr(
            "app.main.remove_document",
            lambda relative_path, source_type="general": {
                "relative_path": relative_path,
                "source_type": source_type,
                "removed_points": 1,
            },
        )

        uploaded = client.post(
            "/documents",
            data={"audience": "hr", "demo_role": "manager"},
            files={"file": ("note.txt", b"hr only", "text/plain")},
        )
        assert uploaded.status_code == 200
        assert captured == {"filename": "note.txt", "content": b"hr only", "audience": "hr"}
        assert uploaded.json()["access_roles"] == ["hr"]

        listed = client.get("/documents", params={"demo_role": "manager"})
        assert listed.status_code == 200
        assert listed.json()["documents"][0]["audience"] == "hr"

        hidden = client.get("/documents", params={"demo_role": "employee"})
        assert hidden.status_code == 403

        deleted = client.request(
            "DELETE",
            "/documents",
            json={"relative_path": "uploads/note.txt", "source_type": "general", "demo_role": "manager"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["removed_points"] == 1
    finally:
        settings.API_KEY = original_key
        settings.RAG_ROLE_KEYS = original_map
        settings.RBAC_DEMO = original_demo
        settings.DOCUMENT_ADMIN_ENABLED = original_flag


def test_delete_removes_the_index_before_storage(monkeypatch):
    calls = []

    def fake_points(source, relative):
        calls.append(("index", source, relative))
        return 4

    def fake_storage(source, relative):
        calls.append(("storage", source, relative))

    monkeypatch.setattr("app.documents.delete_document_points", fake_points)
    monkeypatch.setattr("app.documents.delete_stored_document", fake_storage)

    result = remove_document("uploads/note.txt", "general")

    assert result == {"relative_path": "uploads/note.txt", "source_type": "general", "removed_points": 4}
    assert calls == [("index", "general", "uploads/note.txt"), ("storage", "general", "uploads/note.txt")]


def test_delete_leaves_storage_when_the_index_has_no_points(monkeypatch):
    monkeypatch.setattr("app.documents.delete_document_points", lambda source, relative: 0)

    def fail_storage(source, relative):
        raise AssertionError("storage should stay")

    monkeypatch.setattr("app.documents.delete_stored_document", fail_storage)

    with pytest.raises(ValueError, match="not in the index"):
        remove_document("uploads/missing.txt")
