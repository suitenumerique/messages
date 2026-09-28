"""Access scoping and outbound-request hardening.

Endpoints that acted on a caller-supplied id, host, URL or port without
checking the caller was entitled to it.
"""

import socket
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import reverse

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from core import enums, factories
from core.api.permissions import IsAllowedToAccess
from core.mda.outbound import send_outbound_email
from core.services.ssrf import SSRFSafeSession

# Smallest valid GIF; python-magic must sniff it as image/gif.
_GIF_BYTES = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
    b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
    b"\x00\x00\x02\x02D\x01\x00;"
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    """Throttle state lives in the shared cache; isolate each test."""
    cache.clear()
    yield


def _client(user):
    client = APIClient()
    client.force_login(user)
    return client


class TestMessageListMailboxScope:
    """``mailbox_id`` selects whose read state is annotated: check access."""

    def test_foreign_mailbox_id_is_refused(self):
        """A mailbox the caller has no access to must not be annotatable."""
        user = factories.UserFactory()
        own_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(user=user, mailbox=own_mailbox)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=own_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        factories.MessageFactory(thread=thread)

        other_mailbox = factories.MailboxFactory()
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=other_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        response = _client(user).get(
            reverse("messages-list"),
            {"thread_id": str(thread.id), "mailbox_id": str(other_mailbox.id)},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_own_mailbox_id_is_allowed(self):
        """The ordinary case still works."""
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(user=user, mailbox=mailbox)
        thread = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread, mailbox=mailbox, role=enums.ThreadAccessRoleChoices.EDITOR
        )
        factories.MessageFactory(thread=thread)

        response = _client(user).get(
            reverse("messages-list"),
            {"thread_id": str(thread.id), "mailbox_id": str(mailbox.id)},
        )

        assert response.status_code == status.HTTP_200_OK


class TestImageProxyRestrictions:
    """The proxy only reaches web ports and is rate-limited per user."""

    PUBLIC_IP = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    @staticmethod
    def _setup():
        user = factories.UserFactory()
        mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(user=user, mailbox=mailbox)
        url = reverse("image-proxy-list", kwargs={"mailbox_id": mailbox.id})
        return _client(user), url

    def test_percent_encoded_url_is_fetched_as_is(self, settings):
        """A literal `%XX` in the URL is not decoded a second time."""
        settings.IMAGE_PROXY_ENABLED = True
        client, url = self._setup()
        tracked = "https://cdn.example.com/o.gif?u=abc%3D%3D"

        with patch("core.api.viewsets.image_proxy.SSRFSafeSession") as mock_session:
            fetched = mock_session.return_value.get.return_value
            fetched.headers = {"Content-Type": "image/gif"}
            fetched.iter_content.return_value = iter([_GIF_BYTES])
            response = client.get(url, {"url": tracked})

        assert response.status_code == status.HTTP_200_OK
        assert mock_session.return_value.get.call_args[0][0] == tracked

    @pytest.mark.parametrize(
        "image_url",
        ["https://cdn.example.com:22/x.png", "http://cdn.example.com:8080/x.png"],
    )
    def test_non_web_port_is_refused(self, image_url, settings):
        """Other ports are refused before any lookup or connection."""
        settings.IMAGE_PROXY_ENABLED = True
        client, url = self._setup()

        with (
            patch("core.services.ssrf.socket.getaddrinfo") as mock_resolve,
            patch("requests.Session.get") as mock_get,
        ):
            response = client.get(url, {"url": image_url})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        mock_resolve.assert_not_called()
        mock_get.assert_not_called()

    def test_redirect_to_non_web_port_is_refused(self, settings):
        """The port check also applies to redirect targets."""
        settings.IMAGE_PROXY_ENABLED = True
        client, url = self._setup()
        redirect = SimpleNamespace(
            status_code=302,
            headers={"Location": "https://cdn.example.com:8443/x.png"},
            close=lambda: None,
        )

        with (
            patch("core.services.ssrf.socket.getaddrinfo", return_value=self.PUBLIC_IP),
            patch("requests.Session.get", return_value=redirect) as mock_get,
        ):
            response = client.get(url, {"url": "https://cdn.example.com/x.png"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert mock_get.call_count == 1

    def test_other_callers_keep_any_port(self):
        """Without allowed_ports (webhooks, push), any port is accepted."""
        with patch(
            "core.services.ssrf.socket.getaddrinfo", return_value=self.PUBLIC_IP
        ):
            port = SSRFSafeSession()._validate_and_unpack(  # pylint: disable=protected-access
                "https://hooks.example.com:8443/in"
            )[3]
        assert port == 8443

    def test_requests_are_throttled_per_user(self, settings):
        """Past the rate, the proxy answers 429."""
        settings.IMAGE_PROXY_ENABLED = False
        client, url = self._setup()
        other_client, other_url = self._setup()

        with patch.object(ScopedRateThrottle, "get_rate", return_value="2/minute"):
            statuses = [client.get(url, {"url": "x"}).status_code for _ in range(3)]
            other_status = other_client.get(other_url, {"url": "x"}).status_code

        assert statuses[-1] == status.HTTP_429_TOO_MANY_REQUESTS
        assert status.HTTP_429_TOO_MANY_REQUESTS not in statuses[:2]
        assert other_status != status.HTTP_429_TOO_MANY_REQUESTS


class TestMailDomainCustomSettings:
    """``custom_settings`` reaches the outbound relay, so it is validated."""

    def test_unknown_keys_are_accepted(self):
        """The schema is open so existing rows with other keys still save."""
        domain = factories.MailDomainFactory()
        domain.custom_settings = {"SOME_OTHER_KEY": "value"}
        domain.save()

    @pytest.mark.parametrize(
        "custom_settings",
        [
            {"MTA_OUT_RELAY_HOST": "not a host!"},
            {"MTA_OUT_RELAY_HOST": "relay.example.com:25\r\nX"},
            {"MTA_OUT_MODE": "smtp"},
            {"MTA_OUT_RELAY_PASSWORD": 1234},
            [],
        ],
    )
    def test_known_keys_are_validated(self, custom_settings):
        """Known keys must have the expected shape."""
        domain = factories.MailDomainFactory()
        domain.custom_settings = custom_settings

        with pytest.raises(ValidationError) as excinfo:
            domain.save()

        assert "custom_settings" in excinfo.value.message_dict

    def test_valid_settings_are_accepted(self):
        """The supported keys still round-trip."""
        domain = factories.MailDomainFactory()
        domain.custom_settings = {
            "MTA_OUT_MODE": "relay",
            "MTA_OUT_RELAY_HOST": "relay.example.com:587",
            "MTA_OUT_RELAY_USERNAME": "user",
            "MTA_OUT_RELAY_PASSWORD": "pass",
        }
        domain.save()

        domain.refresh_from_db()
        assert domain.custom_settings["MTA_OUT_RELAY_HOST"] == "relay.example.com:587"


class TestThreadRosterAssignability:
    """ "Who I can see" must say whether I can also assign them."""

    def test_viewer_is_listed_but_not_assignable(self):
        """A read-only participant is mentionable, not assignable."""
        editor_user = factories.UserFactory()
        viewer_user = factories.UserFactory()
        thread = factories.ThreadFactory()

        editor_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            user=editor_user,
            mailbox=editor_mailbox,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=editor_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        viewer_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            user=viewer_user,
            mailbox=viewer_mailbox,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=viewer_mailbox,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )

        response = _client(editor_user).get(
            reverse("thread-user-list", kwargs={"thread_id": thread.id})
        )

        assert response.status_code == status.HTTP_200_OK
        by_id = {row["id"]: row for row in response.json()}
        assert by_id[str(editor_user.id)]["can_be_assigned"] is True
        assert by_id[str(viewer_user.id)]["can_be_assigned"] is False

    def test_rights_split_across_mailboxes_are_not_combined(self):
        """Mailbox edit role and EDITOR thread access must come from one mailbox."""
        editor_user = factories.UserFactory()
        split_user = factories.UserFactory()
        thread = factories.ThreadFactory()

        # EDITOR on the thread, but split_user is only a mailbox viewer here.
        editor_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            user=editor_user,
            mailbox=editor_mailbox,
            role=enums.MailboxRoleChoices.ADMIN,
        )
        factories.MailboxAccessFactory(
            user=split_user,
            mailbox=editor_mailbox,
            role=enums.MailboxRoleChoices.VIEWER,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=editor_mailbox,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        # split_user can edit this mailbox, but it only views the thread.
        viewer_mailbox = factories.MailboxFactory()
        factories.MailboxAccessFactory(
            user=split_user,
            mailbox=viewer_mailbox,
            role=enums.MailboxRoleChoices.EDITOR,
        )
        factories.ThreadAccessFactory(
            thread=thread,
            mailbox=viewer_mailbox,
            role=enums.ThreadAccessRoleChoices.VIEWER,
        )

        response = _client(editor_user).get(
            reverse("thread-user-list", kwargs={"thread_id": thread.id})
        )

        assert response.status_code == status.HTTP_200_OK
        by_id = {row["id"]: row for row in response.json()}
        assert by_id[str(editor_user.id)]["can_be_assigned"] is True
        assert by_id[str(split_user.id)]["can_be_assigned"] is False


class TestImapPortAllowlist:
    """An arbitrary destination port would make the import a port scanner."""

    @staticmethod
    def _is_valid(port):
        from core.api import serializers  # pylint: disable=import-outside-toplevel

        serializer = serializers.ImportCreateSerializer(
            data={
                "source": "imap",
                "imap_server": "imap.example.com",
                "imap_port": port,
                "username": "user",
                "password": "pass",
            }
        )
        valid = serializer.is_valid()
        assert valid or "imap_port" in serializer.errors
        return valid

    def test_non_allowlisted_port_is_refused(self):
        """Only the allowlisted IMAP ports are accepted by default."""
        assert self._is_valid(993)
        assert not self._is_valid(22)

    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_empty_allowlist_still_requires_a_real_port(self, port, settings):
        """An empty allowlist allows any port, but not out-of-range ones."""
        settings.MESSAGES_IMPORT_IMAP_ALLOWED_PORTS = []

        assert self._is_valid(1143)
        assert not self._is_valid(port)


class TestIsAllowedToAccessDefault:
    """Actions without an object to check are denied unless they opt in."""

    @staticmethod
    def _has_permission(**view_attrs):
        request = SimpleNamespace(
            user=factories.UserFactory(), auth=None, query_params={}
        )
        view = SimpleNamespace(kwargs={}, action="custom", **view_attrs)
        return IsAllowedToAccess().has_permission(request, view)

    def test_collection_action_is_denied_by_default(self):
        """A non-list, non-detail action gets no free pass."""
        assert self._has_permission(detail=False) is False

    def test_detail_action_defers_to_object_permission(self):
        """Detail actions are checked later by has_object_permission."""
        assert self._has_permission(detail=True) is True

    def test_view_can_opt_in(self):
        """A view doing its own check declares it."""
        assert self._has_permission(checks_access_in_view=True) is True


class TestThreadSearchMailboxScope:
    """Search results must be restricted to the requested mailbox."""

    def test_thread_reached_through_another_mailbox_is_not_returned(self, settings):
        """A stale index hit must not surface a thread from another mailbox."""
        settings.OPENSEARCH_HOSTS = ["http://opensearch:9200"]
        user = factories.UserFactory()
        mailbox_a = factories.MailboxFactory(users_read=[user])
        mailbox_b = factories.MailboxFactory(users_read=[user])
        thread_a = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread_a,
            mailbox=mailbox_a,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )
        thread_b = factories.ThreadFactory()
        factories.ThreadAccessFactory(
            thread=thread_b,
            mailbox=mailbox_b,
            role=enums.ThreadAccessRoleChoices.EDITOR,
        )

        with patch("core.api.viewsets.thread.search_threads") as mock_search:
            mock_search.return_value = {
                "threads": [{"id": str(thread_a.id)}, {"id": str(thread_b.id)}],
                "total": 2,
            }
            response = _client(user).get(
                reverse("threads-list"),
                {"search": "query", "mailbox_id": str(mailbox_a.id)},
            )

        assert response.status_code == status.HTTP_200_OK
        result_ids = {row["id"] for row in response.json()["results"]}
        assert result_ids == {str(thread_a.id)}


class TestPerDomainRelaySSRF:
    """A relay from custom_settings is SSRF-checked and pinned to its IP."""

    RECIPIENTS = {"to@example.com"}

    @staticmethod
    def _resolve_to(ip):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def _send(self, custom_settings):
        return send_outbound_email(
            self.RECIPIENTS, "sender@example.com", b"raw", custom_settings
        )

    def test_private_relay_is_refused_without_retry(self):
        """A per-domain relay resolving to a private IP is never dialed.

        It is a configuration error, so recipients fail for good instead of
        going through the whole retry schedule.
        """
        with (
            patch(
                "core.services.ssrf.socket.getaddrinfo",
                return_value=self._resolve_to("10.0.0.5"),
            ),
            patch("core.mda.outbound.send_smtp_mail") as mock_send,
        ):
            statuses = self._send(
                {"MTA_OUT_MODE": "relay", "MTA_OUT_RELAY_HOST": "smtp.internal:25"}
            )

        mock_send.assert_not_called()
        assert statuses == {
            "to@example.com": {
                "delivered": False,
                "error": "The outbound relay of this domain is not allowed",
                "retry": False,
            }
        }

    def test_unresolvable_relay_is_retried(self):
        """A DNS failure may be transient: recipients are left for retry."""
        with (
            patch(
                "core.services.ssrf.socket.getaddrinfo",
                side_effect=socket.gaierror("temporary failure"),
            ),
            patch("core.mda.outbound.send_smtp_mail") as mock_send,
        ):
            statuses = self._send(
                {"MTA_OUT_MODE": "relay", "MTA_OUT_RELAY_HOST": "relay.example.com"}
            )

        mock_send.assert_not_called()
        assert statuses == {
            "to@example.com": {
                "delivered": False,
                "error": "Unable to resolve the outbound relay of this domain",
                "retry": True,
            }
        }

    def test_public_relay_is_pinned_to_the_validated_ip(self):
        """The connection goes to the IP that was checked, not a new lookup."""
        with (
            patch(
                "core.services.ssrf.socket.getaddrinfo",
                return_value=self._resolve_to("93.184.216.34"),
            ),
            patch("core.mda.outbound.send_smtp_mail", return_value={}) as mock_send,
        ):
            self._send(
                {"MTA_OUT_MODE": "relay", "MTA_OUT_RELAY_HOST": "relay.example.com"}
            )

        kwargs = mock_send.call_args.kwargs
        assert kwargs["smtp_host"] == "relay.example.com"
        assert kwargs["smtp_ip"] == "93.184.216.34"
        assert kwargs["smtp_port"] == 587

    def test_relay_prefers_ipv4(self):
        """Like direct MX delivery, IPv4 is dialed when the relay has both."""
        resolved = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1111", 0, 0, 0)),
            *self._resolve_to("93.184.216.34"),
        ]
        with (
            patch("core.services.ssrf.socket.getaddrinfo", return_value=resolved),
            patch("core.mda.outbound.send_smtp_mail", return_value={}) as mock_send,
        ):
            self._send(
                {"MTA_OUT_MODE": "relay", "MTA_OUT_RELAY_HOST": "relay.example.com"}
            )

        assert mock_send.call_args.kwargs["smtp_ip"] == "93.184.216.34"

    def test_allowlisted_internal_relay_is_accepted(self, settings):
        """SSRF_ALLOWED_HOSTS lets an operator use an internal smarthost."""
        settings.SSRF_ALLOWED_HOSTS = ["smtp.internal"]
        with (
            patch(
                "core.services.ssrf.socket.getaddrinfo",
                return_value=self._resolve_to("10.0.0.5"),
            ),
            patch("core.mda.outbound.send_smtp_mail", return_value={}) as mock_send,
        ):
            self._send(
                {"MTA_OUT_MODE": "relay", "MTA_OUT_RELAY_HOST": "smtp.internal:25"}
            )

        assert mock_send.call_args.kwargs["smtp_ip"] == "10.0.0.5"

    def test_deployment_relay_is_trusted(self, settings):
        """The relay from the deployment settings is not SSRF-checked."""
        settings.MTA_OUT_RELAY_HOST = "mta-out:25"
        with (
            patch("core.services.ssrf.socket.getaddrinfo") as mock_resolve,
            patch("core.mda.outbound.send_smtp_mail", return_value={}) as mock_send,
        ):
            self._send({"MTA_OUT_MODE": "relay"})

        mock_resolve.assert_not_called()
        assert mock_send.call_args.kwargs["smtp_host"] == "mta-out"
        assert mock_send.call_args.kwargs["smtp_ip"] is None
