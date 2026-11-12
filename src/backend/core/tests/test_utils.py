"""Tests for ``core.utils``."""

from datetime import datetime

from jmap_email import compose_email

from core.mda.utils import SNIPPET_MAX_CHARS, current_sent_at, message_snippet
from core.utils import (
    SecretFileDictValue,
    SecretFileJSONValue,
    SecretFileListValue,
)


class TestCurrentSentAt:
    """``current_sent_at`` is the single ``sentAt`` source for outbound
    composition — its output must round-trip through ``compose_email``
    and ``datetime.fromisoformat`` cleanly."""

    def test_round_trips_through_compose_email(self):
        """The string ``current_sent_at`` returns is accepted by the
        composer's strict ``sentAt`` validation — pin that contract so
        a future timezone / format change can't break outbound."""
        raw = compose_email(
            {
                "from": [{"email": "s@example.com"}],
                "to": [{"email": "r@example.com"}],
                "subject": "t",
                "sentAt": current_sent_at(),
                "textBody": [{"content": "body"}],
            }
        )
        assert raw.startswith(b"MIME-Version") or b"Date:" in raw

    def test_returns_parseable_iso_8601_with_offset(self):
        """The string contains a tz offset (``+`` / ``-`` / ``Z``) so
        ``datetime.fromisoformat`` returns a tz-aware datetime."""
        dt = datetime.fromisoformat(current_sent_at())
        assert dt.tzinfo is not None


class TestMessageSnippet:
    """``message_snippet`` resolves preview → textBody → htmlBody, and
    never falls back to the subject — an empty body is an empty
    snippet, display fallbacks are the frontend's concern."""

    def test_uses_preview_when_present(self):
        """The library's ``preview`` wins over textBody: since
        ``jmap_email.preview_text`` cleans BEFORE truncating, the
        precomputed preview is safe to re-truncate to the snippet
        budget."""
        parsed = {
            "preview": "Hello from preview",
            "textBody": [{"partId": "1", "content": "raw text body"}],
        }
        assert message_snippet(parsed) == "Hello from preview"

    def test_hand_built_dict_with_markdown_and_html(self):
        """Hand-built dicts (importers, autoreply) carry no preview:
        the textBody flows through the full cleaning pipeline."""
        content = (
            "<figcaption>Tableau de Corot</figcaption>\n\n"
            "# Voici un message\n\n**Bold** and [link](https://ex.co)"
        )
        parsed = {"textBody": [{"partId": "1", "content": content}]}
        assert message_snippet(parsed) == (
            "Tableau de Corot Voici un message Bold and link"
        )

    def test_falls_back_to_body_values_projection(self):
        """body_values=True projection: textBody[i] has no inline
        ``content`` — the helper reads through to ``bodyValues``."""
        parsed = {
            "textBody": [{"partId": "p1"}],
            "bodyValues": {
                "p1": {
                    "value": "From bodyValues",
                    "isEncodingProblem": False,
                    "isTruncated": False,
                }
            },
        }
        assert message_snippet(parsed) == "From bodyValues"

    def test_falls_back_to_html_body(self):
        """HTML-only hand-built dicts (importers) still get a snippet."""
        parsed = {"htmlBody": [{"partId": "1", "content": "<p>Only <b>html</b></p>"}]}
        assert message_snippet(parsed) == "Only html"

    def test_empty_parsed_returns_empty(self):
        """Empty parsed dict (header-only / parse failure / draft)
        returns ``""`` — no subject fallback baked in."""
        assert message_snippet({}) == ""

    def test_handles_none_parsed_email(self):
        """Defensive: ``None`` yields ``""`` rather than an
        AttributeError."""
        assert message_snippet(None) == ""

    def test_empty_text_body_list(self):
        """An empty ``textBody`` list (no parts at all) yields ``""``
        rather than crashing on ``textBody[0]``."""
        assert message_snippet({"textBody": []}) == ""

    def test_multiple_text_body_entries_uses_first(self):
        """When ``textBody`` carries several parts, only the first
        contributes — same behaviour the search-index consumer relies
        on."""
        parsed = {
            "textBody": [
                {"partId": "1", "content": "first"},
                {"partId": "2", "content": "second"},
            ],
        }
        assert message_snippet(parsed) == "first"

    def test_missing_partid_in_body_values(self):
        """A truncated walk (M22 part-count cap) can emit body parts
        whose ``partId`` does not appear in ``bodyValues``. The helper
        yields ``""`` rather than KeyError."""
        parsed = {
            "textBody": [{"partId": "p_missing"}],
            "bodyValues": {},
        }
        assert message_snippet(parsed) == ""

    def test_truncates_to_preview_max_length(self):
        """The snippet budget is set to 140 chars,
        — a single shared truncation, applied after cleaning."""
        out = message_snippet({"preview": "x" * 1024})
        assert len(out) == SNIPPET_MAX_CHARS


class TestSecretFileValues:
    """List/JSON settings holding secrets accept a ``{NAME}_FILE`` path, so they
    can be mounted from a secret store like the plain ``SecretFileValue`` ones."""

    def test_list_value_is_read_from_file(self, monkeypatch, tmp_path):
        """The file wins over the plain variable and is parsed as a list."""
        secret = tmp_path / "salt"
        secret.write_text("new_key,old_key\n")
        monkeypatch.setenv("TEST_SECRET_LIST", "ignored")
        monkeypatch.setenv("TEST_SECRET_LIST_FILE", str(secret))

        value = SecretFileListValue(
            [], environ_name="TEST_SECRET_LIST", environ_prefix=None
        )

        assert value == ["new_key", "old_key"]

    def test_list_value_falls_back_to_env_then_default(self, monkeypatch):
        """Without a file, the plain variable then the default are used."""
        monkeypatch.delenv("TEST_SECRET_LIST_FILE", raising=False)
        monkeypatch.setenv("TEST_SECRET_LIST", "a,b")
        assert SecretFileListValue(
            [], environ_name="TEST_SECRET_LIST", environ_prefix=None
        ) == ["a", "b"]

        monkeypatch.delenv("TEST_SECRET_LIST")
        assert SecretFileListValue(
            ["default"], environ_name="TEST_SECRET_LIST", environ_prefix=None
        ) == ["default"]

    def test_json_value_is_read_from_file(self, monkeypatch, tmp_path):
        """The file content is parsed as JSON."""
        secret = tmp_path / "keys.json"
        secret.write_text('{"1": {"algo": "aes-gcm", "secret": "s", "active": true}}')
        monkeypatch.setenv("TEST_SECRET_JSON_FILE", str(secret))

        value = SecretFileJSONValue(
            {}, environ_name="TEST_SECRET_JSON", environ_prefix=None
        )

        assert value == {"1": {"algo": "aes-gcm", "secret": "s", "active": True}}

    def test_dict_value_is_read_from_file(self, monkeypatch, tmp_path):
        """The file content is parsed as a dict."""
        secret = tmp_path / "spam"
        secret.write_text('{"rspamd_url": "http://mpa", "rspamd_auth": "token"}\n')
        monkeypatch.setenv("TEST_SECRET_DICT_FILE", str(secret))

        value = SecretFileDictValue(
            {}, environ_name="TEST_SECRET_DICT", environ_prefix=None
        )

        assert value == {"rspamd_url": "http://mpa", "rspamd_auth": "token"}
