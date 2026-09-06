"""Tests for the pure record parsers in core.services.dns.records.

Record *selection* (which TXT value is an SPF/DMARC record at all) is kept
apart from reading what the record says, so these are tested apart too: a
malformed record still has to be selected, or it drops out of the duplicate
check that is the only thing that would report it.
"""

import pytest

from core.services.dns.records import (
    dmarc_policy,
    is_dmarc_record,
    is_spf_record,
    parse_dkim_tags,
    parse_dmarc_tags,
    parse_spf_terms,
    spf_syntax_is_valid,
)


class TestIsSpfRecord:
    @pytest.mark.parametrize(
        "value",
        [
            "v=spf1 -all",
            "v=spf1",
            # RFC 7208 12: ABNF literals are case-insensitive.
            "V=SPF1 -all",
            "v=SpF1 include:_spf.example.com ~all",
        ],
    )
    def test_selected(self, value):
        assert is_spf_record(value)

    @pytest.mark.parametrize(
        "value",
        [
            # RFC 7208 4.5: the version section ends at a space or the record
            # end, so a longer token is a different record entirely.
            "v=spf10 -all",
            "v=spf1x",
            "spf1 -all",
            "",
            "v=DMARC1;p=reject",
            # US-ASCII only (3.1): Unicode folding maps U+017F onto "s", which
            # must not make a non-record compare equal to one.
            "v=ſpf1 -all",
        ],
    )
    def test_not_selected(self, value):
        assert not is_spf_record(value)


class TestIsDmarcRecord:
    @pytest.mark.parametrize(
        "value",
        [
            "v=DMARC1;p=reject",
            "v=DMARC1; p=reject; sp=none",
            # RFC 7489 6.4: the "v" is an ABNF quoted literal and *WSP is
            # allowed on both sides of the "=".
            "V=DMARC1;p=none",
            "v = DMARC1;p=none",
            "v\t=\tDMARC1;p=none",
            # 6.6.3 selects on the version tag alone, before anything else is
            # validated, so a record with nothing after it still counts.
            "v=DMARC1",
        ],
    )
    def test_selected(self, value):
        assert is_dmarc_record(value)

    @pytest.mark.parametrize(
        "value",
        [
            # Needs the ";" of dmarc-sep or the end of the record.
            "v=DMARC1000;x=1",
            "v=DMARC10",
            # DMARC1 is spelled as explicit octets in the ABNF (%x44 %x4d ...)
            # and so, unlike SPF's "v=spf1", is case-SENSITIVE.
            "v=dmarc1;p=none",
            "v=Dmarc1;p=none",
            "",
            "v=spf1 -all",
            "google-site-verification=Ab1Cd2",
        ],
    )
    def test_not_selected(self, value):
        assert not is_dmarc_record(value)


class TestParseDmarcTags:
    def test_not_a_record(self):
        assert parse_dmarc_tags("v=spf1 -all") is None

    def test_tags_and_whitespace(self):
        assert parse_dmarc_tags("v=DMARC1; p = reject ; rua=mailto:d@example.com") == {
            "v": "DMARC1",
            "p": "reject",
            "rua": "mailto:d@example.com",
        }

    def test_tag_names_fold_but_values_do_not(self):
        # RFC 7489 6.4 spells tag names as quoted literals (case-insensitive);
        # a value such as a rua URI is returned as published.
        assert parse_dmarc_tags("v=DMARC1;P=reject;RUA=mailto:D@Example.COM") == {
            "v": "DMARC1",
            "p": "reject",
            "rua": "mailto:D@Example.COM",
        }

    def test_unreadable_spec_is_skipped_not_fatal(self):
        # 6.3 requires unknown tags be ignored, and selection already happened:
        # a broken neighbour must not hide the policy the record does state.
        assert parse_dmarc_tags("v=DMARC1;garbage;p=reject") == {
            "v": "DMARC1",
            "p": "reject",
        }

    def test_first_occurrence_wins(self):
        assert parse_dmarc_tags("v=DMARC1;p=reject;p=none")["p"] == "reject"


class TestDmarcPolicy:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("v=DMARC1;p=reject", "reject"),
            ("v=DMARC1;p=quarantine", "quarantine"),
            ("v=DMARC1;p=none", "none"),
            # Policy values are ABNF quoted literals too.
            ("v=DMARC1;p=Reject", "reject"),
            ("v=DMARC1;P=REJECT", "reject"),
            # "sp" governs subdomains, so reading it as the domain's own
            # policy — as a substring search for "p=none" does — is wrong.
            ("v=DMARC1;p=reject;sp=none", "reject"),
            ("v=DMARC1;sp=reject;p=none", "none"),
            # No "p", or one RFC 7489 6.4 does not define: nothing enforceable,
            # read as the weakest so it can never satisfy a stronger ask.
            ("v=DMARC1;rua=mailto:d@example.com", "none"),
            ("v=DMARC1;p=banish", "none"),
            ("v=DMARC1;p=", "none"),
        ],
    )
    def test_policy(self, value, expected):
        assert dmarc_policy(value) == expected

    def test_not_a_record_has_no_policy(self):
        assert dmarc_policy("v=spf1 -all") == "none"


class TestParseDkimTags:
    """Test DKIM tag parsing."""

    def test_basic_dkim_record(self):
        """Test parsing a standard DKIM record."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_reordered_tags(self):
        """Test parsing DKIM with reordered tags."""
        result = parse_dkim_tags("v=DKIM1; p=MIGfMA0; k=rsa")
        assert result == {"v": "DKIM1", "p": "MIGfMA0", "k": "rsa"}

    def test_with_t_s_flag(self):
        """Test parsing DKIM with t=s (strict) flag."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; t=s")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0", "t": "s"}

    def test_with_t_y_flag(self):
        """Test parsing DKIM with t=y (testing) flag."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; t=y")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0", "t": "y"}

    def test_with_t_y_s_flags(self):
        """Test parsing DKIM with t=y:s (testing+strict) flags."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; t=y:s")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0", "t": "y:s"}

    def test_whitespace_around_equals(self):
        """RFC 6376 3.2 allows folding whitespace on both sides of the '='."""
        result = parse_dkim_tags("v = DKIM1; k = rsa; p = MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_missing_v_defaults_to_dkim1(self):
        """RFC 6376 3.6.1: v= is optional in a key record and defaults to DKIM1."""
        result = parse_dkim_tags("k=rsa; p=MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_missing_k_defaults_to_rsa(self):
        """RFC 6376 3.6.1: k= is optional in a key record and defaults to rsa."""
        result = parse_dkim_tags("v=DKIM1; p=MIGfMA0")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_record_without_any_tag_returns_none(self):
        """A TXT record with no tag=value pair is not a DKIM record."""
        assert parse_dkim_tags("not a dkim record") is None

    def test_segment_without_equals_returns_none(self):
        """RFC 6376 3.2: every segment must be a tag=value pair."""
        assert parse_dkim_tags("v; v=DKIM1; k=rsa; p=MIGfMA0") is None
        assert parse_dkim_tags("k=rsa; p=MIGfMA0; trailing") is None

    def test_empty_tag_name_returns_none(self):
        """A segment with no tag name before the '=' is malformed."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; =MIGfMA0") is None

    def test_trailing_semicolon_is_valid(self):
        """A trailing semicolon is explicitly allowed by RFC 6376 3.2."""
        result = parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0;")
        assert result == {"v": "DKIM1", "k": "rsa", "p": "MIGfMA0"}

    def test_leading_semicolon_returns_none(self):
        """Only a trailing semicolon is optional; a leading one is malformed."""
        assert parse_dkim_tags("; v=DKIM1; k=rsa; p=MIGfMA0") is None

    def test_interior_empty_tag_spec_returns_none(self):
        """An empty tag-spec between two others is malformed."""
        assert parse_dkim_tags("v=DKIM1; k=rsa;; p=MIGfMA0") is None

    def test_second_trailing_semicolon_returns_none(self):
        """RFC 6376 3.2 allows one optional trailing semicolon, not two."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0;;") is None

    def test_tag_name_not_starting_with_alpha_returns_none(self):
        """RFC 6376 3.2: a tag name must start with an ALPHA."""
        assert parse_dkim_tags("v=DKIM1; 2x=junk; k=rsa; p=MIGfMA0") is None
        assert parse_dkim_tags("v=DKIM1; _x=junk; k=rsa; p=MIGfMA0") is None

    def test_vendor_tag_name_is_accepted(self):
        """Tags like "x-foo" are used in the wild and verify, so keep them."""
        result = parse_dkim_tags("v=DKIM1; x-vendor=junk; k=rsa; p=MIGfMA0")
        assert result == {
            "v": "DKIM1",
            "x-vendor": "junk",
            "k": "rsa",
            "p": "MIGfMA0",
        }

    def test_duplicate_p_tag_returns_none(self):
        """RFC 6376 3.2: a duplicate tag name invalidates the whole tag-list."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; p=AAAA; p=MIGfMA0") is None

    def test_duplicate_v_tag_returns_none(self):
        """A repeated v= tag invalidates the record even if both values match."""
        assert parse_dkim_tags("v=DKIM1; k=rsa; p=MIGfMA0; v=DKIM1") is None

    def test_v_not_first_returns_none(self):
        """Test that v= not being first tag returns None."""
        assert parse_dkim_tags("k=rsa; v=DKIM1; p=MIGfMA0") is None

    def test_wrong_version_returns_none(self):
        """Test that wrong DKIM version returns None."""
        assert parse_dkim_tags("v=DKIM2; k=rsa; p=MIGfMA0") is None

    def test_empty_string_returns_none(self):
        """Test that empty string returns None."""
        assert parse_dkim_tags("") is None


class TestParseSpfTerms:
    """Test SPF term parsing."""

    def test_basic_spf(self):
        """Test parsing a basic SPF record."""
        all_mech, terms = parse_spf_terms("v=spf1 include:_spf.example.com -all")
        assert all_mech == "-all"
        assert terms == {"+include:_spf.example.com"}

    def test_multiple_includes(self):
        """Test parsing SPF with multiple includes."""
        all_mech, terms = parse_spf_terms(
            "v=spf1 include:_spf.example.com include:other.com -all"
        )
        assert all_mech == "-all"
        assert terms == {"+include:_spf.example.com", "+include:other.com"}

    def test_tilde_all(self):
        """Test parsing SPF with ~all mechanism."""
        all_mech, _terms = parse_spf_terms("v=spf1 include:_spf.example.com ~all")
        assert all_mech == "~all"

    def test_not_spf_returns_none(self):
        """Test that non-SPF record returns None."""
        assert parse_spf_terms("not-an-spf-record") is None

    def test_version_is_case_insensitive(self):
        """RFC 7208 12: ABNF literals are case-insensitive."""
        all_mech, terms = parse_spf_terms("V=sPf1 MX -ALL")
        assert all_mech == "-all"
        assert terms == {"+mx"}

    def test_version_must_be_terminated(self):
        """RFC 7208 4.5: "v=spf10" is not an SPF record."""
        assert parse_spf_terms("v=spf10 include:_spf.example.com -all") is None

    def test_version_must_be_ascii(self):
        """RFC 7208 3.1 encodes records in US-ASCII and receivers compare
        bytes. The version section is matched case-insensitively (12), so the
        folding has to stay ASCII: Unicode maps U+017F onto "s", the one
        letter of "v=spf1" a lookalike can reach. U+212A onto "k" cannot be
        reached here at all -- there is no "k" in the version section.

        The paired assertion is that genuine ASCII case still matches. Without
        it, dropping re.IGNORECASE would leave this test green just as surely
        as dropping re.ASCII would break it."""
        assert parse_spf_terms("v=ſpf1 include:_spf.example.com -all") is None
        assert parse_spf_terms("V=SPF1 include:_spf.example.com -all") is not None

    def test_version_must_be_terminated_by_a_space(self):
        """RFC 7208 4.6.1 separates terms with SP alone. Receivers read a
        record broken by another control character as no record at all, so we
        must not report it as one either."""
        assert parse_spf_terms("v=spf1\tinclude:_spf.example.com -all") is None

    def test_empty_record_is_valid(self):
        """RFC 7208 4.5: a bare "v=spf1" is a record, with no terms."""
        all_mech, terms = parse_spf_terms("v=spf1")
        assert all_mech is None
        assert terms == set()

    def test_qualifiers_are_made_explicit(self):
        """An omitted qualifier means "+", so both spellings are one term."""
        _all_mech, terms = parse_spf_terms("v=spf1 +MX ip4:1.2.3.4")
        assert terms == {"+mx", "+ip4:1.2.3.4"}

    def test_bare_all_means_pass(self):
        """A bare "all" carries the implicit "+" qualifier."""
        all_mech, _terms = parse_spf_terms("v=spf1 mx all")
        assert all_mech == "+all"

    def test_first_all_wins(self):
        """RFC 7208 5.1: "all" always matches, so anything after it is never
        tested — including a second, stricter "all"."""
        all_mech, _terms = parse_spf_terms("v=spf1 +all -all")
        assert all_mech == "+all"

    def test_mechanisms_after_all_are_dropped(self):
        """Mechanisms listed after "all" MUST be ignored (RFC 7208 5.1)."""
        all_mech, terms = parse_spf_terms("v=spf1 mx -all ip4:1.2.3.4")
        assert all_mech == "-all"
        assert terms == {"+mx"}

    def test_modifiers_after_all_are_kept(self):
        """Modifiers are not mechanisms, so an "all" does not skip them."""
        _all_mech, terms = parse_spf_terms("v=spf1 -all exp=why.example.com")
        assert terms == {"exp=why.example.com"}


class TestSpfSyntaxIsValid:
    """Test the SPF record syntax check."""

    def test_known_mechanisms_and_modifiers(self):
        """Every mechanism of RFC 7208 5, plus modifiers, are accepted."""
        assert spf_syntax_is_valid(
            "v=spf1 a mx ptr ip4:1.2.3.4 ip6:2001:db8::1 exists:%{i}.e.com"
            " include:x.example.com redirect=y.example.com exp=z.example.com -all"
        )

    def test_unknown_mechanism(self):
        """An unknown bare term is a syntax error (RFC 7208 4.6)."""
        assert not spf_syntax_is_valid("v=spf1 include:x.example.com gibberish -all")

    def test_unknown_modifier_is_ignored(self):
        """RFC 7208 6: unrecognized modifiers MUST be ignored, not rejected."""
        assert spf_syntax_is_valid(
            "v=spf1 moo.cow-far_out=man:dog/cat ip4:1.2.3.4 -all"
        )

    def test_case_insensitive(self):
        """Mechanism names are case-insensitive (RFC 7208 12)."""
        assert spf_syntax_is_valid("v=spf1 MX Include:x.example.com -ALL")

    def test_well_formed_ip_literals(self):
        """ip4 and ip6 take an address with an optional CIDR length."""
        assert spf_syntax_is_valid(
            "v=spf1 ip4:1.2.3.4 ip4:192.0.2.0/24 ip6:2001:db8::1 ip6:2001:db8::/32 -all"
        )

    def test_malformed_ip4_literal(self):
        """RFC 7208 12 spells ip4-network as a dotted quad of 0-255 values."""
        assert not spf_syntax_is_valid("v=spf1 ip4:999.1.1.1 -all")

    def test_truncated_ip4_literal(self):
        """RFC 7208 5.6: parts may not be omitted in place of a CIDR."""
        assert not spf_syntax_is_valid("v=spf1 ip4:192.0.2 -all")

    def test_malformed_ip6_literal(self):
        """ip6-network is an address per RFC 4291 2.2."""
        assert not spf_syntax_is_valid("v=spf1 ip6:gggg::1 -all")

    def test_cidr_length_out_of_range(self):
        """RFC 7208 12 bounds the lengths at 32 for ip4 and 128 for ip6."""
        assert not spf_syntax_is_valid("v=spf1 ip4:1.2.3.4/33 -all")
        assert not spf_syntax_is_valid("v=spf1 ip6:2001:db8::1/129 -all")

    def test_dual_cidr_not_allowed_on_ip4(self):
        """The dual "//" form belongs to a and mx, not to ip4 and ip6."""
        assert not spf_syntax_is_valid("v=spf1 ip4:1.2.3.4//24 -all")

    def test_a_and_mx_bare_dual_cidr_is_checked(self):
        """With no domain-spec the whole argument is a dual-cidr-length, so
        there is nothing it could be confused with (RFC 7208 12)."""
        assert spf_syntax_is_valid("v=spf1 a/24//64 mx/32 a//128 -all")
        assert not spf_syntax_is_valid("v=spf1 mx/99 -all")
        assert not spf_syntax_is_valid("v=spf1 a//129 -all")
        assert not spf_syntax_is_valid("v=spf1 a/ -all")

    def test_a_and_mx_domain_spec_is_not_checked(self):
        """Known limitation: once a domain-spec is present it may hold a macro
        carrying a "/" of its own, so the argument is left alone."""
        assert spf_syntax_is_valid("v=spf1 a:foo.example.com/24//64 -all")
        assert spf_syntax_is_valid("v=spf1 mx:%{d}/99 -all")

    def test_defined_modifiers_need_a_domain_spec(self):
        """RFC 7208 6.1 and 6.2 spell redirect and exp with a domain-spec,
        which is never empty."""
        assert not spf_syntax_is_valid("v=spf1 redirect=")
        assert not spf_syntax_is_valid("v=spf1 exp= -all")

    def test_unknown_modifier_may_be_empty(self):
        """RFC 7208 12: unknown-modifier takes a macro-string, and that one
        is allowed to be empty."""
        assert spf_syntax_is_valid("v=spf1 zzz= -all")

    def test_non_ascii_lookalike_is_not_a_mechanism_name(self):
        """Case-insensitive matching must stay ASCII: U+017F case-folds to
        "s" and U+212A to "k", but receivers compare bytes."""
        assert not spf_syntax_is_valid("v=spf1 ſoo=x -all")

    def test_qualified_modifier(self):
        """RFC 7208 4.6.1: a qualifier belongs to a directive. A modifier is a
        bare "name=value", so a qualified one is neither."""
        assert not spf_syntax_is_valid("v=spf1 +redirect=a.example.com")
        assert not spf_syntax_is_valid("v=spf1 -zzz=one -all")

    def test_modifier_name_must_follow_the_grammar(self):
        """RFC 7208 12: name = ALPHA *( ALPHA / DIGIT / "-" / "_" / "." )."""
        assert not spf_syntax_is_valid("v=spf1 1bad=x -all")
        assert not spf_syntax_is_valid("v=spf1 =x -all")
        assert spf_syntax_is_valid("v=spf1 moo.cow-far_out=man:dog/cat -all")

    def test_repeated_redirect_modifier(self):
        """RFC 7208 6: redirect= MUST NOT appear more than once."""
        assert not spf_syntax_is_valid(
            "v=spf1 redirect=a.example.com redirect=b.example.com"
        )

    def test_repeated_exp_modifier(self):
        """RFC 7208 6: exp= MUST NOT appear more than once."""
        assert not spf_syntax_is_valid(
            "v=spf1 exp=a.example.com exp=b.example.com -all"
        )

    def test_repeated_unknown_modifier_is_allowed(self):
        """Only redirect= and exp= are capped; others MUST just be ignored."""
        assert spf_syntax_is_valid("v=spf1 zzz=one zzz=two -all")

    def test_mechanism_missing_its_required_argument(self):
        """include, exists, ip4 and ip6 all spell a mandatory ":" argument."""
        assert not spf_syntax_is_valid("v=spf1 include -all")
        assert not spf_syntax_is_valid("v=spf1 include: -all")
        assert not spf_syntax_is_valid("v=spf1 ip4 -all")

    def test_all_takes_no_argument(self):
        """RFC 7208 12 spells all as the bare word."""
        assert not spf_syntax_is_valid("v=spf1 -all:example.com")

    def test_optional_argument_may_be_omitted(self):
        """a, mx and ptr default to the current domain."""
        assert spf_syntax_is_valid("v=spf1 a mx ptr -all")
        assert not spf_syntax_is_valid("v=spf1 a: -all")

    def test_non_spf_value(self):
        """A value that is not a record at all cannot be a valid one."""
        assert not spf_syntax_is_valid("google-site-verification=abc123")

    def test_modifiers_keep_no_qualifier(self):
        """Modifiers are name=value pairs and take no qualifier."""
        _all_mech, terms = parse_spf_terms("v=spf1 redirect=_spf.example.com")
        assert terms == {"redirect=_spf.example.com"}

    def test_macro_argument_keeps_its_case(self):
        """ "%{s}" and "%{S}" do not expand the same way (RFC 7208 7.3)."""
        _all_mech, terms = parse_spf_terms("v=spf1 exists:%{S}.example.com")
        assert terms == {"+exists:%{S}.example.com"}

    def test_no_all_mechanism(self):
        """Test parsing SPF without an all mechanism."""
        all_mech, terms = parse_spf_terms("v=spf1 include:_spf.example.com")
        assert all_mech is None
        assert terms == {"+include:_spf.example.com"}
