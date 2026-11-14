"""Unit tests for :mod:`core.mda.addresses`."""

import pytest

from core.mda.addresses import (
    address_domain,
    address_local_part,
    ascii_lower,
    envelope_address,
    needs_smtputf8,
    normalize_address,
    normalize_domain,
    organizational_domain,
    split_address,
)

# Code points whose Unicode lowercase / case-folded / NFKC form is ASCII.
# Each is a way to spell an existing local part without being it; none may
# ever fold into one. This is the Django CVE-2019-19844 / GitHub
# password-reset attack shape.
#
# Written as escapes on purpose: a literal that silently degrades to its
# ASCII look-alike (a copy-paste through a lossy encoding) would turn every
# test below into a tautology instead of failing one.
KELVIN_SIGN = "\u212a"  # str.lower() -> "k"

UNICODE_ASCII_LOOKALIKES = [
    KELVIN_SIGN,
    "\u0130",  # LATIN CAPITAL LETTER I WITH DOT ABOVE, lower() -> "i" + U+0307
    "\u0131",  # LATIN SMALL LETTER DOTLESS I, upper() -> "I"
    "\u017f",  # LATIN SMALL LETTER LONG S, casefold() -> "s"
    "\ufb01",  # LATIN SMALL LIGATURE FI, NFKC -> "fi"
    "\uff41",  # FULLWIDTH LATIN SMALL LETTER A, NFKC -> "a"
]


# Code points paired with the ASCII letter they actually fold onto, and the
# transform that does it. A collision test must use a MATCHED pair: spoofing
# "nick" with U+017F can never collide (it folds to "s"), so such a case would
# pass against correct and broken code alike.
ASCII_FOLD_PAIRS = [
    ("\u212a", "k"),  # KELVIN SIGN, folds under str.lower()
    ("\u017f", "s"),  # LATIN SMALL LETTER LONG S, folds under str.casefold()
    ("\uff41", "a"),  # FULLWIDTH LATIN SMALL LETTER A, folds under NFKC
]


def test_lookalikes_are_really_non_ascii():
    """Guard the guard: an escape that degraded to ASCII voids every test."""
    for lookalike in UNICODE_ASCII_LOOKALIKES:
        assert not lookalike.isascii(), repr(lookalike)
    assert KELVIN_SIGN.lower() == "k"


@pytest.mark.parametrize(("lookalike", "ascii_char"), ASCII_FOLD_PAIRS)
def test_fold_pairs_really_collide(lookalike, ascii_char):
    """Each pair must collide under at least one plausible naive fold.

    A pair whose look-alike folds onto some other letter would make every
    collision test below vacuous: it would pass against correct and broken
    code alike.
    """
    import unicodedata

    folds = {
        lookalike.lower(),
        lookalike.casefold(),
        unicodedata.normalize("NFKC", lookalike).lower(),
    }
    assert ascii_char in folds, (repr(lookalike), folds)
    assert not lookalike.isascii()


class TestAsciiLower:
    """``ascii_lower`` folds A-Z and nothing else."""

    def test_folds_ascii_uppercase(self):
        assert ascii_lower("John.DOE-1_x") == "john.doe-1_x"

    def test_leaves_non_ascii_untouched(self):
        assert ascii_lower("JOSÉ") == "josÉ"
        assert ascii_lower("ÀÉÎÕÜ") == "ÀÉÎÕÜ"

    @pytest.mark.parametrize("lookalike", UNICODE_ASCII_LOOKALIKES)
    def test_never_folds_a_lookalike_onto_ascii(self, lookalike):
        """The whole point: no non-ASCII code point may become ASCII here."""
        folded = ascii_lower(lookalike)
        assert folded == lookalike
        assert not folded.isascii()

    def test_str_lower_would_have_folded_the_kelvin_sign(self):
        """Guard the premise: str.lower() really is unsafe for this."""
        assert "nicK".lower() == "nick"
        assert ascii_lower("nicK") != "nick"


class TestSplitAddress:
    """``split_address`` splits on the last @ and rejects malformed input."""

    def test_splits_on_the_last_at_sign(self):
        assert split_address('"weird@local"@example.com') == (
            '"weird@local"',
            "example.com",
        )

    def test_strips_surrounding_whitespace(self):
        assert split_address("  user@example.com  ") == ("user", "example.com")

    @pytest.mark.parametrize(
        "value", ["", "   ", "nodomain", "@example.com", "user@", "@"]
    )
    def test_returns_none_on_malformed(self, value):
        assert split_address(value) is None


class TestAddressParts:
    """``address_local_part`` / ``address_domain`` project ``split_address``."""

    def test_splits_on_the_last_at_sign(self):
        address = '"weird@local"@example.com'
        assert address_local_part(address) == '"weird@local"'
        assert address_domain(address) == "example.com"

    def test_preserves_case(self):
        address = "John.Doe@Example.COM"
        assert address_local_part(address) == "John.Doe"
        assert address_domain(address) == "Example.COM"

    @pytest.mark.parametrize(
        "value", ["", "   ", "nodomain", "@example.com", "user@", "@"]
    )
    def test_returns_empty_on_malformed(self, value):
        # Empty rather than None: every caller uses these for a display-name
        # fallback or a log field, where a falsy string is the useful default.
        assert address_local_part(value) == ""
        assert address_domain(value) == ""


class TestNormalizeDomain:
    """``normalize_domain`` lowercases and A-labels."""

    def test_lowercases_ascii(self):
        assert normalize_domain("EXAMPLE.COM") == "example.com"

    def test_encodes_idn_to_a_label(self):
        assert normalize_domain("exemplé.example") == "xn--exempl-gva.example"

    def test_a_label_round_trips(self):
        assert normalize_domain("XN--EXEMPL-GVA.EXAMPLE") == "xn--exempl-gva.example"

    def test_uts46_maps_fullwidth_to_ascii(self):
        """UTS-46 mapping is the standard DNS behaviour, unlike for local parts."""
        assert normalize_domain("\uff45xample.com") == "example.com"

    def test_leaves_ascii_that_idna_would_refuse(self):
        """An underscore label or a bare hostname must survive unchanged."""
        assert normalize_domain("_dmarc.Example.com") == "_dmarc.example.com"
        assert normalize_domain("LOCALHOST") == "localhost"

    def test_returns_undecodable_non_ascii_unchanged(self):
        assert normalize_domain("a..é") == "a..é"


class TestNormalizeAddress:
    """``normalize_address`` is total and folds both halves."""

    def test_folds_both_halves(self):
        assert normalize_address("John.DOE@EXEMPLÉ.example") == (
            "john.doe@xn--exempl-gva.example"
        )

    def test_keeps_non_ascii_local_part_as_is(self):
        assert normalize_address("JOSÉ@Example.com") == "josÉ@example.com"

    def test_folds_a_value_without_an_at_sign(self):
        assert normalize_address("  NOT-AN-EMAIL ") == "not-an-email"


class TestEnvelopeAddress:
    """``envelope_address`` produces the SMTP wire form, or nothing."""

    def test_keeps_local_part_case(self):
        assert envelope_address("John.Doe@Example.com") == "John.Doe@example.com"

    def test_idna_encodes_the_domain(self):
        assert envelope_address("user@exemplé.example") == (
            "user@xn--exempl-gva.example"
        )

    def test_keeps_a_non_ascii_local_part_intact(self):
        """It has no ASCII form, so it travels as-is on an RFC 6531 session."""
        assert envelope_address("josé@Exemplé.example") == (
            "josé@xn--exempl-gva.example"
        )

    def test_refuses_domain_with_no_a_label(self):
        assert envelope_address("user@a..é") is None

    @pytest.mark.parametrize("value", ["", "nodomain", "user@"])
    def test_refuses_malformed(self, value):
        assert envelope_address(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "foo\r\nRCPT TO:<victim@example.com>@example.com",
            "foo\nDATA@example.com",
            "a b@example.com",
            "two,addresses@example.com",
        ],
    )
    def test_refuses_a_local_part_with_no_wire_form(self, value):
        """The result becomes a MAIL FROM / RCPT TO argument.

        A local part carrying CR/LF could otherwise continue the SMTP
        dialogue, and one carrying a comma or a bare space could split into
        two recipients wherever the wire form is joined into a list.
        """
        assert envelope_address(value) is None

    def test_still_accepts_a_quoted_local_part(self):
        """Legal per RFC 5321, and the '@' inside must not be treated as one."""
        assert envelope_address('"weird@local"@Example.com') == (
            '"weird@local"@example.com'
        )

    def test_domain_is_always_ascii_encodable(self):
        """The domain must survive DNS and the RCPT TO command either way."""
        wire = envelope_address("User+tag@Exemplé.example")
        assert wire is not None
        wire.split("@")[1].encode("ascii")


class TestNeedsSmtputf8:
    """Only the local part decides; a domain always has an A-label."""

    def test_true_for_non_ascii_local_part(self):
        assert needs_smtputf8("josé@example.com") is True

    def test_false_for_non_ascii_domain_only(self):
        assert needs_smtputf8("user@exemplé.example") is False

    def test_false_for_plain_ascii(self):
        assert needs_smtputf8("John.Doe@Example.com") is False

    @pytest.mark.parametrize("lookalike", UNICODE_ASCII_LOOKALIKES)
    def test_true_for_a_lookalike_local_part(self, lookalike):
        assert needs_smtputf8(f"nic{lookalike}@example.com") is True

    @pytest.mark.parametrize("value", ["", "nodomain", "user@"])
    def test_false_for_malformed(self, value):
        assert needs_smtputf8(value) is False


class TestOrganizationalDomain:
    """PSL lookups, which relaxed DKIM alignment compares."""

    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("example.com", "example.com"),
            ("mail.example.com", "example.com"),
            ("a.b.c.example.com", "example.com"),
            # Multi-label public suffix: the registrable domain is two labels.
            ("example.co.uk", "example.co.uk"),
            ("mail.example.co.uk", "example.co.uk"),
            # PSL private section: github.io is a suffix, so each project is
            # its own organization.
            ("victim.github.io", "victim.github.io"),
        ],
    )
    def test_registrable_domain(self, domain, expected):
        assert organizational_domain(domain) == expected

    @pytest.mark.parametrize("suffix", ["com", "co.uk", "github.io"])
    def test_bare_public_suffix_falls_back_to_itself(self, suffix):
        """No registrable domain exists, so the input is returned.

        Returning the suffix's parent instead would let unrelated names
        collapse onto a shared value and compare as aligned.
        """
        assert organizational_domain(suffix) == suffix

    def test_unknown_suffix_falls_back_to_the_input(self):
        """A name under a suffix the bundled list predates stays itself."""
        assert organizational_domain("host.invalid-tld-xyz") == "host.invalid-tld-xyz"

    def test_distinct_domains_never_share_an_organizational_domain(self):
        """The property alignment depends on."""
        assert organizational_domain("a.co.uk") != organizational_domain("b.co.uk")
        assert organizational_domain("evil.com") != organizational_domain("example.com")
