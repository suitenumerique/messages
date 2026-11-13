"""Cutting a PEM file with several keys into one group per key.

The blocks here are not real keys: the module only recognises their
BEGIN/END lines. Loading them is tested with real keys in test_settings.py.
"""

from __future__ import annotations

import os

import pytest

from pymta import pem_bundle


def _block(label: str, body: str = "AAAA") -> str:
    return f"-----BEGIN {label}-----\n{body}\n-----END {label}-----\n"


KEY = _block("PRIVATE KEY", "key")
EC_KEY = _block("EC PRIVATE KEY", "eckey")
RSA_KEY = _block("RSA PRIVATE KEY", "rsakey")
ENCRYPTED_KEY = _block("ENCRYPTED PRIVATE KEY", "enc")
LEAF = _block("CERTIFICATE", "leaf")
CHAIN = _block("CERTIFICATE", "chain")


def test_two_keys_give_two_groups_each_with_its_chain():
    groups = pem_bundle.split(EC_KEY + LEAF + CHAIN + RSA_KEY + LEAF.replace("leaf", "l2"))

    assert groups == [
        "\n".join([EC_KEY.strip(), LEAF.strip(), CHAIN.strip()]) + "\n",
        "\n".join([RSA_KEY.strip(), LEAF.replace("leaf", "l2").strip()]) + "\n",
    ]


@pytest.mark.parametrize("text", ["", "no pem here", KEY + LEAF, LEAF, LEAF + CHAIN + KEY])
def test_at_most_one_key_is_left_to_openssl(text):
    assert pem_bundle.split(text) is None


@pytest.mark.parametrize("key", [KEY, EC_KEY, RSA_KEY, ENCRYPTED_KEY])
def test_every_private_key_label_starts_a_group(key):
    assert len(pem_bundle.split(key + LEAF + key + LEAF)) == 2


def test_other_blocks_and_surrounding_text_are_dropped():
    text = (
        "Bag Attributes\n    friendlyName: x\n"
        + EC_KEY
        + _block("DH PARAMETERS")
        + LEAF
        + "# a comment\n"
        + RSA_KEY
        + _block("EC PARAMETERS")
        + LEAF
    )
    groups = pem_bundle.split(text)
    assert len(groups) == 2
    assert not any(
        "PARAMETERS" in group or "Bag" in group or "comment" in group for group in groups
    )


def test_a_certificate_before_the_first_key_is_refused_with_the_name():
    with pytest.raises(ValueError, match=r"fullchain\.pem: a certificate comes before"):
        pem_bundle.split(LEAF + KEY + LEAF + KEY + LEAF, "fullchain.pem")


def test_a_key_without_a_certificate_is_refused():
    with pytest.raises(ValueError, match="a private key is not followed by its certificate"):
        pem_bundle.split(KEY + LEAF + KEY)


def test_mismatched_begin_and_end_lines_are_not_a_block():
    broken = "-----BEGIN PRIVATE KEY-----\nx\n-----END CERTIFICATE-----\n"
    assert pem_bundle.split(broken + LEAF + KEY + LEAF) is None


def test_a_large_file_without_end_lines_is_cut_in_reasonable_time():
    # Many BEGIN lines and no END: the pattern must not go quadratic.
    text = "-----BEGIN PRIVATE KEY-----\n" * 20_000
    assert pem_bundle.split(text) is None


def test_split_file_reads_the_file_and_names_it_in_errors(tmp_path):
    path = tmp_path / "bundle.pem"
    path.write_text(LEAF + KEY + LEAF + KEY + LEAF)
    with pytest.raises(ValueError, match="bundle.pem"):
        pem_bundle.split_file(str(path))
    path.write_text(KEY + LEAF + RSA_KEY + LEAF)
    assert len(pem_bundle.split_file(str(path))) == 2


def test_an_unreadable_file_raises_oserror(tmp_path):
    with pytest.raises(FileNotFoundError):
        pem_bundle.split_file(str(tmp_path / "missing.pem"))


@pytest.mark.parametrize("variant", ["memfd", "no_memfd", "no_proc"])
def test_readable_path_reads_back(monkeypatch, variant):
    memfd = variant == "memfd"
    if variant == "no_memfd":
        monkeypatch.delattr(pem_bundle.os, "memfd_create", raising=False)
    if variant == "no_proc":
        monkeypatch.setattr(pem_bundle.os.path, "isdir", lambda _path: False)
    with pem_bundle.readable_path(KEY + LEAF) as path:
        with open(path, encoding="ascii") as handle:
            assert handle.read() == KEY + LEAF
    if not memfd:
        assert not os.path.exists(path)


def test_the_in_memory_file_is_closed_even_when_the_caller_fails():
    with pytest.raises(RuntimeError), pem_bundle.readable_path(KEY) as path:
        fd = int(path.rsplit("/", 1)[1])
        raise RuntimeError
    with pytest.raises(OSError):
        os.fstat(fd)
