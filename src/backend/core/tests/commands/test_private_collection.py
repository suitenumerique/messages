"""Tests for the ``private_collection`` management command."""

from io import StringIO
from unittest.mock import MagicMock, call, patch

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from core.management.commands import private_collection
from core.services.docs import DocsDocument, DocsError


@pytest.fixture(autouse=True)
def _docs_credentials(monkeypatch):
    """Credentials in the environment, no browser session by default."""
    monkeypatch.delenv("DOCS_SESSIONID", raising=False)
    monkeypatch.setenv("DOCS_EMAIL", "user1@example.local")
    monkeypatch.setenv("DOCS_PASSWORD", "secret")


@pytest.fixture(name="docs_client")
def fixture_docs_client():
    """Patch the Docs client and yield the instance used by the command."""
    with patch.object(private_collection, "DocsClient") as factory:
        client = MagicMock()
        factory.return_value = client
        yield client


@pytest.fixture(name="ai_service")
def fixture_ai_service():
    """Patch the AI service and yield the instance used by the command."""
    with patch.object(private_collection, "AIService") as factory:
        service = MagicMock()
        service.find_document_ids_by_name.return_value = []
        factory.return_value = service
        yield service


def _with_documents(docs_client, *documents):
    docs_client.iter_document_ids.return_value = [document.id for document in documents]
    by_id = {document.id: document for document in documents}
    docs_client.get_document.side_effect = by_id.__getitem__


def _run(*args):
    out, err = StringIO(), StringIO()
    call_command("private_collection", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_from_docs_imports_every_document_with_title_and_content(
    docs_client, ai_service
):
    """Each Docs document is uploaded under its title."""
    _with_documents(
        docs_client,
        DocsDocument("1", "Carte d'identité", "# Carte"),
        DocsDocument("2", "Déménagement", "# Adresse"),
    )

    out, _ = _run("--from-docs")

    docs_client.login.assert_called_once_with("user1@example.local", "secret")
    assert ai_service.upload_text_document.call_args_list == [
        call("Carte d'identité", "# Carte"),
        call("Déménagement", "# Adresse"),
    ]
    assert "Imported: 2, replaced: 0" in out


def test_from_docs_replaces_document_with_same_title_after_upload(
    docs_client, ai_service
):
    """The previous version is deleted only once the new one is uploaded."""
    _with_documents(docs_client, DocsDocument("1", "Carte", "# v2"))
    ai_service.find_document_ids_by_name.return_value = [10, 11]

    out, _ = _run("--from-docs")

    assert [c[0] for c in ai_service.method_calls[-3:]] == [
        "upload_text_document",
        "delete_document",
        "delete_document",
    ]
    assert "replaced: 1" in out


def test_from_docs_keeps_both_documents_sharing_a_title(docs_client, ai_service):
    """A second document with the same title does not delete the first one."""
    _with_documents(
        docs_client,
        DocsDocument("1", "Carte", "# A"),
        DocsDocument("2", "Carte", "# B"),
    )

    _run("--from-docs")

    ai_service.find_document_ids_by_name.assert_called_once_with("Carte")
    assert ai_service.upload_text_document.call_count == 2


def test_from_docs_skips_empty_documents(docs_client, ai_service):
    """An empty document is not sent to the API."""
    _with_documents(docs_client, DocsDocument("1", "Vide", " \n"))

    out, _ = _run("--from-docs")

    ai_service.upload_text_document.assert_not_called()
    assert "skipped (empty): 1" in out


def test_from_docs_continues_after_a_document_error_and_fails_at_the_end(
    docs_client, ai_service
):
    """One failing document does not stop the import, but the command fails."""
    _with_documents(
        docs_client,
        DocsDocument("1", "Cassé", "# A"),
        DocsDocument("2", "Carte", "# B"),
    )
    ai_service.upload_text_document.side_effect = [ValueError("trop gros"), 99]

    with pytest.raises(CommandError, match="1 Docs document"):
        _run("--from-docs")

    assert ai_service.upload_text_document.call_count == 2


def test_from_docs_dry_run_never_calls_the_ai_service(docs_client):
    """The dry run only lists the documents."""
    _with_documents(docs_client, DocsDocument("1", "Carte", "# A"))

    with patch.object(private_collection, "AIService") as ai_service_class:
        out, _ = _run("--from-docs", "--dry-run")

    ai_service_class.assert_not_called()
    assert "Would import: Carte" in out
    assert "Documents to import: 1" in out


def test_from_docs_uses_browser_session_without_login(
    docs_client, ai_service, monkeypatch
):
    """DOCS_SESSIONID replaces the login."""
    monkeypatch.setenv("DOCS_SESSIONID", "browser-session")
    _with_documents(docs_client)

    with patch.object(private_collection, "DocsClient") as factory:
        factory.return_value = docs_client
        _run("--from-docs")

    factory.assert_called_once_with(session_id="browser-session")
    docs_client.login.assert_not_called()
    docs_client.ensure_authenticated.assert_called_once()


def test_from_docs_reports_refused_login(docs_client, ai_service):
    """A refused login stops the command with its reason."""
    docs_client.login.side_effect = DocsError("Docs login refused")

    with pytest.raises(CommandError, match="Docs login refused"):
        _run("--from-docs")


def test_from_docs_reports_missing_credentials(docs_client, ai_service, monkeypatch):
    """Without a terminal, missing credentials give a clear error."""
    monkeypatch.delenv("DOCS_EMAIL")

    with patch("builtins.input", side_effect=EOFError):
        with pytest.raises(CommandError, match="credentials missing"):
            _run("--from-docs")


def test_requires_a_source():
    """Either a directory or Docs must be chosen."""
    with pytest.raises(CommandError):
        _run()
