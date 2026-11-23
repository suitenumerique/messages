"""One MTA in front of several Messages instances: routing by recipient domain.

The router is tested on its own, then through the real handler over a socket,
with one scripted MDA per tenant.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx
import jwt
import pytest

from pymta import settings
from pymta.handler import InboundHandler
from pymta.mda_async import MDAClient
from pymta.tenants import SingleTenant, TenantRouter, build_router, domain_key

from tests.harness import ScriptedMDA, connected, listening, records


class TenantMDA(ScriptedMDA):
    """A scripted MDA built by the router, as an ``MDAClient`` would be."""

    def __init__(self, *, base_url, secret, tenant):
        super().__init__()
        self.base_url, self.secret, self.tenant = base_url, secret, tenant
        self.closed = False
        self.breaker = False

    def breaker_is_open(self):
        return self.breaker

    async def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _tenant(name, *domains, secret=None):
    return {
        "base_url": f"http://backend.{name}.svc:8000/api/v1.0/",
        "secret": secret or f"secret-of-{name}-" + "x" * 32,
        "domains": list(domains),
    }


def _write(path, data) -> None:
    """Replace the file as a Kubernetes volume update does: a new inode."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data if isinstance(data, str) else json.dumps(data))
    os.replace(tmp, path)


@pytest.fixture
def tenants_file(tmp_path):
    path = tmp_path / "tenants.json"
    _write(
        path, {"a": _tenant("a", "a.example", "Lists.A.example."), "b": _tenant("b", "b.example")}
    )
    return path


def _router(path, clock=None):
    return TenantRouter(
        str(path), reload_interval=10, client_factory=TenantMDA, clock=clock or Clock()
    )


# ---------------------------------------------------------------------------
# Domains
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "key"),
    [
        ("Example.COM", "example.com"),
        ("example.com.", "example.com"),
        ("bücher.example", "xn--bcher-kva.example"),
        ("xn--bcher-kva.example", "xn--bcher-kva.example"),
        # IDNA refuses it, so it is kept as it is and matches no tenant.
        ("[192.0.2.1]", "[192.0.2.1]"),
    ],
)
def test_domain_key(given, key):
    assert domain_key(given) == key


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------


def test_routes_each_domain_to_its_tenant(tenants_file):
    router = _router(tenants_file)
    a, b = router.route("x@a.example"), router.route("y@B.EXAMPLE")
    assert (a.tenant, b.tenant) == ("a", "b")
    assert a.mda.base_url == "http://backend.a.svc:8000/api/v1.0/"
    assert a.mda.secret != b.mda.secret
    # Normalised on both sides.
    assert router.route("z@lists.a.example").mda is a.mda
    assert router.route("x@bücher.example") is None
    assert router.route("x@c.example") is None


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{", "not valid JSON"),
        ("[]", "must be a JSON object"),
        ({"a": "nope"}, "mapped to an object"),
        ({"": _tenant("a", "a.example")}, "mapped to an object"),
        ({"a": {**_tenant("a", "a.example"), "base_url": ""}}, "no base_url"),
        ({"a": {**_tenant("a", "a.example"), "secret": None}}, "no secret"),
        ({"a": {**_tenant("a", "a.example"), "domains": "a.example"}}, "list its domains"),
        ({"a": {**_tenant("a", "a.example"), "domains": [""]}}, "list its domains"),
        (
            {"a": _tenant("a", "a.example"), "b": _tenant("b", "A.example.")},
            "claimed by both 'a' and 'b'",
        ),
    ],
)
def test_a_bad_file_refuses_to_start(tmp_path, content, error):
    path = tmp_path / "tenants.json"
    _write(path, content)
    with pytest.raises(ValueError, match=error):
        _router(path)


def test_a_missing_file_refuses_to_start(tmp_path):
    with pytest.raises(FileNotFoundError):
        _router(tmp_path / "absent.json")


# ---------------------------------------------------------------------------
# Reloading
# ---------------------------------------------------------------------------


def test_the_file_is_looked_at_once_per_interval(tenants_file):
    clock = Clock()
    router = _router(tenants_file, clock)
    router.route("x@a.example")  # Arms the next look at t=10.
    _write(tenants_file, {"c": _tenant("c", "c.example")})
    clock.now = 9
    assert router.route("x@c.example") is None
    clock.now = 10
    assert router.route("x@c.example").tenant == "c"


@pytest.mark.asyncio
async def test_a_reload_keeps_unchanged_clients_and_retires_the_others(tenants_file):
    clock = Clock()
    router = _router(tenants_file, clock)
    a = router.route("x@a.example").mda
    b = router.route("x@b.example").mda
    router.route("x@a.example")

    # Same file: nothing is rebuilt.
    clock.now = 10
    assert router.route("x@a.example").mda is a

    # a unchanged, b's secret rotated, c new: b gets a new client.
    _write(
        tenants_file,
        {
            "a": _tenant("a", "a.example", "lists.a.example"),
            "b": _tenant("b", "b.example", secret="rotated-" + "y" * 32),
            "c": _tenant("c", "c.example"),
        },
    )
    clock.now = 20
    assert router.route("x@a.example").mda is a
    new_b = router.route("x@b.example").mda
    assert new_b is not b
    assert new_b.secret.startswith("rotated-")

    # c removed.
    c = router.route("x@c.example").mda
    _write(tenants_file, {"a": _tenant("a", "a.example"), "b": _tenant("b", "b.example")})
    clock.now = 30
    assert router.route("x@c.example") is None

    # Retired clients may still have a call in flight: closed at shutdown.
    assert not (b.closed or c.closed)
    await router.close()
    assert a.closed and new_b.closed and b.closed and c.closed


@pytest.mark.parametrize("breakage", ["bad_json", "missing"])
def test_a_failed_reload_keeps_the_previous_mapping(tenants_file, caplog, breakage):
    clock = Clock()
    router = _router(tenants_file, clock)
    if breakage == "bad_json":
        _write(tenants_file, "{")
    else:
        tenants_file.unlink()
    clock.now = 10
    with caplog.at_level(logging.ERROR, logger="pymta"):
        assert router.route("x@a.example").tenant == "a"
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == ["tenants_reload_failed"]


@pytest.mark.asyncio
async def test_the_breaker_gauge_reads_open_while_any_tenant_is_shed(tenants_file):
    router = _router(tenants_file)
    await router.start()
    assert router.breaker_is_open() is False
    router.route("x@b.example").mda.breaker = True
    assert router.breaker_is_open() is True


# ---------------------------------------------------------------------------
# Which router the settings ask for
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_a_tenants_file_everything_goes_to_the_one_mda(monkeypatch):
    monkeypatch.setattr(settings, "PYMTA_TENANTS_FILE", "")
    router = build_router()
    assert isinstance(router, SingleTenant)
    route = router.route("anyone@anywhere.example")
    assert route.tenant == ""
    assert route.mda.base_url == settings.MDA_API_BASE_URL.rstrip("/") + "/"
    await router.start()
    assert router.breaker_is_open() is False
    await router.close()


def test_with_a_tenants_file_recipients_are_routed(monkeypatch, tenants_file):
    monkeypatch.setattr(settings, "PYMTA_TENANTS_FILE", str(tenants_file))
    router = build_router()
    assert isinstance(router, TenantRouter)
    route = router.route("x@b.example")
    assert route.tenant == "b"
    assert isinstance(route.mda, MDAClient)
    assert route.mda.base_url == "http://backend.b.svc:8000/api/v1.0/"
    assert route.mda.secret == _tenant("b")["secret"]
    assert route.mda.tenant == "b"


@pytest.mark.asyncio
async def test_each_tenant_signs_its_calls_with_its_own_secret(tenants_file):
    router = TenantRouter(str(tenants_file))
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        (address,) = json.loads(request.content)["addresses"]
        return httpx.Response(200, json={address: True})

    for name in ("a", "b"):
        mda = router.route(f"x@{name}.example").mda
        mda._client = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        assert (await mda.check_recipient(f"x@{name}.example")).ok

    for request, (own, other) in zip(requests, [("a", "b"), ("b", "a")], strict=True):
        assert request.url == f"http://backend.{own}.svc:8000/api/v1.0/inbound/mta/check/"
        token = request.headers["Authorization"].removeprefix("Bearer ")
        jwt.decode(token, _tenant(own)["secret"], algorithms=["HS256"])
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, _tenant(other)["secret"], algorithms=["HS256"])
    await router.close()


# ---------------------------------------------------------------------------
# Over SMTP
# ---------------------------------------------------------------------------


async def _send(client, rcpts, body=b"Subject: hi\r\n\r\nbody\r\n"):
    """One transaction. Returns the RCPT replies and the final reply."""
    await client.cmd(b"EHLO mx.sender.example")
    await client.cmd(b"MAIL FROM:<sender@example.org>")
    replies = [await client.cmd(b"RCPT TO:<" + rcpt + b">") for rcpt in rcpts]
    return replies, await client.data(body)


@pytest.fixture
def routed(tenants_file):
    router = _router(tenants_file)
    mdas = {name: router.route(f"x@{name}.example").mda for name in ("a", "b")}
    return InboundHandler(router=router), mdas


@pytest.mark.asyncio
async def test_each_recipient_is_checked_with_its_own_tenant(routed):
    handler, mdas = routed
    async with listening(handler=handler) as port, connected(port) as client:
        replies, _ = await _send(client, [b"one@a.example", b"two@b.example"])
    assert all(r.startswith(b"250") for r in replies), replies
    assert mdas["a"].checked == ["one@a.example"]
    assert mdas["b"].checked == ["two@b.example"]


@pytest.mark.asyncio
async def test_a_domain_no_tenant_serves_is_an_unknown_recipient(monkeypatch, routed, caplog):
    monkeypatch.setattr(settings, "PYMTA_MAX_RCPT_MISSES_PER_SESSION", 2)
    handler, mdas = routed
    with caplog.at_level(logging.INFO, logger="pymta"):
        async with listening(handler=handler) as port, connected(port) as client:
            await client.cmd(b"EHLO mx.sender.example")
            await client.cmd(b"MAIL FROM:<sender@example.org>")
            first = await client.cmd(b"RCPT TO:<x@c.example>")
            # It spends the miss budget like any unknown mailbox.
            second = await client.cmd(b"RCPT TO:<y@c.example>")
            # The command after it is the one answered 421, unasked of the MDA.
            third = await client.cmd(b"RCPT TO:<z@a.example>")
            assert await client.closed()
    assert first == second == b"550 5.1.1 No such recipient\r\n"
    assert third.startswith(b"421 4.7.0 Too many unknown recipients")
    assert mdas["a"].checked == mdas["b"].checked == []
    rejects = [r for r in await records(caplog, "reject") if r.reason == "unknown_domain"]
    assert [(r.recipient, r.misses) for r in rejects] == [("x@c.example", 1), ("y@c.example", 2)]


@pytest.mark.asyncio
async def test_a_message_is_delivered_once_per_tenant_with_its_own_recipients(routed):
    handler, mdas = routed
    async with listening(handler=handler) as port, connected(port) as client:
        _, reply = await _send(
            client, [b"one@a.example", b"two@b.example", b"three@lists.a.example"]
        )
    assert reply == b"250 2.0.0 Message accepted for delivery\r\n"
    assert [d["original_recipients"] for d in mdas["a"].delivered] == [
        ["one@a.example", "three@lists.a.example"]
    ]
    assert [d["original_recipients"] for d in mdas["b"].delivered] == [["two@b.example"]]
    assert mdas["a"].delivered[0]["message"] == mdas["b"].delivered[0]["message"]


@pytest.mark.parametrize(
    ("answers", "reply", "outcome"),
    [
        # One tenant has it, the other will later: the sender retries all.
        (("ok", "temp"), b"451 4.3.0 Delivery temporarily unavailable\r\n", "deferred"),
        # One tenant took it: not a bounce, even if the other refuses for good.
        (("ok", "perm"), b"451 4.3.0 Delivery temporarily unavailable\r\n", "deferred"),
        (("perm", "perm"), b"554 5.6.0 Message rejected by delivery agent\r\n", "rejected"),
    ],
)
@pytest.mark.asyncio
async def test_the_reply_covers_every_tenant(routed, caplog, answers, reply, outcome):
    handler, mdas = routed
    mdas["a"].delivers, mdas["b"].delivers = [answers[0]], [answers[1]]
    with caplog.at_level(logging.INFO, logger="pymta"):
        async with listening(handler=handler) as port, connected(port) as client:
            _, got = await _send(client, [b"one@a.example", b"two@b.example"])
        (line,) = await records(caplog, "message_" + outcome)
    assert got == reply
    # The line names the first tenant that did not take it.
    failed = "a" if answers[0] != "ok" else "b"
    assert line.tenant == failed


@pytest.mark.asyncio
async def test_a_slow_tenant_gets_the_sender_a_451_although_another_tenant_took_the_message(
    monkeypatch, routed
):
    monkeypatch.setattr(settings, "PYMTA_DATA_TIMEOUT", settings.PYMTA_REPLY_RESERVE_SECONDS + 1)
    handler, mdas = routed

    async def stall(**_):
        await asyncio.sleep(30)

    mdas["b"].delivers = [stall]
    async with listening(handler=handler) as port, connected(port) as client:
        _, reply = await _send(client, [b"slow@b.example", b"second@a.example"])
    (call,) = mdas["a"].delivered
    assert call["original_recipients"] == ["second@a.example"]
    assert reply.startswith(b"451 4.3.0 Delivery timed out"), reply


@pytest.mark.asyncio
async def test_accepted_recipient_still_delivered_on_removed_tenant(tenants_file):
    clock = Clock()
    router = _router(tenants_file, clock)
    old_b = router.route("x@b.example").mda
    async with listening(handler=InboundHandler(router=router)) as port, connected(port) as client:
        await client.cmd(b"EHLO mx.sender.example")
        await client.cmd(b"MAIL FROM:<sender@example.org>")
        assert (await client.cmd(b"RCPT TO:<one@b.example>")).startswith(b"250")
        _write(tenants_file, {"a": _tenant("a", "a.example")})
        clock.now = 10
        assert (await client.cmd(b"RCPT TO:<two@a.example>")).startswith(b"250")
        assert router.route("x@b.example") is None
        reply = await client.data()
    assert reply == b"250 2.0.0 Message accepted for delivery\r\n"
    assert [d["original_recipients"] for d in old_b.delivered] == [["one@b.example"]]
    a = router.route("x@a.example").mda
    assert [d["original_recipients"] for d in a.delivered] == [["two@a.example"]]
