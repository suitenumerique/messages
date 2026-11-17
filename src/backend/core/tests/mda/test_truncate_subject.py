"""Tests for ``core.mda.utils.truncate_subject``."""

from core.mda.utils import truncate_subject


class TestTruncateSubject:
    """``truncate_subject`` caps a subject and marks the cut with ``...``."""

    def test_keeps_a_subject_that_fits(self):
        """A subject at the limit is returned untouched, without ellipsis."""
        assert truncate_subject("a" * 10, 10) == "a" * 10

    def test_cuts_with_an_ellipsis(self):
        """A longer subject is cut so that the ellipsis still fits the limit."""
        assert truncate_subject("Re: " + "a" * 10, 10) == "Re: aaa..."

    def test_drops_the_space_before_the_ellipsis(self):
        """A cut right after a word does not leave ``word ...``."""
        assert truncate_subject("Re: ab cdefgh", 10) == "Re: ab..."
