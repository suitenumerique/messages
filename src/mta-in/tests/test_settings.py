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

import pytest

from pymta import controller, settings
from pymta.settings import _env_bool, _env_int, _env_str, _env_token


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


@pytest.mark.parametrize("raw", ["\r\n220 you are welcome here", "a\tb"])
def test_token_rejects_control_characters(monkeypatch, raw):
    # These land in the "220 {hostname} {ident}" banner; a CR/LF would append
    # attacker-chosen lines to our own greeting, and a TAB folds if the value
    # reaches a Received header. (NUL is also rejected but cannot be tested
    # through the environment: putenv refuses to store one.)
    monkeypatch.setenv("PYMTA_TEST_TOKEN", raw)
    with pytest.raises(ValueError, match="control characters"):
        _env_token("PYMTA_TEST_TOKEN", "mta-in")


def test_token_checks_the_default_too(monkeypatch):
    # PYMTA_SMTP_HOSTNAME defaults to $MYHOSTNAME, which on k8s can come from the
    # downward API, so the fallback value needs the same check as the direct one.
    monkeypatch.delenv("PYMTA_TEST_TOKEN", raising=False)
    with pytest.raises(ValueError, match="control characters"):
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
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is True


@pytest.mark.parametrize("raw", ["", "false", "off", "no", "0"])
def test_proxy_protocol_off_spellings(monkeypatch, reload_settings, raw):
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", raw)
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is False


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
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", "true" if shared == "false" else "false")
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is (shared == "false")


@pytest.mark.parametrize("raw", ["haproxy", "HAPROXY", " haproxy ", "true", "1", "on"])
def test_proxy_protocol_prefixed_name_takes_the_same_spellings(monkeypatch, reload_settings, raw):
    monkeypatch.delenv("ENABLE_PROXY_PROTOCOL", raising=False)
    monkeypatch.setenv("PYMTA_ENABLE_PROXY_PROTOCOL", raw)
    assert reload_settings().PYMTA_ENABLE_PROXY_PROTOCOL is True
