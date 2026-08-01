"""Tests for the ``search_reindex`` management command.

Focus on the orchestration surface: option validation and ``--from-date``
forwarding to the service layer. The underlying reindex logic is exercised
by its own unit tests.
"""

from datetime import datetime
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

import pytest


def _run(*args):
    """Invoke ``search_reindex`` with the given CLI arguments.

    Stdout is redirected to a StringIO sink to keep test output quiet.
    """
    out = StringIO()
    call_command("search_reindex", *args, stdout=out, stderr=out)
    return out.getvalue()


@pytest.fixture(name="patched_command_targets")
def fixture_patched_command_targets():
    """Patch every collaborator the command imports.

    The command references each helper via the absolute module path it was
    imported from, so we patch at *that* path rather than at the original
    definition.
    """
    targets = {}
    with (
        mock.patch(
            "core.management.commands.search_reindex.create_index_if_not_exists"
        ) as targets["create_index"],
        mock.patch("core.management.commands.search_reindex.delete_index") as targets[
            "delete_index"
        ],
        mock.patch(
            "core.management.commands.search_reindex._reindex_all_base",
            return_value={"success_count": 0, "failure_count": 0},
        ) as targets["reindex_all_base"],
        mock.patch(
            "core.management.commands.search_reindex._reindex_mailbox_base",
            return_value={"success_count": 0, "failure_count": 0},
        ) as targets["reindex_mailbox_base"],
        mock.patch("core.management.commands.search_reindex.reindex_all") as targets[
            "reindex_all_async"
        ],
        mock.patch(
            "core.management.commands.search_reindex.reindex_mailbox_task"
        ) as targets["reindex_mailbox_task"],
        mock.patch(
            "core.management.commands.search_reindex.reindex_thread_task"
        ) as targets["reindex_thread_task"],
    ):
        targets["reindex_all_async"].delay.return_value = mock.MagicMock(id="task-id")
        targets["reindex_mailbox_task"].delay.return_value = mock.MagicMock(
            id="task-id"
        )
        targets["reindex_thread_task"].delay.return_value = mock.MagicMock(id="task-id")
        targets["reindex_thread_task"].return_value = {"success": True}
        yield targets


class TestOptionValidation:
    """Argparse cannot express the cross-option rules — guard them here."""

    def test_from_date_with_thread_is_rejected(
        self,
        patched_command_targets,  # pylint: disable=unused-argument
    ):
        """``--from-date`` is meaningless on a single-thread reindex."""
        with pytest.raises(CommandError, match="incompatible with --thread"):
            _run(
                "--thread",
                "11111111-1111-1111-1111-111111111111",
                "--from-date",
                "2026-04-01",
            )

    def test_invalid_from_date_is_rejected(
        self,
        patched_command_targets,  # pylint: disable=unused-argument
    ):
        """A non-ISO string surfaces a clear error before the bulk job starts."""
        with pytest.raises(CommandError, match="Invalid --from-date"):
            _run("--all", "--from-date", "not-a-date")


class TestFromDateForwarding:
    """``--from-date`` must reach the service layer as a real ``datetime``."""

    def test_all_sync_forwards_datetime(self, patched_command_targets):
        """Sync ``--all`` calls the base helper with a parsed datetime.

        Naive ISO input is promoted to the active timezone so it can be
        compared against ``Thread.updated_at`` without ``USE_TZ=True``
        warnings.
        """
        _run("--all", "--from-date", "2026-04-01T08:30")
        call_kwargs = patched_command_targets["reindex_all_base"].call_args.kwargs
        assert call_kwargs["from_date"] == timezone.make_aware(
            datetime(2026, 4, 1, 8, 30)
        )

    def test_all_async_forwards_iso_string(self, patched_command_targets):
        """Async ``--all`` re-encodes the datetime as ISO for the JSON task payload."""
        _run("--all", "--async", "--from-date", "2026-04-01")
        patched_command_targets["reindex_all_async"].delay.assert_called_once_with(
            from_date_iso=timezone.make_aware(datetime(2026, 4, 1)).isoformat()
        )

    def test_mailbox_sync_forwards_datetime(self, patched_command_targets):
        """Per-mailbox sync run forwards the datetime the same way."""
        # The command does an UUID lookup before delegating; bypass it with
        # a mocked Mailbox.objects.get so the test stays unit-scoped.
        with mock.patch(
            "core.management.commands.search_reindex.models.Mailbox.objects.get",
            return_value=mock.MagicMock(),
        ):
            _run(
                "--mailbox",
                "11111111-1111-1111-1111-111111111111",
                "--from-date",
                "2026-04-01",
            )
        call_kwargs = patched_command_targets["reindex_mailbox_base"].call_args.kwargs
        assert call_kwargs["from_date"] == timezone.make_aware(datetime(2026, 4, 1))
