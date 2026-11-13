"""Env-var parsing contracts in :mod:`pymta.settings`.

The settings module runs once at import, so a bad value has to fail there,
loudly, rather than surfacing later as strange SMTP behaviour.
"""

from __future__ import annotations

import importlib
import socket
import ssl
import subprocess
import threading
import time
import warnings

import pytest
from prometheus_client import REGISTRY

from pymta import controller, server, settings
from pymta.session_log import tls_failure_code
from pymta.settings import _env_address, _env_bool, _env_int, _env_str, _env_token


@pytest.fixture
def reload_settings(monkeypatch):
    """Re-import the settings module under a patched environment.

    Every constant is computed once at import, so re-running the module is the
    only way to exercise a fallback chain. The rest of the package reads these
    through the module object at call time, so the patched values have to be
    undone before the next test sees them.
    """

    def _reload():
        return importlib.reload(settings)

    yield _reload
    monkeypatch.undo()
    importlib.reload(settings)


def test_int_reads_env_over_default(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_INT", "42")
    assert _env_int("PYMTA_TEST_INT", 7, minimum=1) == 42


def test_int_falls_back_on_unset_and_blank(monkeypatch):
    monkeypatch.delenv("PYMTA_TEST_INT", raising=False)
    assert _env_int("PYMTA_TEST_INT", 7, minimum=1) == 7
    monkeypatch.setenv("PYMTA_TEST_INT", "   ")
    assert _env_int("PYMTA_TEST_INT", 7, minimum=1) == 7


def test_int_rejects_non_numeric(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_INT", "soon")
    with pytest.raises(ValueError, match="must be an integer"):
        _env_int("PYMTA_TEST_INT", 7, minimum=1)


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_int_rejects_values_below_the_minimum(monkeypatch, raw):
    # The settings that treat 0 as "disabled" declare minimum=0; everywhere
    # else 0 is nonsense that would otherwise fail silently, e.g. a
    # PYMTA_MAX_RECIPIENTS_PER_ENVELOPE of 0 would 452 every recipient.
    monkeypatch.setenv("PYMTA_TEST_INT", raw)
    with pytest.raises(ValueError, match="must be >= 1"):
        _env_int("PYMTA_TEST_INT", 7, minimum=1)


def test_data_timeout_must_exceed_the_reply_reserve(monkeypatch):
    # The handler subtracts PYMTA_REPLY_RESERVE_SECONDS from this budget. A DATA
    # timeout at or below it leaves the deliver call nothing, which would defer
    # every message rather than fail loudly at startup.
    monkeypatch.setenv("PYMTA_TEST_INT", str(settings.PYMTA_REPLY_RESERVE_SECONDS))
    with pytest.raises(ValueError, match="must be >="):
        _env_int("PYMTA_TEST_INT", 300, minimum=settings.PYMTA_REPLY_RESERVE_SECONDS + 1)
    assert settings.PYMTA_DATA_TIMEOUT > settings.PYMTA_REPLY_RESERVE_SECONDS


def test_int_allows_zero_where_it_means_disabled(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_INT", "0")
    assert _env_int("PYMTA_TEST_INT", 7, minimum=0) == 0


@pytest.mark.parametrize("raw", ["\r\n220 you are welcome here", "a\tb", "mx.exämple"])
def test_token_rejects_what_a_greeting_cannot_carry(monkeypatch, raw):
    # These land in the "220 {hostname} {ident}" banner; a CR/LF would append
    # attacker-chosen lines to our own greeting, and a TAB folds if the value
    # reaches a Received header. (NUL is also rejected but cannot be tested
    # through the environment: putenv refuses to store one.) A non-ASCII one
    # cannot be written at all with SMTPUTF8 off.
    monkeypatch.setenv("PYMTA_TEST_TOKEN", raw)
    with pytest.raises(ValueError, match="printable ASCII"):
        _env_token("PYMTA_TEST_TOKEN", "mta-in", spaces=True)


def test_token_takes_spaces_only_where_asked(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_TOKEN", "ESMTP ready")
    assert _env_token("PYMTA_TEST_TOKEN", "x", spaces=True) == "ESMTP ready"
    # A hostname with a space would give the greeting a second word.
    with pytest.raises(ValueError, match="spaces"):
        _env_token("PYMTA_TEST_TOKEN", "x")


def test_address_setting_is_read_and_normalised(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_ADDRESS", " Admin@Example.COM ")
    assert _env_address("PYMTA_TEST_ADDRESS") == "Admin@example.com"
    monkeypatch.delenv("PYMTA_TEST_ADDRESS")
    assert _env_address("PYMTA_TEST_ADDRESS") == ""


@pytest.mark.parametrize("raw", ["postmaster", "a@b@c", "user@", "a b@example.com"])
def test_address_setting_rejects_what_rcpt_would_refuse(monkeypatch, raw):
    # PYMTA_POSTMASTER_ADDRESS replaces a recipient: a value RCPT refuses would
    # turn every postmaster mail into a 5xx with no hint of why.
    monkeypatch.setenv("PYMTA_TEST_ADDRESS", raw)
    with pytest.raises(ValueError, match="PYMTA_TEST_ADDRESS"):
        _env_address("PYMTA_TEST_ADDRESS")


def test_token_checks_the_default_too(monkeypatch):
    # PYMTA_SMTP_HOSTNAME defaults to $MYHOSTNAME, which on k8s can come from the
    # downward API, so the fallback value needs the same check as the direct one.
    monkeypatch.delenv("PYMTA_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="printable ASCII"):
        _env_token("PYMTA_TEST_TOKEN", "bad\r\nvalue")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("OFF", False),
    ],
)
def test_bool_spellings(monkeypatch, raw, expected):
    monkeypatch.setenv("PYMTA_TEST_BOOL", raw)
    assert _env_bool("PYMTA_TEST_BOOL", not expected) is expected


def test_bool_unrecognised_value_is_refused(monkeypatch):
    # A typo must not read as its opposite: PYMTA_ENABLE_PROXY_PROTOCOL=Ture
    # silently disabling PROXY protocol is a security-relevant misconfiguration.
    monkeypatch.setenv("PYMTA_TEST_BOOL", "maybe")
    with pytest.raises(ValueError, match="not a recognised boolean"):
        _env_bool("PYMTA_TEST_BOOL", True)
    with pytest.raises(ValueError, match="not a recognised boolean"):
        _env_bool("PYMTA_TEST_BOOL", False)


def test_bool_blank_and_missing_take_the_default(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_BOOL", "   ")
    assert _env_bool("PYMTA_TEST_BOOL", True) is True
    monkeypatch.delenv("PYMTA_TEST_BOOL", raising=False)
    assert _env_bool("PYMTA_TEST_BOOL", False) is False


def test_str_treats_blank_as_unset(monkeypatch):
    monkeypatch.setenv("PYMTA_TEST_STR", "")
    assert _env_str("PYMTA_TEST_STR", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# PROXY protocol: `haproxy` is an alias for true, because that is the only
# protocol postscreen defines and the one implemented here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["haproxy", "HAProxy", " haproxy ", "true", "1", "on"])
def test_proxy_protocol_haproxy_is_an_alias_for_true(monkeypatch, reload_settings, raw):
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", raw)
    monkeypatch.setenv("PYMTA_TRUSTED_PROXIES", "192.0.2.10")
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is True


def test_proxy_protocol_without_an_allowlist_is_refused(monkeypatch, reload_settings):
    """An empty allowlist would mean every peer's header is believed."""
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", "true")
    monkeypatch.delenv("PYMTA_TRUSTED_PROXIES", raising=False)
    with pytest.raises(ValueError, match="PYMTA_TRUSTED_PROXIES is empty"):
        reload_settings()
    # Trusting every peer is still possible, said in so many words.
    monkeypatch.setenv("PYMTA_TRUSTED_PROXIES", "0.0.0.0/0,::/0")
    assert len(reload_settings().PYMTA_TRUSTED_PROXIES) == 2


@pytest.mark.parametrize("raw", ["", "false", "off", "no", "0"])
def test_proxy_protocol_off_spellings(monkeypatch, reload_settings, raw):
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", raw)
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is False


def test_default_command_timeout_meets_the_rfc_minimum(monkeypatch, reload_settings):
    # RFC 5321 §4.5.3.2.7: a server should wait at least 5 minutes for the
    # next command. A shorter default cuts slow but legitimate senders.
    monkeypatch.delenv("PYMTA_COMMAND_TIMEOUT", raising=False)
    assert reload_settings().PYMTA_COMMAND_TIMEOUT >= 300


def test_proxy_protocol_refuses_a_typo(monkeypatch, reload_settings):
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", "haprox")
    with pytest.raises(ValueError, match="not a recognised boolean"):
        reload_settings()


# ---------------------------------------------------------------------------
# Settings whose misconfiguration would otherwise be silent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cert, key", [("/tls/cert.pem", ""), ("", "/tls/key.pem")])
def test_half_configured_starttls_is_refused(monkeypatch, reload_settings, cert, key):
    # load_tls_context() returns None unless it has both, so one alone would
    # serve plaintext and simply not advertise STARTTLS — encryption lost with
    # nothing in the log to say so.
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", cert)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    with pytest.raises(ValueError, match="must be set together"):
        reload_settings()


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/tls/chain.pem,", ("/tls/chain.pem",)),
        (",/tls/chain.pem", ("/tls/chain.pem",)),
        ("/tls/a.pem,,/tls/b.pem", ("/tls/a.pem", "/tls/b.pem")),
    ],
)
def test_a_stray_comma_is_tolerated(monkeypatch, reload_settings, value, expected):
    """Postfix tolerated these, and the value may have been written for it.

    Refusing one would turn a working configuration into a process that will
    not boot, which on an MX stops inbound mail — a worse outcome than the
    typo. Nothing is lost silently: each surviving path is still loaded.
    """
    monkeypatch.delenv("PYMTA_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("PYMTA_TLS_KEY_FILE", raising=False)
    monkeypatch.setenv("STARTTLS_CHAIN_FILES", value)
    assert reload_settings().PYMTA_TLS_CERT_PAIRS == tuple((path, path) for path in expected)


@pytest.mark.parametrize("value", [",", ",,", " , "])
def test_a_value_naming_no_path_is_refused(monkeypatch, reload_settings, value):
    # It would otherwise leave both paths empty, which the pair check reads as
    # "STARTTLS deliberately off" — losing encryption without a word.
    monkeypatch.delenv("PYMTA_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("PYMTA_TLS_KEY_FILE", raising=False)
    monkeypatch.setenv("STARTTLS_CHAIN_FILES", value)
    with pytest.raises(ValueError, match="STARTTLS_CHAIN_FILES.*names no path"):
        reload_settings()


def test_chain_files_keeps_every_entry(monkeypatch, reload_settings):
    """Postfix's dual-cert list survives the move; dropping one would quietly
    stop serving that key type."""
    monkeypatch.delenv("PYMTA_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("PYMTA_TLS_KEY_FILE", raising=False)
    monkeypatch.setenv("STARTTLS_CHAIN_FILES", "/tls/rsa.pem, /tls/ecdsa.pem")
    # A Postfix bundle carries key and chain together, so each entry is both.
    assert reload_settings().PYMTA_TLS_CERT_PAIRS == (
        ("/tls/rsa.pem", "/tls/rsa.pem"),
        ("/tls/ecdsa.pem", "/tls/ecdsa.pem"),
    )


def test_separate_cert_and_key_lists_are_paired_in_order(monkeypatch, reload_settings):
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", "/tls/rsa.crt,/tls/ecdsa.crt")
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", "/tls/rsa.key,/tls/ecdsa.key")
    assert reload_settings().PYMTA_TLS_CERT_PAIRS == (
        ("/tls/rsa.crt", "/tls/rsa.key"),
        ("/tls/ecdsa.crt", "/tls/ecdsa.key"),
    )


def test_mismatched_list_lengths_are_refused(monkeypatch, reload_settings):
    # Otherwise a cert is loaded against another's key, and OpenSSL's complaint
    # about key values names neither variable.
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", "/tls/rsa.crt,/tls/ecdsa.crt")
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", "/tls/rsa.key")
    with pytest.raises(ValueError, match="paired in order"):
        reload_settings()


def test_starttls_off_when_both_are_empty(monkeypatch, reload_settings):
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", "")
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", "")
    fresh = reload_settings()
    assert fresh.PYMTA_TLS_CERT_FILE == ""
    assert fresh.PYMTA_TLS_CERT_PAIRS == ()


def _self_signed(tmp_path, name, keyopts):
    """A throwaway self-signed pair, built with the openssl already in the image."""
    key, crt = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-nodes",
            "-days",
            "1",
            *keyopts,
            "-keyout",
            str(key),
            "-out",
            str(crt),
            "-subj",
            f"/CN={name}.invalid",
        ],
        check=True,
        capture_output=True,
    )
    return str(crt), str(key)


def _cert_presented_to(ctx, ciphers):
    """DER of the certificate *ctx* serves a client offering only *ciphers*."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def serve():
            try:
                conn, _ = server.accept()
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except OSError:
                pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        # TLS 1.3 drops these suite names, so pin 1.2 to make the server's
        # choice of key type observable from the cipher list alone.
        client.maximum_version = ssl.TLSVersion.TLSv1_2
        client.set_ciphers(ciphers)
        with socket.create_connection(server.getsockname()) as raw:
            with client.wrap_socket(raw, server_hostname="x.invalid") as tls:
                der = tls.getpeercert(binary_form=True)
        thread.join(timeout=5)
        return der


def test_both_certificates_are_served_one_per_key_type(monkeypatch, reload_settings, tmp_path):
    """The point of configuring two: OpenSSL keeps a slot per key type and
    presents whichever the client said it can verify, so one listener serves
    ECDSA to modern senders and RSA to everything else."""
    rsa_crt, rsa_key = _self_signed(tmp_path, "rsa", ["-newkey", "rsa:2048"])
    ec_crt, ec_key = _self_signed(
        tmp_path, "ecdsa", ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"]
    )
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", f"{rsa_crt},{ec_crt}")
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", f"{rsa_key},{ec_key}")
    reload_settings()

    ctx = controller.load_tls_context()
    assert ctx is not None

    def der_of(path):
        with open(path, encoding="ascii") as handle:
            return ssl.PEM_cert_to_DER_cert(handle.read())

    assert _cert_presented_to(ctx, "ECDHE-ECDSA-AES128-GCM-SHA256") == der_of(ec_crt)
    assert _cert_presented_to(ctx, "ECDHE-RSA-AES128-GCM-SHA256") == der_of(rsa_crt)


def _bundle(tmp_path, *pairs):
    """One PEM holding each (cert, key) pair as key then certificate."""
    path = tmp_path / "bundle.pem"
    path.write_text("".join(open(k).read() + open(c).read() for c, k in pairs))
    return str(path)


def _ec_and_rsa(tmp_path):
    rsa = _self_signed(tmp_path, "rsa", ["-newkey", "rsa:2048"])
    ec = _self_signed(
        tmp_path, "ecdsa", ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"]
    )
    return ec, rsa


def test_one_pem_holding_both_key_types_serves_both(monkeypatch, reload_settings, tmp_path):
    """OpenSSL reads the first key of a file and takes every later certificate
    for chain, so without splitting the RSA pair is never served and an
    RSA-only sender fails with no shared cipher."""
    ec, rsa = _ec_and_rsa(tmp_path)
    bundle = _bundle(tmp_path, ec, rsa)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", bundle)
    reload_settings()

    ctx = controller.load_tls_context()

    def der_of(path):
        with open(path, encoding="ascii") as handle:
            return ssl.PEM_cert_to_DER_cert(handle.read())

    assert _cert_presented_to(ctx, "ECDHE-ECDSA-AES128-GCM-SHA256") == der_of(ec[0])
    assert _cert_presented_to(ctx, "ECDHE-RSA-AES128-GCM-SHA256") == der_of(rsa[0])
    assert controller.served_key_types(ctx) == ["ecdsa", "rsa"]


def test_a_single_pair_is_reported_with_the_key_type_it_serves(
    monkeypatch, reload_settings, tmp_path
):
    ec, _ = _ec_and_rsa(tmp_path)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", ec[0])
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", ec[1])
    reload_settings()
    assert controller.served_key_types(controller.load_tls_context()) == ["ecdsa"]


def test_key_types_are_not_probed_when_only_tls13_is_accepted(
    monkeypatch, reload_settings, tmp_path
):
    ec, _ = _ec_and_rsa(tmp_path)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", ec[0])
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", ec[1])
    monkeypatch.setenv("PYMTA_TLS_MIN_VERSION", "1.3")
    reload_settings()
    assert controller.served_key_types(controller.load_tls_context()) is None


def test_each_group_of_a_bundle_has_its_own_expiry_and_the_key_types_are_logged(
    monkeypatch, reload_settings, tmp_path, caplog
):
    ec, rsa = _ec_and_rsa(tmp_path)
    bundle = _bundle(tmp_path, ec, rsa)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", bundle)
    reload_settings()

    with caplog.at_level("INFO", logger="pymta.server"):
        server._log_certificates(controller.load_tls_context())

    files = [r.file for r in caplog.records if r.getMessage() == "tls_certificate"]
    assert files == [f"{bundle}[1]", f"{bundle}[2]"]
    for name in files:
        assert REGISTRY.get_sample_value(
            "pymta_tls_certificate_not_after_seconds", {"file": name}
        ) == pytest.approx(time.time() + 86400, abs=120)
    (record,) = [r for r in caplog.records if r.getMessage() == "tls_key_types"]
    assert record.served == "ecdsa,rsa"


def test_a_handshake_failure_is_named_by_openssl_s_reason(tmp_path):
    """An ECDSA-only server and an RSA-only client: the failure a sender
    without ECDSA support produces, named without the source line."""
    ec, _ = _ec_and_rsa(tmp_path)
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(*ec)
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    client_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    client_ctx.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256")
    to_server, to_client = ssl.MemoryBIO(), ssl.MemoryBIO()
    client = client_ctx.wrap_bio(to_client, to_server)
    server = server_ctx.wrap_bio(to_server, to_client, server_side=True)
    with pytest.raises(ssl.SSLWantReadError):
        client.do_handshake()
    with pytest.raises(ssl.SSLError) as failure:
        server.do_handshake()

    assert tls_failure_code(failure.value) == "no_shared_cipher"


def test_a_failure_without_a_reason_is_named_by_its_class():
    assert tls_failure_code(ConnectionResetError()) == "connection_reset_error"
    assert tls_failure_code(TimeoutError()) == "timeout_error"


def test_a_reason_attribute_on_another_exception_is_not_taken_for_openssl_s():
    class Odd(Exception):
        reason = "Something Else"

    assert tls_failure_code(Odd()) == "odd"


def test_a_bundle_named_through_a_symlink_is_still_split(monkeypatch, reload_settings, tmp_path):
    ec, rsa = _ec_and_rsa(tmp_path)
    bundle = _bundle(tmp_path, ec, rsa)
    link = tmp_path / "link.pem"
    link.symlink_to(bundle)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", str(link))
    reload_settings()
    assert controller.served_key_types(controller.load_tls_context()) == ["ecdsa", "rsa"]


def test_the_key_type_probe_uses_the_suites_the_cipher_list_leaves(
    monkeypatch, reload_settings, tmp_path
):
    """Neither probe suite is in this list, yet both certificates are served."""
    ec, rsa = _ec_and_rsa(tmp_path)
    bundle = _bundle(tmp_path, ec, rsa)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_CIPHERS", "ECDHE+CHACHA20")
    reload_settings()
    assert controller.served_key_types(controller.load_tls_context()) == ["ecdsa", "rsa"]


def test_the_expiry_series_of_a_file_that_changed_shape_are_dropped(
    monkeypatch, reload_settings, tmp_path
):
    ec, rsa = _ec_and_rsa(tmp_path)
    bundle = _bundle(tmp_path, ec, rsa)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", bundle)
    reload_settings()
    controller.publish_certificate_expiry()
    name = "pymta_tls_certificate_not_after_seconds"
    assert REGISTRY.get_sample_value(name, {"file": f"{bundle}[2]"}) is not None

    single = _bundle(tmp_path, ec)
    monkeypatch.setattr(settings, "PYMTA_TLS_CERT_PAIRS", [(single, single)])
    controller.publish_certificate_expiry()

    assert REGISTRY.get_sample_value(name, {"file": f"{bundle}[2]"}) is None
    assert REGISTRY.get_sample_value(name, {"file": single}) is not None


def test_a_bundle_with_one_key_is_loaded_as_it_is(monkeypatch, reload_settings, tmp_path):
    """The usual Postfix-style file: key then chain, in one PEM."""
    ec, _ = _ec_and_rsa(tmp_path)
    bundle = _bundle(tmp_path, ec)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", bundle)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", bundle)
    reload_settings()
    assert controller.served_key_types(controller.load_tls_context()) == ["ecdsa"]
    assert list(controller.publish_certificate_expiry()) == [bundle]


def test_an_unreadable_bundle_has_no_expiry(monkeypatch):
    monkeypatch.setattr(
        settings, "PYMTA_TLS_CERT_PAIRS", [("/nonexistent.pem", "/nonexistent.pem")]
    )
    assert controller.publish_certificate_expiry() == {"/nonexistent.pem": None}


def test_a_handshake_that_never_completes_serves_no_key_type(monkeypatch, tmp_path):
    ec, _ = _ec_and_rsa(tmp_path)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(*ec)

    def never(self):
        raise ssl.SSLWantReadError

    monkeypatch.setattr(ssl.SSLObject, "do_handshake", never)
    assert controller.served_key_types(ctx) == []


def test_log_level_typo_is_refused(monkeypatch, reload_settings):
    # Otherwise `getattr(logging, "INF0", INFO)` resolves it to INFO and the
    # operator never learns the level they asked for was not applied.
    monkeypatch.setenv("PYMTA_LOG_LEVEL", "INF0")
    with pytest.raises(ValueError, match="PYMTA_LOG_LEVEL"):
        reload_settings()


def test_log_level_is_case_insensitive(monkeypatch, reload_settings):
    monkeypatch.setenv("PYMTA_LOG_LEVEL", "debug")
    assert reload_settings().PYMTA_LOG_LEVEL == "DEBUG"


@pytest.mark.parametrize("shared", ["haproxy", "false"])
def test_proxy_protocol_prefixed_name_wins_either_way(monkeypatch, reload_settings, shared):
    # The recommended name overrides the inherited one in both directions,
    # otherwise pymta could not be run in a topology of its own during a
    # side-by-side migration.
    monkeypatch.setenv("ENABLE_PROXY_PROTOCOL", shared)
    monkeypatch.setenv("PYMTA_TRUSTED_PROXIES", "192.0.2.10")
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", "true" if shared == "false" else "false")
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is (shared == "false")


@pytest.mark.parametrize("raw", ["haproxy", "HAPROXY", " haproxy ", "true", "1", "on"])
def test_proxy_protocol_prefixed_name_takes_the_same_spellings(monkeypatch, reload_settings, raw):
    monkeypatch.delenv("ENABLE_PROXY_PROTOCOL", raising=False)
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", raw)
    monkeypatch.setenv("PYMTA_TRUSTED_PROXIES", "192.0.2.10")
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is True


# ---------------------------------------------------------------------------
# Oldest TLS version accepted.
# ---------------------------------------------------------------------------


def test_requiring_tls_without_a_certificate_is_refused(monkeypatch, reload_settings):
    """Otherwise every message would be answered 530 with no way to comply."""
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", "")
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", "")
    monkeypatch.delenv("STARTTLS_CHAIN_FILES", raising=False)
    monkeypatch.setenv("PYMTA_REQUIRE_TLS", "true")
    with pytest.raises(ValueError, match="PYMTA_REQUIRE_TLS"):
        reload_settings()


def test_requiring_tls_reaches_the_smtp_options(monkeypatch, reload_settings, tmp_path):
    crt, key = _self_signed(tmp_path, "rsa", ["-newkey", "rsa:2048"])
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", crt)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    monkeypatch.setenv("PYMTA_REQUIRE_TLS", "true")
    assert reload_settings().PYMTA_REQUIRE_TLS is True
    context = controller.load_tls_context()
    assert controller.build_smtp_kwargs(tls_context=context)["require_starttls"] is True
    assert controller.build_smtp_kwargs(tls_context=None)["require_starttls"] is False
    monkeypatch.setenv("PYMTA_REQUIRE_TLS", "false")
    reload_settings()
    assert controller.build_smtp_kwargs(tls_context=context)["require_starttls"] is False


def test_tls_min_version_typo_is_refused(monkeypatch, reload_settings):
    monkeypatch.setenv("PYMTA_TLS_MIN_VERSION", "TLSv1")
    with pytest.raises(ValueError, match="PYMTA_TLS_MIN_VERSION"):
        reload_settings()


def test_tls_ciphers_typo_is_refused(monkeypatch, reload_settings):
    monkeypatch.setenv("PYMTA_TLS_CIPHERS", "NOT-A-SUITE")
    with pytest.raises(ValueError, match="PYMTA_TLS_CIPHERS"):
        reload_settings()


@pytest.mark.parametrize("minimum", ["1.0", "1.2"])
def test_tls_ciphers_restrict_what_is_negotiated(monkeypatch, reload_settings, tmp_path, minimum):
    """A CBC-only client gets no handshake once the list is GCM only, and one
    still works without the setting."""
    ec, _ = _ec_and_rsa(tmp_path)
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", ec[0])
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", ec[1])
    monkeypatch.setenv("PYMTA_TLS_MIN_VERSION", minimum)
    reload_settings()
    open_ctx = controller.load_tls_context()
    assert _cert_presented_to(open_ctx, "ECDHE-ECDSA-AES128-SHA256")

    monkeypatch.setenv("PYMTA_TLS_CIPHERS", "ECDHE+AESGCM:ECDHE+CHACHA20")
    reload_settings()
    narrow_ctx = controller.load_tls_context()
    assert _cert_presented_to(narrow_ctx, "ECDHE-ECDSA-AES128-GCM-SHA256")
    with pytest.raises(ssl.SSLError):
        _cert_presented_to(narrow_ctx, "ECDHE-ECDSA-AES128-SHA256")


def _handshake_as_tls10(ctx) -> str | None:
    """The version negotiated with a client that only speaks TLS 1.0, or None."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)

        def serve():
            try:
                conn, _ = server.accept()
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except OSError:
                pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client.check_hostname = False
        client.verify_mode = ssl.CERT_NONE
        client.set_ciphers("DEFAULT:@SECLEVEL=0")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            client.minimum_version = ssl.TLSVersion.TLSv1
            client.maximum_version = ssl.TLSVersion.TLSv1
        try:
            with socket.create_connection(server.getsockname()) as raw:
                with client.wrap_socket(raw, server_hostname="x.invalid") as tls:
                    return tls.version()
        except ssl.SSLError:
            return None
        finally:
            thread.join(timeout=5)


@pytest.mark.parametrize(
    ("minimum", "negotiated"), [(None, "TLSv1"), ("1.2", None), ("1.0", "TLSv1")]
)
def test_tls_10_is_accepted_by_default_and_refused_from_1_2(
    monkeypatch, reload_settings, tmp_path, minimum, negotiated
):
    crt, key = _self_signed(tmp_path, "rsa", ["-newkey", "rsa:2048"])
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", crt)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    if minimum is None:
        monkeypatch.delenv("PYMTA_TLS_MIN_VERSION", raising=False)
    else:
        monkeypatch.setenv("PYMTA_TLS_MIN_VERSION", minimum)
    reload_settings()

    assert _handshake_as_tls10(controller.load_tls_context()) == negotiated


def test_tls_13_can_be_required(monkeypatch, reload_settings, tmp_path):
    crt, key = _self_signed(tmp_path, "rsa", ["-newkey", "rsa:2048"])
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", crt)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    monkeypatch.setenv("PYMTA_TLS_MIN_VERSION", "1.3")
    reload_settings()
    assert controller.load_tls_context().minimum_version == ssl.TLSVersion.TLSv1_3


# ---------------------------------------------------------------------------
# Certificate reload and expiry.
# ---------------------------------------------------------------------------


def _controller():
    return controller.Listener(object(), ip_gate=None)


def _server_events(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "pymta.server"]


def test_reload_tls_reads_the_files_again(monkeypatch, reload_settings, tmp_path):
    crt, key = _self_signed(tmp_path, "live", ["-newkey", "rsa:2048"])
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", crt)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    reload_settings()
    ctrl = _controller()
    first = ctrl._smtp_kwargs["tls_context"]

    def der_of(path):
        with open(path, encoding="ascii") as handle:
            return ssl.PEM_cert_to_DER_cert(handle.read())

    served_before = _cert_presented_to(first, "ECDHE-RSA-AES128-GCM-SHA256")
    assert served_before == der_of(crt)

    # A renewal: new certificate and key at the same paths.
    _self_signed(tmp_path, "live", ["-newkey", "rsa:2048"])
    assert der_of(crt) != served_before
    assert ctrl.reload_tls() is True

    # What a new connection is built with.
    assert ctrl.factory().tls_context is not first
    assert _cert_presented_to(ctrl.factory().tls_context, "ECDHE-RSA-AES128-GCM-SHA256") == der_of(
        crt
    )


def test_a_failed_tls_reload_keeps_the_working_context(
    monkeypatch, reload_settings, tmp_path, caplog
):
    crt, key = _self_signed(tmp_path, "live", ["-newkey", "rsa:2048"])
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", crt)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    reload_settings()
    ctrl = _controller()
    working = ctrl._smtp_kwargs["tls_context"]

    with open(crt, "w", encoding="ascii") as handle:
        handle.write("not a certificate")
    with caplog.at_level("INFO", logger="pymta.server"):
        server._reload_tls(ctrl)

    assert ctrl._smtp_kwargs["tls_context"] is working
    assert _server_events(caplog) == ["tls_reload_rejected"]


def test_tls_reload_does_nothing_without_certificates(monkeypatch, reload_settings, caplog):
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", "")
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", "")
    reload_settings()
    ctrl = _controller()
    with caplog.at_level("INFO", logger="pymta.server"):
        server._reload_tls(ctrl)
    assert ctrl._smtp_kwargs["tls_context"] is None
    assert _server_events(caplog) == []


def test_certificate_expiry_is_exported(monkeypatch, reload_settings, tmp_path, caplog):
    crt, key = _self_signed(tmp_path, "live", ["-newkey", "rsa:2048"])
    monkeypatch.setenv("PYMTA_TLS_CERT_FILE", crt)
    monkeypatch.setenv("PYMTA_TLS_KEY_FILE", key)
    reload_settings()

    with caplog.at_level("INFO", logger="pymta.server"):
        server._log_certificates()

    # _self_signed asks for one day.
    not_after = REGISTRY.get_sample_value("pymta_tls_certificate_not_after_seconds", {"file": crt})
    # A minute of slack: the certificate's clock is openssl's, read in another
    # process, and a virtual machine's clock can step between the two.
    assert 86400 - 60 < not_after - time.time() <= 86400 + 60
    (record,) = [r for r in caplog.records if r.getMessage() == "tls_certificate"]
    assert record.file == crt
    assert record.days_left in (0, 1)
    assert record.not_after.endswith("Z")


def test_an_unreadable_certificate_has_no_expiry():
    assert controller.certificate_not_after("/nonexistent.crt") is None


@pytest.mark.parametrize(("raw", "value"), [(None, "+"), ("", ""), ("-", "-"), (" + ", "+")])
def test_recipient_delimiter(monkeypatch, reload_settings, raw, value):
    if raw is None:
        monkeypatch.delenv("PYMTA_RECIPIENT_DELIMITER", raising=False)
    else:
        monkeypatch.setenv("PYMTA_RECIPIENT_DELIMITER", raw)
    assert reload_settings().PYMTA_RECIPIENT_DELIMITER == value


@pytest.mark.parametrize("raw", ["++", "@", "."])
def test_an_unusable_recipient_delimiter_is_refused(monkeypatch, reload_settings, raw):
    monkeypatch.setenv("PYMTA_RECIPIENT_DELIMITER", raw)
    with pytest.raises(ValueError, match="PYMTA_RECIPIENT_DELIMITER"):
        reload_settings()


def test_fqdn_sender_requirement_is_on_by_default(monkeypatch, reload_settings):
    monkeypatch.delenv("PYMTA_REQUIRE_FQDN_SENDER", raising=False)
    assert reload_settings().PYMTA_REQUIRE_FQDN_SENDER is True


def test_one_source_may_hold_a_tenth_of_the_slots_by_default(monkeypatch, reload_settings):
    monkeypatch.delenv("PYMTA_MAX_SHARE_PER_SOURCE", raising=False)
    assert reload_settings().PYMTA_MAX_SHARE_PER_SOURCE == 0.1


@pytest.mark.parametrize(("raw", "value"), [("0.25", 0.25), ("1", 1.0), (" .05 ", 0.05)])
def test_the_share_per_source_is_a_fraction(monkeypatch, reload_settings, raw, value):
    monkeypatch.setenv("PYMTA_MAX_SHARE_PER_SOURCE", raw)
    assert reload_settings().PYMTA_MAX_SHARE_PER_SOURCE == value


@pytest.mark.parametrize("raw", ["0", "-0.1", "1.5", "10", "nan", "half", "10%"])
def test_a_share_that_is_not_a_fraction_is_refused(monkeypatch, reload_settings, raw):
    """10 would be read as ten times everything: a percentage is not accepted."""
    monkeypatch.setenv("PYMTA_MAX_SHARE_PER_SOURCE", raw)
    with pytest.raises(ValueError, match="PYMTA_MAX_SHARE_PER_SOURCE"):
        reload_settings()
