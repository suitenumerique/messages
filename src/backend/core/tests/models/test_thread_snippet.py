"""Tests for ``Thread.snippet`` derivation via ``update_stats`` and the
``MessageSerializer.snippet`` on-the-fly computation."""

from unittest.mock import patch

from django.utils import timezone

import pytest

from core import factories, models
from core.api import serializers

pytestmark = pytest.mark.django_db

_FIRST = b"From: a@example.com\r\nSubject: t\r\n\r\nFirst message body"
_SECOND = b"From: a@example.com\r\nSubject: t\r\n\r\nSecond message body"
_MARKDOWN = (
    b"From: a@example.com\r\nSubject: t\r\n\r\n"
    b"**Bold** intro with [a link](https://example.com/x)"
)


class TestThreadSnippetUpdateStats:
    """``update_stats`` is the only writer of ``Thread.snippet``: it re-derives
    it whenever ``messaged_at`` moves, or unconditionally when the caller
    forces it (paths that change the visible set without moving the
    timestamp)."""

    def test_snippet_follows_latest_visible_message(self):
        """Each new visible message moves the snippet forward."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        thread.update_stats()
        assert thread.snippet == "First message body"

        factories.MessageFactory(thread=thread, raw_mime=_SECOND)
        thread.update_stats()
        assert thread.snippet == "Second message body"

    def test_snippet_is_markdown_stripped(self):
        """Markdown text/plain parts come out as prose."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_MARKDOWN)
        thread.update_stats()
        assert thread.snippet == "Bold intro with a link"

    def test_deleting_last_message_falls_back_to_previous(self):
        """Removing the newest message re-derives from the one before it."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        last = factories.MessageFactory(thread=thread, raw_mime=_SECOND)
        thread.update_stats()
        assert thread.snippet == "Second message body"

        last.delete()
        thread.update_stats()
        assert thread.snippet == "First message body"

    def test_trashing_last_message_falls_back_to_previous(self):
        """A trashed message is not visible: the snippet steps back, and
        forward again on untrash."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        last = factories.MessageFactory(thread=thread, raw_mime=_SECOND)
        thread.update_stats()

        last.is_trashed = True
        last.save()
        thread.update_stats()
        assert thread.snippet == "First message body"

        last.is_trashed = False
        last.save()
        thread.update_stats()
        assert thread.snippet == "Second message body"

    def test_trashing_whole_thread_keeps_snippet(self):
        """Trashing every message keeps the stored snippet (and costs no
        parse): the trash view keeps its preview, and re-deriving would be
        impossible anyway — nothing visible remains."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        thread.update_stats()
        assert thread.snippet == "First message body"

        message.is_trashed = True
        message.save()
        with patch.object(models.Message, "get_parsed_data") as mock_parse:
            thread.update_stats()

        mock_parse.assert_not_called()
        assert thread.snippet == "First message body"
        assert thread.messaged_at is None

    def test_restoring_whole_thread_rederives(self):
        """A restore re-derives from whatever message it surfaces: nothing
        records which message the kept snippet came from, so keeping it
        blindly would go stale on a partial restore or after a purge — the
        blob read per restored thread is the price of always being right."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        thread.update_stats()
        message.is_trashed = True
        message.save()
        thread.update_stats()

        message.is_trashed = False
        message.save()
        thread.update_stats()

        assert thread.snippet == "First message body"
        assert thread.messaged_at is not None

    def test_new_message_on_trashed_thread_wins_over_kept_snippet(self):
        """A message arriving on a fully-trashed thread replaces the kept
        snippet — and it comes through a hinted path, so the re-derivation
        costs no blob read."""
        thread = factories.ThreadFactory()
        old = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        thread.update_stats()
        old.is_trashed = True
        old.save()
        thread.update_stats()
        assert thread.snippet == "First message body"

        new = factories.MessageFactory(thread=thread, raw_mime=_SECOND)
        thread.update_stats(source_message=new)

        assert thread.snippet == "Second message body"

    def test_partial_restore_of_older_message_rederives(self):
        """Restoring only an *older* message of a fully-trashed thread must
        not keep the newer trashed message's snippet: the restore re-derives
        from the message it actually surfaces."""
        thread = factories.ThreadFactory()
        older = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        newer = factories.MessageFactory(thread=thread, raw_mime=_SECOND)
        thread.update_stats()
        older.is_trashed = True
        older.save()
        newer.is_trashed = True
        newer.save()
        thread.update_stats()
        assert thread.snippet == "Second message body"

        older.is_trashed = False
        older.save()
        thread.update_stats()

        assert thread.snippet == "First message body"

    def test_forced_rederivation_covers_equal_created_at_delete(self):
        """Deleting the newest visible message when the remaining latest
        shares its ``created_at`` (bulk imports) leaves ``messaged_at`` in
        place, so the guard sees nothing — the deletion paths pass
        ``force_snippet`` for exactly this case."""
        thread = factories.ThreadFactory()
        stamp = timezone.now()
        first = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        second = factories.MessageFactory(thread=thread, raw_mime=_SECOND)
        models.Message.objects.filter(id__in=[first.id, second.id]).update(
            created_at=stamp
        )
        thread.update_stats()
        newest = second if second.id > first.id else first
        survivor_snippet = (
            "First message body" if newest.id == second.id else "Second message body"
        )

        newest.delete()
        thread.update_stats(force_snippet=True)

        assert thread.messaged_at == stamp
        assert thread.snippet == survivor_snippet

    def test_draft_messages_are_ignored(self):
        """Drafts never contribute — even when newer than the last visible
        message."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        factories.MessageFactory(thread=thread, is_draft=True)
        thread.update_stats()
        assert thread.snippet == "First message body"

    def test_empty_thread_has_empty_snippet(self):
        """No messages at all → empty snippet."""
        thread = factories.ThreadFactory()
        thread.update_stats()
        assert thread.snippet == ""

    def test_unreadable_blob_degrades_to_empty_snippet(self):
        """A corrupt blob or a storage failure must not abort the stats
        update: the snippet degrades to empty while the other stats
        (``messaged_at`` included) still land."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_FIRST)

        with patch.object(
            models.Message,
            "get_parsed_data",
            side_effect=RuntimeError("object storage unavailable"),
        ):
            thread.update_stats()

        assert thread.snippet == ""
        assert thread.messaged_at is not None

    def test_no_parse_when_messaged_at_unchanged(self):
        """Flag-style ``update_stats`` calls (read, starred…) must not pay a
        blob parse: the snippet is only re-derived when ``messaged_at``
        moves."""
        thread = factories.ThreadFactory()
        factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        thread.update_stats()

        with patch.object(models.Message, "get_parsed_data") as mock_parse:
            thread.update_stats()
        mock_parse.assert_not_called()
        assert thread.snippet == "First message body"


class TestThreadSnippetSourceHints:
    """``update_stats`` accepts the message (and its parsed MIME) the caller
    already holds, so delivery paths derive the snippet without a refetch or a
    second blob read."""

    def test_source_message_hint_avoids_the_refetch(self):
        """A hint matching the latest visible message is used in place of
        ``Message.objects.get``."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(thread=thread, raw_mime=_FIRST)

        with patch.object(
            models.Message.objects, "get", side_effect=AssertionError("refetched")
        ):
            thread.update_stats(source_message=message)

        assert thread.snippet == "First message body"

    def test_source_parsed_email_hint_avoids_the_blob_read(self):
        """With the parsed MIME supplied too, no blob is read at all."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        parsed = {"textBody": [{"partId": "1", "content": "From the hint"}]}

        with patch.object(models.Message, "get_parsed_data") as mock_parse:
            thread.update_stats(source_message=message, source_parsed_email=parsed)
        mock_parse.assert_not_called()

        assert thread.snippet == "From the hint"

    def test_body_values_projection_hint_is_supported(self):
        """Inbound callers parse with the library's default ``bodyValues``
        projection — the shape ``get_parsed_data`` does not produce."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        parsed = {
            "textBody": [{"partId": "1"}],
            "bodyValues": {"1": {"value": "From bodyValues"}},
        }

        thread.update_stats(source_message=message, source_parsed_email=parsed)

        assert thread.snippet == "From bodyValues"

    def test_hint_does_not_pollute_the_instance_parse_cache(self):
        """The hint feeds the snippet only: its projection differs from
        ``get_parsed_data``'s, so leaking it into the cache would corrupt every
        other consumer of that instance."""
        thread = factories.ThreadFactory()
        message = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        parsed = {"textBody": [{"partId": "1", "content": "From the hint"}]}

        thread.update_stats(source_message=message, source_parsed_email=parsed)

        assert message.get_parsed_data()["textBody"][0]["content"] == (
            "First message body"
        )

    def test_stale_hint_is_ignored(self):
        """A hint that is not the latest visible message must not win — the
        snippet still follows the thread's own latest message."""
        thread = factories.ThreadFactory()
        older = factories.MessageFactory(thread=thread, raw_mime=_FIRST)
        factories.MessageFactory(thread=thread, raw_mime=_SECOND)

        thread.update_stats(
            source_message=older,
            source_parsed_email={"preview": "stale hint content"},
        )

        assert thread.snippet == "Second message body"


class TestMessageSerializerSnippet:
    """``MessageSerializer.snippet`` is computed on the fly from the parsed
    blob — no stored per-message state."""

    def test_snippet_is_cleaned(self):
        """Markdown syntax is stripped in the serialized output."""
        message = factories.MessageFactory(raw_mime=_MARKDOWN)
        data = serializers.MessageSerializer(message).data
        assert data["snippet"] == "Bold intro with a link"

    def test_draft_has_empty_snippet(self):
        """A draft has no MIME blob → empty snippet."""
        message = factories.MessageFactory(is_draft=True)
        data = serializers.MessageSerializer(message).data
        assert data["snippet"] == ""
