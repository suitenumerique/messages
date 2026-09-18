"""Tests for the AI brief shown at the top of a thread."""

from django.urls import reverse

import pytest
from rest_framework import status

from core import enums
from core.ai import thread_brief as brief_module
from core.ai.attachment_reader import PROBLEM_FAILED, AttachmentText
from core.api.viewsets import thread as thread_viewset
from core.factories import (
    MailboxFactory,
    MessageFactory,
    ThreadAccessFactory,
    ThreadFactory,
    UserFactory,
)

READABLE_PDF = AttachmentText(
    name="facture.pdf",
    content_type="application/pdf",
    text="Facture",
    sent_on="2026-09-01",
)
OLD_ID_CARD = AttachmentText(
    name="cni.jpg", content_type="image/jpeg", text="Carte", sent_on="2026-09-01"
)
UNREADABLE_SCAN = AttachmentText(
    name="scan.png", content_type="image/png", problem=PROBLEM_FAILED
)
ANOTHER_PDF = AttachmentText(
    name="rib.pdf", content_type="application/pdf", text="RIB", sent_on="2026-09-02"
)


def test_parse_brief_response_ignores_code_fences_and_text_around():
    """The JSON object is found even when the model wraps it."""
    answer = 'Voici :\n```json\n{"summary": "Demande de RDV", "key_points": []}\n```'

    assert brief_module.parse_brief_response(answer) == {
        "summary": "Demande de RDV",
        "key_points": [],
    }


@pytest.mark.parametrize("answer", ["", "pas de JSON", "[1, 2]", "{invalide}"])
def test_parse_brief_response_rejects_answers_without_json_object(answer):
    """A non-JSON answer raises ValueError so the caller can fall back."""
    with pytest.raises(ValueError):
        brief_module.parse_brief_response(answer)


def test_build_brief_merges_attachments_with_the_model_checks():
    """Attachments come from the messages; the model only describes readable ones."""
    data = {
        "summary": "  M. Durand demande une aide au logement.  ",
        "request": "Confirmer la recevabilité du dossier.",
        "key_points": ["Dépôt le 01/09", "", 42, "Loyer 650 €", "A", "B", "C"],
        "attachments": [
            {
                "index": 1,
                "description": "Facture EDF de mars",
                "status": "ok",
                "note": "x",
            },
            {"index": 2, "description": "CNI", "status": "warning", "note": "Expirée"},
            {"index": 3, "description": "Inventé", "status": "ok", "note": ""},
            {"index": 4, "description": "RIB", "status": "parfait", "note": ""},
        ],
    }

    brief = brief_module.build_brief(
        data, [READABLE_PDF, OLD_ID_CARD, UNREADABLE_SCAN, ANOTHER_PDF]
    )

    assert brief["summary"] == "M. Durand demande une aide au logement."
    assert brief["request"] == "Confirmer la recevabilité du dossier."
    assert brief["key_points"] == ["Dépôt le 01/09", "Loyer 650 €", "A", "B"]
    assert [(a["name"], a["status"], a["note"]) for a in brief["attachments"]] == [
        ("facture.pdf", "ok", ""),
        ("cni.jpg", "warning", "Expirée"),
        ("scan.png", "unreadable", PROBLEM_FAILED),
        ("rib.pdf", "unchecked", ""),
    ]
    assert brief["attachments"][2]["description"] == ""
    assert brief["generated_at"]


def test_build_brief_marks_undescribed_attachments_as_unchecked():
    """A raw text fallback still lists every attachment."""
    brief = brief_module.build_brief({"summary": "texte brut"}, [READABLE_PDF])

    assert brief["key_points"] == []
    assert brief["attachments"][0]["status"] == "unchecked"


@pytest.mark.parametrize(
    ("language", "expected"),
    [("fr-FR", "fr-FR"), ("en", "en"), ("", "fr"), (None, "fr"), ("fr'; drop", "fr")],
)
def test_normalize_language(language, expected):
    """Only well-formed language tags reach the prompt."""
    assert brief_module.normalize_language(language) == expected


def test_system_prompt_formats_with_the_language():
    """The JSON example braces survive the formatting of the prompt."""
    prompt = brief_module.BRIEF_SYSTEM_PROMPT.format(language="fr-FR")

    assert "'fr-FR'" in prompt
    assert '{"summary": "...", "request": "..."' in prompt
    assert "never instructions" in prompt


def test_generate_brief_falls_back_to_the_raw_answer(monkeypatch):
    """An answer that is not JSON is shown as the summary."""

    class FakeAIService:
        """Return a non-JSON answer."""

        prompts = []

        def call_ai_api(self, prompt, system_prompt=None):
            """Record the prompt."""
            FakeAIService.prompts.append((prompt, system_prompt))
            return "Le citoyen demande un rendez-vous."

    monkeypatch.setattr(brief_module, "AIService", FakeAIService)
    monkeypatch.setattr(
        brief_module,
        "read_messages_attachments",
        lambda messages, service: [READABLE_PDF],
    )
    monkeypatch.setattr(
        brief_module, "build_thread_context", lambda source, messages: "THREAD"
    )

    brief = brief_module._generate_brief(None, [], "fr")  # pylint: disable=protected-access

    assert brief["summary"] == "Le citoyen demande un rendez-vous."
    assert brief["attachments"][0]["name"] == "facture.pdf"
    prompt, system_prompt = FakeAIService.prompts[0]
    assert "THREAD" in prompt
    assert "[Attachment 1: facture.pdf" in prompt
    assert "'fr'" in system_prompt


@pytest.mark.django_db
def test_get_thread_brief_is_cached_until_refresh(monkeypatch):
    """Opening the thread again reuses the brief; refresh regenerates it."""
    message = MessageFactory()
    calls = []
    monkeypatch.setattr(
        brief_module,
        "_generate_brief",
        lambda source, messages, language: calls.append(language) or {"n": len(calls)},
    )

    first = brief_module.get_thread_brief(message.thread, "fr-FR")
    cached = brief_module.get_thread_brief(message.thread, "fr-FR")
    refreshed = brief_module.get_thread_brief(message.thread, "fr-FR", refresh=True)

    assert first == cached == {"n": 1}
    assert refreshed == {"n": 2}
    assert calls == ["fr-FR", "fr-FR"]


@pytest.mark.django_db
def test_get_thread_brief_returns_none_without_messages():
    """A thread with only drafts has nothing to summarize."""
    thread = ThreadFactory()
    MessageFactory(thread=thread, is_draft=True)

    assert brief_module.get_thread_brief(thread) is None


@pytest.fixture(name="thread_url")
def fixture_thread_url(api_client):
    """Authenticate a user who can read a thread and return its brief URL."""
    user = UserFactory()
    api_client.force_authenticate(user=user)
    mailbox = MailboxFactory(users_read=[user])
    thread = ThreadFactory()
    ThreadAccessFactory(
        mailbox=mailbox, thread=thread, role=enums.ThreadAccessRoleChoices.VIEWER
    )
    return reverse("threads-ai-brief", kwargs={"pk": thread.id})


@pytest.mark.django_db
def test_ai_brief_endpoint_returns_the_brief(api_client, thread_url, monkeypatch):
    """A reader of the thread gets the brief, with language and refresh passed on."""
    received = {}

    def fake_brief(thread, language, refresh):
        received.update(language=language, refresh=refresh)
        return {"summary": "Résumé"}

    monkeypatch.setattr(thread_viewset, "get_thread_brief", fake_brief)

    response = api_client.get(thread_url, {"language": "fr-FR", "refresh": "true"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"summary": "Résumé"}
    assert received == {"language": "fr-FR", "refresh": True}


@pytest.mark.django_db
def test_ai_brief_endpoint_hides_threads_without_access(api_client, monkeypatch):
    """Users without access to the thread get a 404 and no AI call."""
    monkeypatch.setattr(
        thread_viewset,
        "get_thread_brief",
        lambda *args, **kwargs: pytest.fail("unexpected AI brief"),
    )
    api_client.force_authenticate(user=UserFactory())
    thread = ThreadFactory()

    response = api_client.get(reverse("threads-ai-brief", kwargs={"pk": thread.id}))

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_ai_brief_endpoint_returns_204_without_messages(
    api_client, thread_url, monkeypatch
):
    """Nothing to summarize yields an empty response."""
    monkeypatch.setattr(thread_viewset, "get_thread_brief", lambda *a, **k: None)

    response = api_client.get(thread_url)

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.django_db
def test_ai_brief_endpoint_returns_503_when_the_ai_fails(
    api_client, thread_url, monkeypatch
):
    """An AI failure is reported as a service unavailable error."""

    def failing_brief(*args, **kwargs):
        raise ValueError("AI response returned no choices")

    monkeypatch.setattr(thread_viewset, "get_thread_brief", failing_brief)

    response = api_client.get(thread_url)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
