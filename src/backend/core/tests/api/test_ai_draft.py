"""Tests for the AI reply prompt: the agent's instructions must drive the reply."""

import json

import pytest

from core.api.viewsets import ai_draft as ai_draft_module

AGENT_DRAFT = (
    "* proposer un rendez-vous mardi à 10h\n"
    "* rappeler le délai de 15 jours\n"
    '* conclure par "Nous restons à votre disposition."'
)
RAG_RESULTS = [
    {"chunk": {"content": "Le délai légal est de 15 jours."}},
    {"chunk": {"content": "Les pièces justificatives sont obligatoires."}},
]


class FakeAIService:
    """Record the prompts sent to the AI service instead of calling it."""

    calls = []

    def __init__(self, search_results=None):
        self.search_results = search_results or []

    def search_chunks(self, query):
        """Return canned RAG results."""
        return self.search_results

    def call_ai_api(self, prompt, system_prompt=None):
        """Record the call and return a canned reply."""
        FakeAIService.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return "Madame, Monsieur,"


@pytest.fixture(name="fake_ai")
def fixture_fake_ai(monkeypatch):
    """Replace the AI service and the DB-backed thread transcript."""
    FakeAIService.calls = []
    monkeypatch.setattr(
        ai_draft_module, "_build_thread_context", lambda message: "THREAD"
    )

    def install(search_results=None):
        monkeypatch.setattr(
            ai_draft_module,
            "AIService",
            lambda: FakeAIService(search_results=search_results),
        )
        return FakeAIService.calls

    return install


def test_rag_context_does_not_restrict_the_reply_to_the_excerpts():
    """The excerpts are a source, not the only allowed one."""
    context = ai_draft_module.build_rag_context(RAG_RESULTS)

    assert "uniquement" not in context
    assert "[Question]" not in context
    assert "[Extrait 1]\nLe délai légal est de 15 jours." in context
    assert "[Extrait 2]\nLes pièces justificatives sont obligatoires." in context


def test_system_prompt_makes_agent_instructions_trusted_and_exhaustive():
    """Agent facts are not inventions and every point must be addressed."""
    system_prompt = ai_draft_module.build_system_prompt(allow_lists=False)

    assert "Address every point" in system_prompt
    assert "trusted source" in system_prompt
    assert "verbatim" in system_prompt
    assert "nor the agent's instructions" in system_prompt
    assert "ignore the draft" not in system_prompt


def test_system_prompt_allows_lists_only_when_requested():
    """Lists are allowed when the agent's draft contains one."""
    with_lists = ai_draft_module.build_system_prompt(allow_lists=True)
    without_lists = ai_draft_module.build_system_prompt(allow_lists=False)

    assert "start each item on its own line with '- '" in with_lists
    assert "no bullet lists" not in with_lists
    assert "Do not use lists" in without_lists


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        (AGENT_DRAFT, True),
        ("- oui\n- mardi", True),
        ("1. premier point\n2. second point", True),
        ("oui, mardi", False),
        ("", False),
        (None, False),
    ],
)
def test_draft_has_list(draft, expected):
    """Bullet and numbered lists are detected in the agent draft."""
    assert ai_draft_module.draft_has_list(draft) is expected


def test_user_prompt_puts_agent_instructions_last(fake_ai):
    """Agent instructions come after the excerpts, right before the reply."""
    prompt = ai_draft_module.build_user_prompt(
        message=None,
        current_draft_text=AGENT_DRAFT,
        rag_context="[Extrait 1]\nLe délai légal est de 15 jours.",
    )

    thread_at = prompt.index("THREAD")
    excerpts_at = prompt.index("Le délai légal")
    instructions_at = prompt.index("proposer un rendez-vous mardi")
    reply_at = prompt.index("Draft reply:")
    assert thread_at < excerpts_at < instructions_at < reply_at
    assert prompt.count("Draft reply:") == 1
    assert AGENT_DRAFT in prompt


def test_user_prompt_omits_empty_sections(fake_ai):
    """No excerpts or instructions sections when there is nothing to show."""
    prompt = ai_draft_module.build_user_prompt(message=None, current_draft_text="  ")

    assert "Official reference excerpts" not in prompt
    assert "Agent's instructions" not in prompt
    assert prompt.rstrip().endswith("Draft reply:")


def test_reply_with_rag_sends_rules_as_system_and_every_bullet(fake_ai):
    """The full pipeline keeps all bullets and allows a list in the reply."""
    calls = fake_ai(search_results=RAG_RESULTS)

    reply = ai_draft_module.generate_ai_reply_body_with_rag(None, AGENT_DRAFT)

    assert reply == "Madame, Monsieur,"
    assert len(calls) == 1
    call = calls[0]
    assert call["system_prompt"] == ai_draft_module.build_system_prompt(
        allow_lists=True
    )
    for bullet in AGENT_DRAFT.splitlines():
        assert bullet in call["prompt"]
    assert call["prompt"].index("Le délai légal") < call["prompt"].index(
        "rappeler le délai"
    )


def test_reply_without_rag_results_still_sends_agent_instructions(fake_ai):
    """Without excerpts, the agent instructions are still in the prompt."""
    calls = fake_ai(search_results=[])

    ai_draft_module.generate_ai_reply_body_with_rag(None, "oui, mardi")

    call = calls[0]
    assert "Official reference excerpts" not in call["prompt"]
    assert "oui, mardi" in call["prompt"]
    assert call["system_prompt"] == ai_draft_module.build_system_prompt(
        allow_lists=False
    )


def test_blocknote_blocks_turns_list_lines_into_list_blocks():
    """List lines from the AI become BlockNote list items, the rest paragraphs."""
    text = (
        "Madame, Monsieur,\n\n"
        "Voici les prochaines étapes :\n"
        "- rendez-vous mardi à 10h\n"
        "* délai de 15 jours\n"
        "1. envoyer les pièces\n"
        "2) attendre la réponse\n\n"
        "Cordialement,"
    )

    blocks = json.loads(ai_draft_module.blocknote_blocks(text))

    assert [(block["type"], block["content"][0]["text"]) for block in blocks] == [
        ("paragraph", "Madame, Monsieur,"),
        ("paragraph", "Voici les prochaines étapes :"),
        ("bulletListItem", "rendez-vous mardi à 10h"),
        ("bulletListItem", "délai de 15 jours"),
        ("numberedListItem", "envoyer les pièces"),
        ("numberedListItem", "attendre la réponse"),
        ("paragraph", "Cordialement,"),
    ]


def test_blocknote_blocks_returns_an_empty_paragraph_for_blank_text():
    """A blank AI reply still yields a valid editor document."""
    assert json.loads(ai_draft_module.blocknote_blocks("  \n")) == [
        {"type": "paragraph", "content": ""}
    ]
