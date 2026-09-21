"""Tests for the Albert API document operations of ``AIService``."""

import json

import pytest
import requests
import responses

from core.services import ai_service as ai_service_module
from core.services.ai_service import AIService

BASE_URL = "https://albert.test/v1"


@pytest.fixture(name="ai_service")
def fixture_ai_service(settings):
    """An AIService configured with a private collection."""
    settings.AI_BASE_URL = BASE_URL
    settings.AI_API_KEY = "test-key"
    settings.AI_MODEL = "test-model"
    settings.AI_PRIVATE_COLLECTION_ID = "291828"
    settings.AI_COLLECTION_IDS = []
    settings.AI_SEARCH_METHOD = "semantic"
    settings.AI_SEARCH_LIMIT = 5
    return AIService()


@responses.activate
def test_upload_text_document_sends_title_as_name_and_markdown_file(ai_service):
    """The title becomes the document name, the content a Markdown file."""
    upload = responses.post(f"{BASE_URL}/documents", status=201, json={"id": 42})

    document_id = ai_service.upload_text_document("Carte d'identité", "# Démarches")

    assert document_id == 42
    body = upload.calls[0].request.body
    assert b'name="name"\r\n\r\nCarte d\'identit\xc3\xa9' in body
    assert b'name="collection_id"\r\n\r\n291828' in body
    assert b"Content-Type: text/markdown" in body
    assert "# Démarches".encode() in body


def test_upload_text_document_rejects_empty_content(ai_service):
    """An empty document is refused before any API call."""
    with pytest.raises(ValueError, match="vide"):
        ai_service.upload_text_document("Titre", "  \n")


@responses.activate
def test_upload_text_document_raises_with_api_error_detail(ai_service):
    """An API error keeps its detail."""
    responses.post(f"{BASE_URL}/documents", status=422, json={"detail": "bad"})

    with pytest.raises(requests.HTTPError, match="422"):
        ai_service.upload_text_document("Titre", "Contenu")


@responses.activate
def test_find_document_ids_by_name_keeps_exact_matches_across_pages(
    ai_service, monkeypatch
):
    """Every page is read and only identical names are kept."""
    monkeypatch.setattr(ai_service_module, "DOCUMENTS_PAGE_SIZE", 2)
    responses.get(
        f"{BASE_URL}/documents",
        match=[
            responses.matchers.query_param_matcher(
                {
                    "collection_id": "291828",
                    "name": "Titre",
                    "limit": "2",
                    "offset": "0",
                }
            )
        ],
        json={"data": [{"id": 1, "name": "Titre"}, {"id": 2, "name": "Titre bis"}]},
    )
    responses.get(
        f"{BASE_URL}/documents",
        match=[
            responses.matchers.query_param_matcher(
                {
                    "collection_id": "291828",
                    "name": "Titre",
                    "limit": "2",
                    "offset": "2",
                }
            )
        ],
        json={"data": [{"id": 3, "name": "Titre"}]},
    )

    assert ai_service.find_document_ids_by_name("Titre") == [1, 3]


@responses.activate
def test_delete_document_ignores_already_deleted_document(ai_service):
    """A 404 on delete means the document is already gone."""
    responses.delete(f"{BASE_URL}/documents/7", status=404, json={})

    ai_service.delete_document(7)


@responses.activate
def test_delete_document_raises_on_server_error(ai_service):
    """Other errors are raised."""
    responses.delete(f"{BASE_URL}/documents/7", status=500, json={})

    with pytest.raises(requests.HTTPError):
        ai_service.delete_document(7)


@responses.activate
def test_search_chunks_includes_private_collection_without_mutating_settings(
    ai_service, settings
):
    """The private collection is searched even without public collections,
    and repeated searches send the same collection list."""
    settings.AI_COLLECTION_IDS = ["150277", " 139226"]
    search = responses.post(f"{BASE_URL}/search", json={"data": []})

    ai_service.search_chunks("question")
    ai_service.search_chunks("question")

    for call in search.calls:
        assert json.loads(call.request.body)["collection_ids"] == [
            150277,
            139226,
            291828,
        ]
    assert settings.AI_COLLECTION_IDS == ["150277", " 139226"]


@responses.activate
def test_search_chunks_searches_private_collection_alone(ai_service):
    """Without public collections, the private collection is still searched."""
    search = responses.post(f"{BASE_URL}/search", json={"data": []})

    ai_service.search_chunks("question")

    assert json.loads(search.calls[0].request.body)["collection_ids"] == [291828]


class FakeCompletions:
    """Record the chat completion payload and return a canned answer."""

    def __init__(self):
        self.payload = None

    def create(self, **payload):
        """Return an OpenAI-like response."""
        self.payload = payload
        message = type("Message", (), {"content": "réponse"})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


def test_call_ai_api_sends_system_prompt_before_user_prompt(ai_service, monkeypatch):
    """Fixed rules go in a system message, the content in the user message."""
    completions = FakeCompletions()
    monkeypatch.setattr(ai_service.client.chat, "completions", completions)

    answer = ai_service.call_ai_api("contenu", system_prompt="règles")

    assert answer == "réponse"
    assert completions.payload["messages"] == [
        {"role": "system", "content": "règles"},
        {"role": "user", "content": "contenu"},
    ]


def test_call_ai_api_without_system_prompt_sends_only_user_prompt(
    ai_service, monkeypatch
):
    """Existing callers keep a single user message."""
    completions = FakeCompletions()
    monkeypatch.setattr(ai_service.client.chat, "completions", completions)

    ai_service.call_ai_api("contenu")

    assert completions.payload["messages"] == [{"role": "user", "content": "contenu"}]


@responses.activate
def test_ocr_document_sends_a_data_url_and_joins_pages(ai_service, settings):
    """Documents go as document_url, pages are joined in order."""
    settings.AI_OCR_MODEL = "ocr-model"
    ocr = responses.post(
        f"{BASE_URL}/ocr",
        json={"pages": [{"markdown": "Page 1"}, {"markdown": "Page 2"}]},
    )

    text = ai_service.ocr_document(b"%PDF", "application/pdf")

    assert text == "Page 1\n\nPage 2"
    body = json.loads(ocr.calls[0].request.body)
    assert body["model"] == "ocr-model"
    assert body["document"] == {
        "type": "document_url",
        "document_url": "data:application/pdf;base64,JVBERg==",
    }


@responses.activate
def test_ocr_document_sends_images_as_image_url(ai_service, settings):
    """Images use the image_url chunk type."""
    settings.AI_OCR_MODEL = "ocr-model"
    ocr = responses.post(f"{BASE_URL}/ocr", json={"pages": [{"markdown": "Avis"}]})

    ai_service.ocr_document(b"\x89PNG", "image/png")

    assert json.loads(ocr.calls[0].request.body)["document"] == {
        "type": "image_url",
        "image_url": "data:image/png;base64,iVBORw==",
    }


@responses.activate
def test_ocr_document_raises_with_api_error_detail(ai_service, settings):
    """OCR errors keep the API detail."""
    settings.AI_OCR_MODEL = "ocr-model"
    responses.post(f"{BASE_URL}/ocr", status=503, json={"detail": "busy"})

    with pytest.raises(requests.HTTPError, match="503"):
        ai_service.ocr_document(b"%PDF", "application/pdf")
