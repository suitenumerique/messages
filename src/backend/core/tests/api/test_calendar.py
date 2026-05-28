"""Tests for calendar API views using a real in-process Radicale CalDAV server."""
# pylint: disable=redefined-outer-name, unused-argument, protected-access, missing-function-docstring, too-many-lines

import shutil
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
import radicale.app
import radicale.config
import requests
import requests.adapters
from icalendar import Calendar as ICalendar

from core import factories
from core.enums import ChannelTypes, MailboxRoleChoices
from core.services.calendar.ics_rebuild import rebuild_for_storage
from core.services.calendar.service import CalDAVError, CalDAVService


class _SilentHandler(WSGIRequestHandler):
    """Suppress Radicale request logs during tests."""

    def log_message(self, format, *args):  # pylint: disable=redefined-builtin
        pass


@pytest.fixture()
def radicale_server():
    """Start a real Radicale CalDAV server in a background thread."""
    tmpdir = tempfile.mkdtemp()
    configuration = radicale.config.load()
    configuration.update(
        {
            "storage": {
                "filesystem_folder": tmpdir,
                "type": "multifilesystem_nolock",
            },
            "auth": {"type": "none"},
        },
        "test",
    )

    app = radicale.app.Application(configuration)
    server = make_server("localhost", 0, app, handler_class=_SilentHandler)
    port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    yield f"http://localhost:{port}"

    server.shutdown()
    thread.join(timeout=5)
    shutil.rmtree(tmpdir, ignore_errors=True)


RADICALE_USER = "testuser"
RADICALE_PASSWORD = "testpass"


def _mkcalendar(url, display_name, auth):
    """Create a calendar on a CalDAV server via MKCALENDAR."""
    body = (
        '<?xml version="1.0"?>'
        '<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
        "<d:set><d:prop>"
        f"<d:displayname>{display_name}</d:displayname>"
        "</d:prop></d:set>"
        "</c:mkcalendar>"
    )
    resp = requests.request(
        "MKCALENDAR",
        url,
        data=body.encode("utf-8"),
        auth=auth,
        headers={"Content-Type": "application/xml; charset=utf-8"},
        timeout=5,
    )
    resp.raise_for_status()


def _put_event(calendar_url, uid, ics, auth):
    resp = requests.put(
        calendar_url.rstrip("/") + f"/{uid}.ics",
        data=ics.encode("utf-8"),
        auth=auth,
        headers={"Content-Type": "text/calendar; charset=utf-8"},
        timeout=5,
    )
    resp.raise_for_status()


@pytest.fixture()
def radicale_with_calendar(radicale_server):
    """Create a default calendar on the Radicale server.

    Returns (calendar_url, put_event_callable).
    """
    calendar_url = f"{radicale_server}/{RADICALE_USER}/test-cal/"
    _mkcalendar(
        calendar_url,
        "Test Calendar",
        auth=(RADICALE_USER, RADICALE_PASSWORD),
    )

    def put_event(uid, ics):
        _put_event(calendar_url, uid, ics, auth=(RADICALE_USER, RADICALE_PASSWORD))

    return calendar_url, put_event


@pytest.fixture()
def caldav_channel(radicale_server, mailbox):
    """Create a Channel of type caldav pointing at the Radicale server.

    Credentials live in ``encrypted_settings`` (never in plain ``settings``)
    so a DB dump cannot surface them.
    """
    return factories.ChannelFactory(
        mailbox=mailbox,
        type=ChannelTypes.CALDAV,
        settings={
            "url": f"{radicale_server}/{RADICALE_USER}/",
        },
        encrypted_settings={
            "username": RADICALE_USER,
            "password": RADICALE_PASSWORD,
        },
    )


@pytest.fixture(autouse=True)
def _bypass_caldav_ssrf(request):
    """Bypass the per-channel SSRF guard for tests.

    Production ``from_channel`` calls ``_build_ssrf_adapter`` which
    rejects loopback / private IPs — but the whole test suite targets a
    localhost Radicale, which production correctly refuses. Replace the
    adapter builder with a plain ``HTTPAdapter`` for the duration of
    each test so the channel-flavored path can reach the test server.

    Opt out by adding the ``caldav_ssrf_real`` marker to a test that
    needs to verify the production guard fires.
    """
    if "caldav_ssrf_real" in request.keywords:
        yield
        return
    with mock.patch.object(
        CalDAVService,
        "_build_ssrf_adapter",
        lambda self: requests.adapters.HTTPAdapter(),
    ):
        yield


@pytest.fixture()
def user_with_mailbox(mailbox):
    """Create a user with access to the mailbox."""
    user = factories.UserFactory()
    factories.MailboxAccessFactory(
        mailbox=mailbox,
        user=user,
        role=MailboxRoleChoices.ADMIN,
    )
    return user


SAMPLE_ICS = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
METHOD:REQUEST
BEGIN:VEVENT
UID:test-event-001@example.com
DTSTART:{dtstart}
DTEND:{dtend}
SUMMARY:Team Meeting
ORGANIZER:mailto:organizer@example.com
ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{attendee}
END:VEVENT
END:VCALENDAR"""


def _make_ics(mailbox, dtstart=None, dtend=None):
    now = datetime.now(tz=timezone.utc)
    dtstart = dtstart or now + timedelta(hours=1)
    dtend = dtend or dtstart + timedelta(hours=1)
    return SAMPLE_ICS.format(
        dtstart=dtstart.strftime("%Y%m%dT%H%M%SZ"),
        dtend=dtend.strftime("%Y%m%dT%H%M%SZ"),
        attendee=str(mailbox),
    )


# ---------------------------------------------------------------------------
# Permission tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCalendarPermissions:
    """Verify that calendar endpoints enforce mailbox access."""

    def test_anonymous_cannot_access(self, api_client, mailbox, caldav_channel):
        """Anonymous users are rejected."""
        base = f"/api/v1.0/mailboxes/{mailbox.id}/calendar"
        assert api_client.get(f"{base}/calendars/").status_code == 401
        assert api_client.post(f"{base}/conflicts/", {}).status_code == 401
        assert api_client.post(f"{base}/rsvp/", {}).status_code == 401
        assert api_client.post(f"{base}/add/", {}).status_code == 401

    def test_user_without_access_is_forbidden(
        self, api_client, mailbox, caldav_channel, other_user
    ):
        """Authenticated user without MailboxAccess is rejected."""
        api_client.force_authenticate(user=other_user)
        base = f"/api/v1.0/mailboxes/{mailbox.id}/calendar"
        assert api_client.get(f"{base}/calendars/").status_code == 403
        assert api_client.post(f"{base}/conflicts/", {}).status_code == 403
        assert api_client.post(f"{base}/rsvp/", {}).status_code == 403
        assert api_client.post(f"{base}/add/", {}).status_code == 403

    def test_user_with_access_is_allowed(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """Authenticated user with MailboxAccess can reach the endpoints."""
        api_client.force_authenticate(user=user_with_mailbox)
        base = f"/api/v1.0/mailboxes/{mailbox.id}/calendar"

        # calendars list should succeed
        resp = api_client.get(f"{base}/calendars/")
        assert resp.status_code == 200

    def test_viewer_cannot_write(self, api_client, mailbox, caldav_channel):
        """VIEWER-only access can read but not RSVP or add events.

        Writing into the mailbox's CalDAV calendar (RSVP / Add) produces
        outbound iTIP traffic on the user's behalf. A user granted
        read-only access to the mailbox must not be able to do that.
        """
        viewer = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=viewer,
            role=MailboxRoleChoices.VIEWER,
        )
        api_client.force_authenticate(user=viewer)
        base = f"/api/v1.0/mailboxes/{mailbox.id}/calendar"

        # Write endpoints must be blocked.
        assert api_client.post(f"{base}/rsvp/", {}).status_code == 403
        assert api_client.post(f"{base}/add/", {}).status_code == 403

    def test_viewer_can_read_calendars_and_conflicts(
        self,
        api_client,
        mailbox,
        caldav_channel,
        radicale_with_calendar,
    ):
        """VIEWER access can hit the read endpoints — calendar
        permissions are mirrored from mailbox permissions, so a viewer
        of the mailbox is intended to be able to see the calendar."""
        viewer = factories.UserFactory()
        factories.MailboxAccessFactory(
            mailbox=mailbox,
            user=viewer,
            role=MailboxRoleChoices.VIEWER,
        )
        api_client.force_authenticate(user=viewer)
        base = f"/api/v1.0/mailboxes/{mailbox.id}/calendar"

        assert api_client.get(f"{base}/calendars/").status_code == 200
        now = datetime.now(tz=timezone.utc)
        resp = api_client.post(
            f"{base}/conflicts/",
            {
                "start": now.isoformat(),
                "end": (now + timedelta(hours=1)).isoformat(),
            },
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Calendar list
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCalendarListView:
    """Tests for the calendar list endpoint."""

    def test_list_calendars(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.get(f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/")
        assert resp.status_code == 200
        calendars = resp.json()["calendars"]
        assert len(calendars) >= 1
        assert any(c["name"] == "Test Calendar" for c in calendars)

    def test_list_calendars_no_channel(
        self, api_client, mailbox, user_with_mailbox, settings
    ):
        """Without a caldav channel, returns an empty list."""
        settings.CALDAV_DEFAULT_URL = None
        settings.CALDAV_DEFAULT_PASSWORD = None
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.get(f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/")
        assert resp.status_code == 200
        assert resp.json()["calendars"] == []

    def test_list_calendars_requests_writable_only(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        """The endpoint must ask the service for writable calendars only,
        so read-only shares never reach the UI dropdown."""
        api_client.force_authenticate(user=user_with_mailbox)
        with mock.patch.object(CalDAVService, "list_calendars") as list_calendars:
            list_calendars.return_value = [
                {
                    "id": "https://caldav.example.com/rw/",
                    "name": "Writable",
                    "color": None,
                }
            ]
            resp = api_client.get(
                f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/"
            )

        assert resp.status_code == 200
        list_calendars.assert_called_once_with(writable_only=True)
        names = [c["name"] for c in resp.json()["calendars"]]
        assert names == ["Writable"]

    def test_list_calendars_403_instance_config_is_empty_list(
        self,
        api_client,
        mailbox,
        user_with_mailbox,
        instance_caldav_config,
    ):
        """On the instance-level path, a 403 means the OIDC identity has no
        principal upstream yet (provisioned on first login) — surface it as
        configured=True with an empty list, not an error."""
        api_client.force_authenticate(user=user_with_mailbox)
        with mock.patch.object(
            CalDAVService,
            "list_calendars",
            side_effect=CalDAVError("Forbidden", status_code=403),
        ):
            resp = api_client.get(
                f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/"
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["calendars"] == []
        assert body["configured"] is True

    def test_list_calendars_403_per_channel_surfaces_error(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        """On a per-mailbox channel the user supplies their own credentials,
        so a 403 is a genuine ACL/auth failure and must surface — not be
        masked as an empty calendar list."""
        api_client.force_authenticate(user=user_with_mailbox)
        with mock.patch.object(
            CalDAVService,
            "list_calendars",
            side_effect=CalDAVError("Forbidden", status_code=403),
        ):
            resp = api_client.get(
                f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/"
            )

        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCalendarConflictsView:
    """Tests for the calendar conflicts endpoint."""

    def test_check_conflicts_empty(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """No events => no conflicts."""
        api_client.force_authenticate(user=user_with_mailbox)
        now = datetime.now(tz=timezone.utc)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": (now + timedelta(hours=1)).isoformat(),
                "end": (now + timedelta(hours=2)).isoformat(),
            },
        )
        assert resp.status_code == 200
        assert resp.json()["conflicts"] == []

    def test_check_conflicts_with_event(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """An event in the time range is returned as a conflict."""
        _, put_event = radicale_with_calendar
        now = datetime.now(tz=timezone.utc)
        event_start = now + timedelta(hours=1)
        event_end = event_start + timedelta(hours=1)

        put_event(
            "conflict-test",
            f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:conflict-test@example.com
DTSTART:{event_start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{event_end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:Existing Meeting
END:VEVENT
END:VCALENDAR""",
        )

        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": event_start.isoformat(),
                "end": event_end.isoformat(),
            },
        )
        assert resp.status_code == 200
        conflicts = resp.json()["conflicts"]
        assert len(conflicts) >= 1
        assert any("Existing Meeting" in c["summary"] for c in conflicts)

    def test_check_conflicts_excludes_uid(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """exclude_uid filters out prior imports of the same event."""
        _, put_event = radicale_with_calendar
        now = datetime.now(tz=timezone.utc)
        event_start = now + timedelta(hours=3)
        event_end = event_start + timedelta(hours=1)

        shared_uid = "same-invite@example.com"
        put_event(
            "prior-import",
            f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:{shared_uid}
DTSTART:{event_start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{event_end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:JZ fête
END:VEVENT
END:VCALENDAR""",
        )

        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": event_start.isoformat(),
                "end": event_end.isoformat(),
                "exclude_uid": shared_uid,
            },
        )
        assert resp.status_code == 200
        conflicts = resp.json()["conflicts"]
        assert not any(c.get("uid") == shared_uid for c in conflicts)

    def test_check_conflicts_missing_fields(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {},
        )
        assert resp.status_code == 400

    def test_check_conflicts_naive_datetime_is_400_not_502(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        """A naive ISO datetime is a client-input error (400) — not a
        CalDAV upstream failure (502)."""
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": "2026-06-01T10:00:00",  # no tz
                "end": "2026-06-01T11:00:00",
            },
        )
        assert resp.status_code == 400

    def test_check_conflicts_inverted_range_is_400(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        now = datetime.now(tz=timezone.utc)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": (now + timedelta(hours=2)).isoformat(),
                "end": now.isoformat(),
            },
        )
        assert resp.status_code == 400

    def test_check_conflicts_response_does_not_leak_uid(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """The conflicts response must not echo UIDs back to the client
        (they can carry internal routing info; we only need them
        server-side for the exclude_uid filter)."""
        _, put_event = radicale_with_calendar
        now = datetime.now(tz=timezone.utc)
        start = now + timedelta(hours=3)
        end = start + timedelta(hours=1)
        put_event(
            "no-leak",
            f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:incident-12345-prod@example.com
DTSTART:{start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:secret
END:VEVENT
END:VCALENDAR""",
        )
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {"start": start.isoformat(), "end": end.isoformat()},
        )
        assert resp.status_code == 200
        for c in resp.json()["conflicts"]:
            assert "uid" not in c

    def test_check_conflicts_returns_existing_partstat(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """When ``exclude_uid`` matches a stored event, the response carries
        the responding mailbox's PARTSTAT so the UI can pre-select their
        prior RSVP. Without this, a user who already accepted would be
        re-prompted on every page load."""
        _, put_event = radicale_with_calendar
        now = datetime.now(tz=timezone.utc)
        start = now + timedelta(hours=5)
        end = start + timedelta(hours=1)
        uid = "already-accepted@example.com"
        put_event(
            "already-accepted",
            f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//Test//EN
BEGIN:VEVENT
UID:{uid}
DTSTART:{start.strftime("%Y%m%dT%H%M%SZ")}
DTEND:{end.strftime("%Y%m%dT%H%M%SZ")}
SUMMARY:Prior import
ATTENDEE;PARTSTAT=ACCEPTED:mailto:{mailbox}
END:VEVENT
END:VCALENDAR""",
        )
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "exclude_uid": uid,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["existing_partstat"] == "ACCEPTED"
        # The excluded UID must not also appear as a conflict.
        assert body["conflicts"] == []

    def test_check_conflicts_existing_partstat_null_when_no_match(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """No prior copy → ``existing_partstat`` is null, not an error."""
        api_client.force_authenticate(user=user_with_mailbox)
        now = datetime.now(tz=timezone.utc)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": now.isoformat(),
                "end": (now + timedelta(hours=1)).isoformat(),
                "exclude_uid": "never-stored@example.com",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["existing_partstat"] is None

    def test_check_conflicts_no_channel(
        self, api_client, mailbox, user_with_mailbox, settings
    ):
        """Without a caldav channel, returns 404."""
        settings.CALDAV_DEFAULT_URL = None
        settings.CALDAV_DEFAULT_PASSWORD = None
        api_client.force_authenticate(user=user_with_mailbox)
        now = datetime.now(tz=timezone.utc)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": now.isoformat(),
                "end": (now + timedelta(hours=1)).isoformat(),
            },
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RSVP (task-based – we call the task synchronously via .apply())
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCalendarRsvpView:
    """Tests for the calendar RSVP endpoint."""

    def test_rsvp_accepted(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        ics_data = _make_ics(mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
            {
                "ics_data": ics_data,
                "response": "ACCEPTED",
            },
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_rsvp_declined_stores_with_partstat_declined(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """DECLINED also PUTs a stored copy with PARTSTAT=DECLINED.

        Until the organizer removes the user from ATTENDEEs they are
        still invited, so the canonical record of the decline lives on
        the user's calendar. Sabre/dav's broker emits the iTIP REPLY to
        the organizer on PUT regardless of PARTSTAT value, so the
        decline notification is the side-effect of this same PUT.
        """
        calendar_url, _ = radicale_with_calendar
        api_client.force_authenticate(user=user_with_mailbox)
        uid = "decline-stored@example.com"
        ics_data = SAMPLE_ICS.format(
            dtstart="20260601T100000Z",
            dtend="20260601T110000Z",
            attendee=str(mailbox),
        ).replace("test-event-001@example.com", uid)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
            {"ics_data": ics_data, "response": "DECLINED"},
        )
        assert resp.status_code == 200

        # Tasks run eagerly under DevelopmentMinimal; the declined event
        # is now on Radicale. Fetch and inspect.
        stored = requests.get(
            calendar_url.rstrip("/") + f"/{uid}.ics",
            auth=(RADICALE_USER, RADICALE_PASSWORD),
            timeout=5,
        )
        assert stored.status_code == 200, stored.text
        stored_cal = ICalendar.from_ical(stored.text)
        vevent = stored_cal.walk("VEVENT")[0]
        attendees = vevent.get("ATTENDEE")
        if not isinstance(attendees, list):
            attendees = [attendees]
        # The mailbox attendee is recorded as DECLINED.
        decliner = next(
            a for a in attendees if str(a).lower().endswith(str(mailbox).lower())
        )
        assert decliner.params.get("PARTSTAT") == "DECLINED"

    def test_rsvp_invalid_response(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
            {
                "ics_data": "BEGIN:VCALENDAR\nEND:VCALENDAR",
                "response": "INVALID",
            },
        )
        assert resp.status_code == 400

    def test_rsvp_missing_fields(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
            {},
        )
        assert resp.status_code == 400

    def test_respond_to_event_refuses_when_mailbox_not_in_attendees(
        self,
        caldav_channel,
        radicale_with_calendar,
        mailbox,
    ):
        """RSVP must refuse when the mailbox is not on the ATTENDEE list.

        Without that, ``_update_partstat`` would silently no-op (PUT
        happens, but no PARTSTAT changes for the mailbox), the iTIP
        REPLY never reaches the organizer, and the user sees a
        misleading "Response saved — the organizer will be notified"
        toast. Exercised at the service level because eager Celery
        does not persist task results to the result backend, so the
        task-polling endpoint cannot observe the FAILURE here.
        """
        service = CalDAVService.from_channel(caldav_channel)
        ics_no_user = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:not-an-attendee@example.com\r\n"
            "DTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\n"
            "DTEND:20260601T110000Z\r\n"
            "SUMMARY:Forwarded invite\r\n"
            "ORGANIZER:mailto:org@example.com\r\n"
            "ATTENDEE:mailto:someone-else@example.com\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        with pytest.raises(CalDAVError, match="not an attendee"):
            service.respond_to_event(
                ics_data=ics_no_user,
                response="ACCEPTED",
                attendee_email=str(mailbox),
            )

    def test_rsvp_no_channel(self, api_client, mailbox, user_with_mailbox):
        """Without a caldav channel, returns 404 and does NOT schedule a task."""
        api_client.force_authenticate(user=user_with_mailbox)
        with mock.patch("core.api.viewsets.calendar.calendar_rsvp_task.delay") as delay:
            resp = api_client.post(
                f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
                {
                    "ics_data": _make_ics(mailbox),
                    "response": "ACCEPTED",
                },
            )
        assert resp.status_code == 404
        delay.assert_not_called()


# ---------------------------------------------------------------------------
# Add event
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
class TestCalendarAddEventView:
    """Tests for the calendar add-event endpoint."""

    def test_add_event(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        ics_data = _make_ics(mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/add/",
            {"ics_data": ics_data},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_add_event_missing_ics(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/add/",
            {},
        )
        assert resp.status_code == 400

    def test_add_event_stores_sanitized_copy(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """E2E: POST hostile ICS → fetch back from Radicale → assert the
        stored copy has no VALARM, no X-*, no javascript URL, and that
        SCHEDULE-AGENT=CLIENT is stamped on ORGANIZER."""
        calendar_url, _ = radicale_with_calendar
        api_client.force_authenticate(user=user_with_mailbox)
        uid = "e2e-add-event@example.com"
        hostile_ics = (
            f"BEGIN:VCALENDAR\r\n"
            f"VERSION:2.0\r\n"
            f"PRODID:-//Evil//EN\r\n"
            f"METHOD:REQUEST\r\n"
            f"BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTAMP:20260101T000000Z\r\n"
            f"DTSTART:20260601T100000Z\r\n"
            f"DTEND:20260601T110000Z\r\n"
            f"SUMMARY:hostile\r\n"
            f"ORGANIZER;X-EVIL=1:mailto:org@example.com\r\n"
            f"ATTENDEE;X-PWN=yes:mailto:victim@target.example\r\n"
            f"URL:javascript:alert(1)\r\n"
            f"X-MS-OLK-CONFTYPE:0\r\n"
            f"BEGIN:VALARM\r\nACTION:EMAIL\r\nTRIGGER:-PT15M\r\n"
            f"DESCRIPTION:x\r\nATTENDEE:mailto:spam@target.example\r\n"
            f"END:VALARM\r\n"
            f"END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/add/",
            {"ics_data": hostile_ics},
        )
        assert resp.status_code == 200, resp.content

        # Tasks run eagerly under DevelopmentMinimal; the event is now
        # on Radicale. Fetch it back and inspect.
        stored = requests.get(
            calendar_url.rstrip("/") + f"/{uid}.ics",
            auth=(RADICALE_USER, RADICALE_PASSWORD),
            timeout=5,
        )
        assert stored.status_code == 200, stored.text
        stored_cal = ICalendar.from_ical(stored.text)

        assert "METHOD" not in stored_cal
        vevent = stored_cal.walk("VEVENT")[0]
        assert "URL" not in vevent  # javascript: scheme dropped
        for key in vevent.keys():
            assert not key.upper().startswith("X-"), key
        assert not any(s.name == "VALARM" for s in vevent.subcomponents)
        # Attacker PRODID replaced with ours.
        assert "messages" in str(stored_cal["PRODID"])
        # SCHEDULE-AGENT=CLIENT stamped on ORGANIZER — sabre/dav will
        # NOT auto-dispatch iTIP REQUEST emails on this PUT.
        organizer = vevent.get("ORGANIZER")
        assert organizer.params.get("SCHEDULE-AGENT") == "CLIENT"
        assert "X-EVIL" not in {k.upper() for k in organizer.params}
        # Attendee still there but X-* params gone.
        attendees = vevent.get("ATTENDEE")
        if not isinstance(attendees, list):
            attendees = [attendees]
        assert len(attendees) == 1
        assert "X-PWN" not in {k.upper() for k in attendees[0].params}

    def test_rsvp_stores_sanitized_copy(
        self,
        api_client,
        mailbox,
        caldav_channel,
        user_with_mailbox,
        radicale_with_calendar,
    ):
        """E2E for /rsvp/: hostile ICS → sanitized copy stored. The
        RSVP path keeps default SCHEDULE-AGENT (so REPLY routes to
        organizer) but still rebuilds everything else."""
        calendar_url, _ = radicale_with_calendar
        api_client.force_authenticate(user=user_with_mailbox)
        uid = "e2e-rsvp@example.com"
        hostile_ics = (
            f"BEGIN:VCALENDAR\r\n"
            f"VERSION:2.0\r\n"
            f"PRODID:-//Evil//EN\r\n"
            f"BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            f"DTSTAMP:20260101T000000Z\r\n"
            f"DTSTART:20260601T100000Z\r\n"
            f"DTEND:20260601T110000Z\r\n"
            f"SUMMARY:hostile\r\n"
            f"ORGANIZER:mailto:org@example.com\r\n"
            f"ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{mailbox}\r\n"
            f"BEGIN:VALARM\r\nACTION:EMAIL\r\nTRIGGER:-PT15M\r\n"
            f"END:VALARM\r\n"
            f"END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
            {"ics_data": hostile_ics, "response": "ACCEPTED"},
        )
        assert resp.status_code == 200, resp.content

        stored = requests.get(
            calendar_url.rstrip("/") + f"/{uid}.ics",
            auth=(RADICALE_USER, RADICALE_PASSWORD),
            timeout=5,
        )
        assert stored.status_code == 200, stored.text
        stored_cal = ICalendar.from_ical(stored.text)
        vevent = stored_cal.walk("VEVENT")[0]
        # VALARM stripped on RSVP too.
        assert not any(s.name == "VALARM" for s in vevent.subcomponents)
        # PARTSTAT was updated to ACCEPTED before rebuild — the updated
        # value survives the rebuild via the ATTENDEE param allowlist.
        attendees = vevent.get("ATTENDEE")
        if not isinstance(attendees, list):
            attendees = [attendees]
        assert attendees[0].params.get("PARTSTAT") == "ACCEPTED"
        # RSVP=TRUE was popped by _update_partstat and not re-added.
        assert "RSVP" not in attendees[0].params
        # ORGANIZER kept default SCHEDULE-AGENT (so the server WILL
        # dispatch the REPLY iTIP to the organizer).
        organizer = vevent.get("ORGANIZER")
        assert organizer.params.get("SCHEDULE-AGENT") != "CLIENT"

    def test_add_event_no_channel(self, api_client, mailbox, user_with_mailbox):
        """Without a caldav channel, returns 404 and does NOT schedule a task."""
        api_client.force_authenticate(user=user_with_mailbox)
        with mock.patch(
            "core.api.viewsets.calendar.calendar_add_event_task.delay"
        ) as delay:
            resp = api_client.post(
                f"/api/v1.0/mailboxes/{mailbox.id}/calendar/add/",
                {"ics_data": _make_ics(mailbox)},
            )
        assert resp.status_code == 404
        delay.assert_not_called()


# ---------------------------------------------------------------------------
# Instance-level CalDAV config (no per-mailbox channel)
# ---------------------------------------------------------------------------


# Static shared secret sent as the Basic Auth password. Radicale with
# auth.type=none ignores the actual value; production servers verify it.
INSTANCE_CALDAV_PASSWORD = "stub-shared-secret"


@pytest.fixture()
def instance_caldav_config(radicale_server, settings):
    """Configure instance-level CalDAV settings pointing at Radicale.

    CALDAV_DEFAULT_URL is the CalDAV server root — the service resolves the
    per-user calendar-home-set via principal discovery, using the requesting
    user's OIDC identity email as the Basic Auth username (see
    ``CalDAVService.from_instance_config``).
    """
    settings.CALDAV_DEFAULT_URL = f"{radicale_server}/"
    settings.CALDAV_DEFAULT_PASSWORD = INSTANCE_CALDAV_PASSWORD


@pytest.fixture()
def instance_calendar(radicale_server, user_with_mailbox):
    """Create a calendar under the requesting user's principal on Radicale.

    Radicale with ``auth.type=none`` treats the Basic Auth username as the
    principal name and serves calendars under ``/{username}/``. The
    instance-level service authenticates as the requesting user's OIDC
    identity email (``CalDAVService.from_instance_config``), so the calendar
    must live under that user's path — not the mailbox's.
    """
    user_email = user_with_mailbox.email
    cal_url = f"{radicale_server}/{user_email}/instance-cal/"
    _mkcalendar(cal_url, "Instance Calendar", auth=(str(user_email), "ignored"))
    return radicale_server


@pytest.mark.django_db()
class TestCalendarInstanceConfig:
    """Tests using instance-level CalDAV settings instead of a per-mailbox Channel."""

    def test_list_calendars(
        self,
        api_client,
        mailbox,
        user_with_mailbox,
        instance_caldav_config,
        instance_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.get(f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/")
        assert resp.status_code == 200
        assert len(resp.json()["calendars"]) >= 1

    def test_conflicts(
        self,
        api_client,
        mailbox,
        user_with_mailbox,
        instance_caldav_config,
        instance_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        now = datetime.now(tz=timezone.utc)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/conflicts/",
            {
                "start": (now + timedelta(hours=1)).isoformat(),
                "end": (now + timedelta(hours=2)).isoformat(),
            },
        )
        assert resp.status_code == 200

    def test_rsvp(
        self,
        api_client,
        mailbox,
        user_with_mailbox,
        instance_caldav_config,
        instance_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/rsvp/",
            {"ics_data": _make_ics(mailbox), "response": "ACCEPTED"},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_add_event(
        self,
        api_client,
        mailbox,
        user_with_mailbox,
        instance_caldav_config,
        instance_calendar,
    ):
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.post(
            f"/api/v1.0/mailboxes/{mailbox.id}/calendar/add/",
            {"ics_data": _make_ics(mailbox)},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    def test_channel_overrides_instance_config(
        self,
        api_client,
        mailbox,
        user_with_mailbox,
        caldav_channel,
        instance_caldav_config,
        radicale_with_calendar,
    ):
        """Per-mailbox channel takes precedence over instance config."""
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.get(f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/")
        assert resp.status_code == 200
        assert len(resp.json()["calendars"]) >= 1

    def test_no_config_returns_empty_calendars(
        self, api_client, mailbox, user_with_mailbox, settings
    ):
        """Without channel or instance config, calendar list returns []."""
        settings.CALDAV_DEFAULT_URL = None
        settings.CALDAV_DEFAULT_PASSWORD = None
        api_client.force_authenticate(user=user_with_mailbox)
        resp = api_client.get(f"/api/v1.0/mailboxes/{mailbox.id}/calendar/calendars/")
        assert resp.status_code == 200
        assert resp.json()["calendars"] == []

    def test_no_config_returns_404_for_actions(
        self, api_client, mailbox, user_with_mailbox, settings
    ):
        """Without channel or instance config, action endpoints return 404."""
        settings.CALDAV_DEFAULT_URL = None
        settings.CALDAV_DEFAULT_PASSWORD = None
        api_client.force_authenticate(user=user_with_mailbox)
        base = f"/api/v1.0/mailboxes/{mailbox.id}/calendar"
        assert (
            api_client.post(
                f"{base}/rsvp/",
                {"ics_data": _make_ics(mailbox), "response": "ACCEPTED"},
            ).status_code
            == 404
        )
        assert (
            api_client.post(
                f"{base}/add/", {"ics_data": _make_ics(mailbox)}
            ).status_code
            == 404
        )
        now = datetime.now(tz=timezone.utc)
        assert (
            api_client.post(
                f"{base}/conflicts/",
                {
                    "start": now.isoformat(),
                    "end": (now + timedelta(hours=1)).isoformat(),
                },
            ).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# Unit tests for CalDAVService helpers (no network)
# ---------------------------------------------------------------------------


class TestUpdatePartstat:
    """Direct tests for PARTSTAT rewriting, independent of any CalDAV server."""

    def _build(
        self,
        attendee_line="ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:me@example.com",
    ):
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:x@example.com\r\n"
            "DTSTART:20260101T120000Z\r\n"
            "DTEND:20260101T130000Z\r\n"
            "SUMMARY:X\r\n"
            f"{attendee_line}\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        return ICalendar.from_ical(ics)

    @staticmethod
    def _attendee(cal):
        vevent = cal.walk("VEVENT")[0]
        att = vevent.get("ATTENDEE")
        if isinstance(att, list):
            att = att[0]
        return att

    def test_updates_existing_partstat_and_drops_rsvp(self):
        cal = self._build()
        CalDAVService._update_partstat(cal, "me@example.com", "ACCEPTED")
        att = self._attendee(cal)
        assert att.params["PARTSTAT"] == "ACCEPTED"
        assert "RSVP" not in att.params

    def test_adds_partstat_when_missing(self):
        cal = self._build("ATTENDEE:mailto:me@example.com")
        CalDAVService._update_partstat(cal, "me@example.com", "DECLINED")
        att = self._attendee(cal)
        assert att.params["PARTSTAT"] == "DECLINED"

    def test_case_insensitive_email_match(self):
        cal = self._build("ATTENDEE:mailto:ME@Example.COM")
        CalDAVService._update_partstat(cal, "me@example.com", "TENTATIVE")
        att = self._attendee(cal)
        assert att.params["PARTSTAT"] == "TENTATIVE"

    def test_leaves_other_attendees_untouched(self):
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:x@example.com\r\n"
            "DTSTART:20260101T120000Z\r\n"
            "DTEND:20260101T130000Z\r\n"
            "SUMMARY:X\r\n"
            "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:other@example.com\r\n"
            "ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:me@example.com\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        cal = ICalendar.from_ical(ics)
        CalDAVService._update_partstat(cal, "me@example.com", "ACCEPTED")

        attendees = cal.walk("VEVENT")[0].get("ATTENDEE")
        by_email = {str(a).lower(): a for a in attendees}
        assert by_email["mailto:me@example.com"].params["PARTSTAT"] == "ACCEPTED"
        assert by_email["mailto:other@example.com"].params["PARTSTAT"] == "NEEDS-ACTION"

    def test_returns_true_on_match(self):
        """The boolean return value lets callers distinguish "RSVP recorded"
        from "no-op write" — for ``respond_to_event`` the latter would
        silently ship an RSVP that never reaches the organizer."""
        cal = self._build()
        assert CalDAVService._update_partstat(cal, "me@example.com", "ACCEPTED") is True

    def test_returns_false_when_no_attendee_matches(self):
        cal = self._build("ATTENDEE:mailto:other@example.com")
        assert (
            CalDAVService._update_partstat(cal, "me@example.com", "ACCEPTED") is False
        )

    def test_substring_email_does_not_match(self):
        """An attendee whose address contains the target as a substring must
        not be updated; only an exact email match is."""
        ics = (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Test//Test//EN\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:x@example.com\r\n"
            "DTSTART:20260101T120000Z\r\n"
            "DTEND:20260101T130000Z\r\n"
            "SUMMARY:X\r\n"
            "ATTENDEE;PARTSTAT=NEEDS-ACTION:mailto:notme@example.com\r\n"
            "ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:me@example.com\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )
        cal = ICalendar.from_ical(ics)
        CalDAVService._update_partstat(cal, "me@example.com", "ACCEPTED")

        attendees = cal.walk("VEVENT")[0].get("ATTENDEE")
        by_email = {str(a).lower(): a for a in attendees}
        assert by_email["mailto:me@example.com"].params["PARTSTAT"] == "ACCEPTED"
        assert by_email["mailto:notme@example.com"].params["PARTSTAT"] == "NEEDS-ACTION"


class TestRebuildForStorage:
    """Default-deny ICS rebuild before storing on the CalDAV server.

    The rebuild keeps a fixed allowlist of RFC 5545 properties; everything
    else (VALARM, ATTACH, X-*, future iCal extensions) is dropped. This
    defends against:

      - Apple-style VALARM ACTION:EMAIL amplification (RFC 9074 §9).
      - X-ALT-DESC;FMTTYPE=text/html and other X-* injection vectors.
      - sabre/dav auto-dispatch of iTIP REQUEST on PUTs with
        ORGANIZER+ATTENDEE — covered separately by SCHEDULE-AGENT=CLIENT
        on the ``/add/`` path.
    """

    @staticmethod
    def _hostile_ics():
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "PRODID:-//Evil Corp//EN\r\n"
            "METHOD:REQUEST\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:evt@example.com\r\n"
            "DTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\n"
            "DTEND:20260601T110000Z\r\n"
            "SUMMARY:test\r\n"
            "DESCRIPTION:hi\r\n"
            "ORGANIZER;CN=Attacker;X-EVIL=1:mailto:attacker@example.com\r\n"
            "ATTENDEE;CN=v1;X-PWN=yes:mailto:victim1@target.example\r\n"
            "ATTENDEE:mailto:victim2@target.example\r\n"
            "ATTACH:data:text/html,<script>alert(1)</script>\r\n"
            "X-MS-OLK-CONFTYPE:0\r\n"
            "X-ALT-DESC;FMTTYPE=text/html:<b>evil</b>\r\n"
            "URL:javascript:alert(1)\r\n"
            "BEGIN:VALARM\r\n"
            "ACTION:EMAIL\r\n"
            "TRIGGER:-PT15M\r\n"
            "SUMMARY:Reminder\r\n"
            "DESCRIPTION:You have a meeting\r\n"
            "ATTENDEE:mailto:spam@target.example\r\n"
            "END:VALARM\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

    def _rebuild(self, ics):
        return rebuild_for_storage(ICalendar.from_ical(ics))

    def test_drops_method(self):
        out = self._rebuild(self._hostile_ics())
        assert "METHOD" not in out

    def test_drops_all_valarms(self):
        """Apple's well-known ACTION:EMAIL amplification turns the
        calendar account into a mailer — VALARM must never survive."""
        out = self._rebuild(self._hostile_ics())
        for comp in out.walk("VEVENT"):
            assert not any(s.name == "VALARM" for s in comp.subcomponents)

    def test_drops_attach(self):
        out = self._rebuild(self._hostile_ics())
        for comp in out.walk("VEVENT"):
            assert "ATTACH" not in comp

    def test_drops_x_properties(self):
        out = self._rebuild(self._hostile_ics())
        for comp in out.walk("VEVENT"):
            for key in comp.keys():
                assert not key.upper().startswith("X-"), key

    def test_drops_javascript_url(self):
        """URL with unsafe schemes is dropped (defense against
        ``javascript:``/``data:``/``file:``)."""
        out = self._rebuild(self._hostile_ics())
        vevent = out.walk("VEVENT")[0]
        assert "URL" not in vevent

    def test_keeps_http_url(self):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "URL:https://meet.example.com/abc\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert str(out.walk("VEVENT")[0]["URL"]) == "https://meet.example.com/abc"

    def test_strips_x_params_from_attendee(self):
        out = self._rebuild(self._hostile_ics())
        for att in out.walk("VEVENT")[0].get("ATTENDEE"):
            assert all(not k.upper().startswith("X-") for k in att.params)

    def test_strips_x_params_from_organizer(self):
        out = self._rebuild(self._hostile_ics())
        organizer = out.walk("VEVENT")[0].get("ORGANIZER")
        assert "X-EVIL" not in {k.upper() for k in organizer.params}

    def test_preserves_attendees(self):
        """Attendees are kept (users want to see who else was invited).
        iTIP suppression on /add/ is handled via SCHEDULE-AGENT, not by
        stripping attendees."""
        out = self._rebuild(self._hostile_ics())
        attendees = out.walk("VEVENT")[0].get("ATTENDEE")
        if not isinstance(attendees, list):
            attendees = [attendees]
        assert len(attendees) == 2

    def test_drops_event_without_uid(self):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nDTSTART:20260601T100000Z\r\n"
            "DTEND:20260601T110000Z\r\nSUMMARY:no-uid\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert len(out.walk("VEVENT")) == 0

    def test_synthesizes_missing_dtstamp(self):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert "DTSTAMP" in out.walk("VEVENT")[0]

    def test_rejects_unbounded_secondly_rrule(self):
        """SECONDLY without COUNT/UNTIL froze Thunderbird (Mozilla
        1770984) — drop the RRULE so the event becomes a single
        occurrence rather than infinite expansion."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "RRULE:FREQ=SECONDLY\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert "RRULE" not in out.walk("VEVENT")[0]

    def test_keeps_bounded_secondly_rrule(self):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "RRULE:FREQ=SECONDLY;COUNT=10\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert "RRULE" in out.walk("VEVENT")[0]

    def test_preserves_referenced_vtimezone(self):
        """VTIMEZONE blocks whose TZID is referenced by a kept event's
        DTSTART/DTEND must survive — otherwise non-UTC events render at
        the wrong time."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VTIMEZONE\r\nTZID:Europe/Paris\r\n"
            "BEGIN:STANDARD\r\nDTSTART:19710101T000000\r\n"
            "TZOFFSETFROM:+0100\r\nTZOFFSETTO:+0100\r\nTZNAME:CET\r\n"
            "END:STANDARD\r\nEND:VTIMEZONE\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART;TZID=Europe/Paris:20260601T100000\r\n"
            "DTEND;TZID=Europe/Paris:20260601T110000\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        tzids = {str(vtz.get("TZID")) for vtz in out.walk("VTIMEZONE")}
        assert "Europe/Paris" in tzids

    def test_drops_unreferenced_vtimezone(self):
        """A VTIMEZONE that no kept event references is dead weight."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VTIMEZONE\r\nTZID:America/Phantom\r\n"
            "BEGIN:STANDARD\r\nDTSTART:19710101T000000\r\n"
            "TZOFFSETFROM:-0500\r\nTZOFFSETTO:-0500\r\nTZNAME:EST\r\n"
            "END:STANDARD\r\nEND:VTIMEZONE\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert len(out.walk("VTIMEZONE")) == 0

    def test_does_not_mutate_input(self):
        """The rebuild must not modify the caller's parsed cal — the
        old in-place ``_filter_params`` would corrupt it via shared
        value objects."""
        ics = self._hostile_ics()
        cal = ICalendar.from_ical(ics)
        before = cal.to_ical()
        _ = rebuild_for_storage(cal)
        # Input must be byte-identical after rebuild.
        assert cal.to_ical() == before

    def test_always_uses_our_prodid(self):
        """The input's PRODID is attacker-controlled branding. Always
        stamp our own so the stored event isn't labeled "Created by
        Evil Corp" in the user's calendar app."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Evil//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert "messages" in str(out["PRODID"])
        assert "Evil" not in str(out["PRODID"])

    def test_dtstamp_fallback_prefers_last_modified(self):
        """Synthesizing DTSTAMP from server-now breaks iTIP versioning;
        prefer LAST-MODIFIED → CREATED → now."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "LAST-MODIFIED:20260515T120000Z\r\n"
            "CREATED:20260501T000000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        # LAST-MODIFIED wins over CREATED.
        assert out.walk("VEVENT")[0]["DTSTAMP"].dt == datetime(
            2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc
        )

    def test_url_rejects_leading_whitespace_javascript(self):
        """Browsers strip leading whitespace before scheme — a value
        like " javascript:..." can become a script URL on click. The
        regex must NOT accept it as 'http(s)'."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "URL: javascript:alert(1)\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert "URL" not in out.walk("VEVENT")[0]

    def test_url_accepts_uppercase_https_scheme(self):
        """Browsers lowercase schemes; we should too."""
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "URL:HTTPS://example.com/foo\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        out = self._rebuild(ics)
        assert "URL" in out.walk("VEVENT")[0]


class TestScheduleAgentClient:
    """RFC 6638 §7.1 opt-out: SCHEDULE-AGENT=CLIENT on ORGANIZER tells the
    CalDAV server not to auto-dispatch iTIP messages on PUT — the standard
    way to prevent /add/ from becoming a mass-mailer."""

    def test_sets_schedule_agent_client_on_organizer(self):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "ORGANIZER:mailto:a@example.com\r\n"
            "ATTENDEE:mailto:b@example.com\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        cal = ICalendar.from_ical(ics)
        CalDAVService._set_schedule_agent_client(cal)
        organizer = cal.walk("VEVENT")[0].get("ORGANIZER")
        assert organizer.params.get("SCHEDULE-AGENT") == "CLIENT"

    def test_handles_missing_organizer(self):
        ics = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//x//EN\r\n"
            "BEGIN:VEVENT\r\nUID:u\r\nDTSTAMP:20260101T000000Z\r\n"
            "DTSTART:20260601T100000Z\r\nDTEND:20260601T110000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        cal = ICalendar.from_ical(ics)
        # Must not raise.
        CalDAVService._set_schedule_agent_client(cal)


class TestParseColor:
    """Validate CalDAV ``calendar-color`` parsing — only hex passes."""

    def test_six_hex(self):
        assert CalDAVService._parse_color("#1a2b3c") == "#1a2b3c"

    def test_three_hex(self):
        assert CalDAVService._parse_color("#abc") == "#abc"

    def test_eight_hex_trims_alpha(self):
        assert CalDAVService._parse_color("#1a2b3cff") == "#1a2b3c"

    def test_rejects_named_color(self):
        assert CalDAVService._parse_color("red") is None

    def test_rejects_rgb_function(self):
        assert CalDAVService._parse_color("rgb(255,0,0)") is None

    def test_rejects_css_injection(self):
        assert CalDAVService._parse_color("#fff; }body{display:none") is None

    def test_empty_returns_none(self):
        assert CalDAVService._parse_color("") is None
        assert CalDAVService._parse_color(None) is None


class TestRequestSameOriginGuard:
    """``_request`` must refuse to talk to any host but the configured one."""

    def test_request_refuses_cross_origin(self):
        service = CalDAVService(url="https://caldav.example.com/home/")
        with pytest.raises(CalDAVError):
            service._request("GET", "https://attacker.example.org/leak")

    def test_request_refuses_relative_url(self):
        """Relative URLs have no scheme/netloc — _same_origin returns
        False; defense-in-depth against a malformed PROPFIND href."""
        service = CalDAVService(url="https://caldav.example.com/home/")
        with pytest.raises(CalDAVError):
            service._request("GET", "/leak")

    def test_request_refuses_userinfo_confusion(self):
        """https://trusted.com@attacker.com/ has hostname=attacker.com
        per RFC 3986 — must be rejected."""
        service = CalDAVService(url="https://caldav.example.com/")
        with pytest.raises(CalDAVError):
            service._request(
                "GET", "https://caldav.example.com@attacker.example.org/leak"
            )

    def test_request_disables_redirects(self):
        """The same-origin guard only validates the initial URL — without
        ``allow_redirects=False`` a 302 from a (third-party) CalDAV server
        would bypass it. Verify the kwarg is forced even if a caller did
        not pass it."""
        service = CalDAVService(url="https://caldav.example.com/")
        captured = {}

        class _StubSession:
            def request(self, method, url, **kwargs):
                captured["allow_redirects"] = kwargs.get("allow_redirects")

                class _Resp:
                    status_code = 200
                    text = ""

                return _Resp()

        service._session = _StubSession()  # type: ignore[assignment]
        service._request("GET", "https://caldav.example.com/cal/")
        assert captured["allow_redirects"] is False


class TestSameOriginPortNormalization:
    """Default ports (80/443) must compare equal whether they're explicit
    in the URL or implicit — otherwise legit setups break."""

    def test_https_default_port_matches_implicit(self):
        s = CalDAVService(url="https://caldav.example.com/")
        assert s._same_origin("https://caldav.example.com:443/cal/")

    def test_https_implicit_matches_default_port(self):
        s = CalDAVService(url="https://caldav.example.com:443/")
        assert s._same_origin("https://caldav.example.com/cal/")

    def test_http_default_port_matches_implicit(self):
        s = CalDAVService(url="http://caldav.example.com/")
        assert s._same_origin("http://caldav.example.com:80/cal/")

    def test_explicit_nonstandard_port_must_match(self):
        s = CalDAVService(url="https://caldav.example.com:8443/")
        assert s._same_origin("https://caldav.example.com:8443/cal/")
        assert not s._same_origin("https://caldav.example.com/cal/")
        assert not s._same_origin("https://caldav.example.com:443/cal/")

    def test_different_scheme_not_same_origin(self):
        s = CalDAVService(url="https://caldav.example.com/")
        assert not s._same_origin("http://caldav.example.com/cal/")

    def test_case_insensitive_hostname(self):
        s = CalDAVService(url="https://caldav.example.com/")
        assert s._same_origin("https://Caldav.Example.COM/cal/")


@pytest.mark.django_db()
@pytest.mark.caldav_ssrf_real
class TestChannelSsrfGuard:
    """Verify the production SSRF guard fires on the per-channel path.

    This class opts out of the autouse bypass (see ``_bypass_caldav_ssrf``)
    so we test the real ``_build_ssrf_adapter`` — the rest of the suite
    targets localhost which the production guard correctly rejects.
    """

    def test_loopback_is_rejected(self, mailbox):

        channel = factories.ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CALDAV,
            settings={"url": "http://localhost:9999/"},
            encrypted_settings={"username": "u", "password": "p"},
        )
        service = CalDAVService.from_channel(channel)
        # The adapter is built lazily on first request. Force a request
        # so the guard fires.
        with pytest.raises(CalDAVError, match="loopback|private"):
            service._request("GET", "http://localhost:9999/")

    def test_ip_literal_is_rejected(self, mailbox):
        """Real CalDAV providers use domain names. Per-channel URLs with a
        raw IP literal (public OR private) are rejected outright so the
        per-channel guard can rely on DNS for the private-IP check."""

        channel = factories.ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CALDAV,
            settings={"url": "http://192.168.0.1/"},
            encrypted_settings={"username": "u", "password": "p"},
        )
        service = CalDAVService.from_channel(channel)
        with pytest.raises(CalDAVError, match="IP addresses are not allowed"):
            service._request("GET", "http://192.168.0.1/")

    def test_unsupported_scheme_is_rejected(self, mailbox):
        """``file://`` (and any non-http(s)) is rejected when the adapter
        builds. Belt-and-braces with the ``_same_origin`` guard, which
        already refuses URLs without a hostname."""

        channel = factories.ChannelFactory(
            mailbox=mailbox,
            type=ChannelTypes.CALDAV,
            settings={"url": "file:///etc/passwd"},
            encrypted_settings={"username": "u", "password": "p"},
        )
        service = CalDAVService.from_channel(channel)
        # Force adapter creation explicitly so the scheme check fires
        # (vs. the same-origin check, which would also reject this URL
        # but via a different code path).
        with pytest.raises(CalDAVError, match="scheme"):
            service._build_ssrf_adapter()


class TestPickCalendarUrl:
    """Direct tests for calendar URL selection / SSRF guard."""

    def _service_with_calendars(self, calendar_ids):
        service = CalDAVService(url="https://caldav.example.com/")
        # ``_pick_calendar_url`` calls ``list_calendars(writable_only=True)``;
        # accept (and ignore) the kwarg in the stub so this helper exercises
        # the same code path as production.
        service.list_calendars = lambda **_kw: [  # type: ignore[method-assign]
            {"id": cid, "name": cid} for cid in calendar_ids
        ]
        return service

    def test_returns_first_when_no_id_given(self):
        service = self._service_with_calendars(
            ["https://caldav.example.com/u/cal1/", "https://caldav.example.com/u/cal2/"]
        )
        assert service._pick_calendar_url(None) == "https://caldav.example.com/u/cal1/"

    def test_accepts_known_calendar_id(self):
        service = self._service_with_calendars(
            ["https://caldav.example.com/u/cal1/", "https://caldav.example.com/u/cal2/"]
        )
        assert (
            service._pick_calendar_url("https://caldav.example.com/u/cal2/")
            == "https://caldav.example.com/u/cal2/"
        )

    def test_rejects_arbitrary_url(self):
        """An attacker-controlled URL must not be used as a calendar target."""
        service = self._service_with_calendars(["https://caldav.example.com/u/cal1/"])
        with pytest.raises(CalDAVError):
            service._pick_calendar_url("https://attacker.example.org/evil/")

    def test_rejects_cross_origin_before_listing(self):
        """The origin check must reject foreign hosts even if list_calendars()
        somehow returned a matching entry — defense in depth."""
        service = CalDAVService(url="https://caldav.example.com/")
        called = {"n": 0}

        def _spy(**_kw):
            called["n"] += 1
            return [{"id": "https://attacker.example.org/evil/", "name": "evil"}]

        service.list_calendars = _spy  # type: ignore[method-assign]
        with pytest.raises(CalDAVError):
            service._pick_calendar_url("https://attacker.example.org/evil/")
        assert called["n"] == 0

    def test_rejects_scheme_relative_or_malformed(self):
        service = self._service_with_calendars(["https://caldav.example.com/u/cal1/"])
        with pytest.raises(CalDAVError):
            service._pick_calendar_url("/u/cal1/")

    def test_rejects_unknown_calendar_on_same_host(self):
        service = self._service_with_calendars(["https://caldav.example.com/u/cal1/"])
        with pytest.raises(CalDAVError):
            service._pick_calendar_url("https://caldav.example.com/u/other/")

    def test_raises_when_no_calendars(self):
        service = self._service_with_calendars([])
        with pytest.raises(CalDAVError):
            service._pick_calendar_url(None)

    def test_requests_writable_only_calendars(self):
        """``_pick_calendar_url`` must consult writable calendars only —
        otherwise a read-only shared id passes selection and only fails
        later at PUT time, with a misleading error."""
        service = CalDAVService(url="https://caldav.example.com/")
        seen_kwargs = {}

        def _spy(**kw):
            seen_kwargs.update(kw)
            return [{"id": "https://caldav.example.com/u/cal1/", "name": "cal1"}]

        service.list_calendars = _spy  # type: ignore[method-assign]
        service._pick_calendar_url(None)
        assert seen_kwargs == {"writable_only": True}


# ---------------------------------------------------------------------------
# UID sanitization in _put_event (defends against attacker-controlled ICS)
# ---------------------------------------------------------------------------


class TestPutEventUidSanitization:
    """The UID extracted from an ICS file flows into the PUT URL; malicious
    UIDs (path separators, traversal, CRLF) must never reach the server
    verbatim — they must either be percent-encoded or replaced by a UUID."""

    @staticmethod
    def _ics_with_uid(uid):
        return (
            "BEGIN:VCALENDAR\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            "SUMMARY:test\r\n"
            "DTSTART:20260516T100000Z\r\n"
            "DTEND:20260516T110000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        )

    def _capture_put_url(self, ics_data):
        service = CalDAVService(url="https://caldav.example.com/home/")
        seen = {}

        def _fake_request(method, url, **_kwargs):
            seen["method"] = method
            seen["url"] = url

            class _R:
                status_code = 201
                text = ""

            return _R()

        service._request = _fake_request  # type: ignore[method-assign]
        service._put_event("https://caldav.example.com/home/cal/", ics_data)
        return seen["url"]

    def test_path_separator_uid_does_not_escape_collection(self):
        url = self._capture_put_url(self._ics_with_uid("../../etc/passwd"))
        # Either the UID got percent-encoded (no raw slashes) or it was
        # rejected and replaced by a generated UUID. Either way the PUT URL
        # must stay inside the original calendar collection.
        assert url.startswith("https://caldav.example.com/home/cal/")
        tail = url[len("https://caldav.example.com/home/cal/") :]
        assert "/" not in tail
        assert ".." not in tail
        assert tail.endswith(".ics")

    def test_crlf_uid_is_neutralized(self):
        url = self._capture_put_url(self._ics_with_uid("evil\r\nX-Header: pwn"))
        # No literal CR/LF may survive into the URL — they'd allow header
        # injection on the wire.
        assert "\r" not in url
        assert "\n" not in url

    def test_backslash_uid_is_neutralized(self):
        url = self._capture_put_url(self._ics_with_uid("a\\b"))
        tail = url[len("https://caldav.example.com/home/cal/") :]
        assert "\\" not in tail

    def test_safe_uid_is_preserved(self):
        url = self._capture_put_url(self._ics_with_uid("safe-uid-123@example.com"))
        assert url.endswith("/safe-uid-123%40example.com.ics") or url.endswith(
            "/safe-uid-123@example.com.ics"
        )


# ---------------------------------------------------------------------------
# Writable-only filter on list_calendars
# ---------------------------------------------------------------------------


def _make_propfind_response(calendars):
    """Build a multistatus body listing the given calendars.

    Each entry is (href, displayname, privileges) where privileges is a
    list of DAV tag names (without namespace) to emit under
    current-user-privilege-set, or None to omit the element entirely.
    """
    parts = [
        '<?xml version="1.0"?>',
        '<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">',
    ]
    for href, name, privileges in calendars:
        parts.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop>")
        parts.append(f"<d:displayname>{name}</d:displayname>")
        parts.append("<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>")
        if privileges is not None:
            parts.append("<d:current-user-privilege-set>")
            for p in privileges:
                parts.append(f"<d:privilege><d:{p}/></d:privilege>")
            parts.append("</d:current-user-privilege-set>")
        parts.append("</d:prop></d:propstat></d:response>")
    parts.append("</d:multistatus>")
    return "".join(parts)


class TestListCalendarsWritableFilter:
    """Unit tests for list_calendars(writable_only=True) parsing."""

    def _service_with_body(self, body):
        service = CalDAVService(url="https://caldav.example.com/home/")
        service._home_set = "https://caldav.example.com/home/"

        class _FakeResp:
            text = body

        service._propfind = lambda *a, **kw: _FakeResp()  # type: ignore[method-assign]
        return service

    def test_all_calendars_returned_when_not_filtering(self):
        body = _make_propfind_response(
            [
                ("/home/writable/", "Writable", ["read", "write"]),
                ("/home/readonly/", "Read only", ["read"]),
            ]
        )
        service = self._service_with_body(body)
        names = [c["name"] for c in service.list_calendars()]
        assert names == ["Writable", "Read only"]

    def test_filter_excludes_read_only_calendars(self):
        body = _make_propfind_response(
            [
                ("/home/writable/", "Writable", ["read", "write"]),
                ("/home/readonly/", "Read only", ["read"]),
            ]
        )
        service = self._service_with_body(body)
        names = [c["name"] for c in service.list_calendars(writable_only=True)]
        assert names == ["Writable"]

    def test_filter_accepts_write_content_as_sufficient(self):
        """Granular servers may report write-content instead of write."""
        body = _make_propfind_response(
            [("/home/cal/", "Cal", ["read", "write-content"])]
        )
        service = self._service_with_body(body)
        names = [c["name"] for c in service.list_calendars(writable_only=True)]
        assert names == ["Cal"]

    def test_filter_trusts_servers_without_privilege_set(self):
        """A server that omits current-user-privilege-set is trusted."""
        body = _make_propfind_response([("/home/cal/", "Cal", None)])
        service = self._service_with_body(body)
        names = [c["name"] for c in service.list_calendars(writable_only=True)]
        assert names == ["Cal"]

    def test_filter_excludes_when_privilege_set_lacks_write(self):
        """Privilege set present but no write-family privilege → excluded."""
        body = _make_propfind_response([("/home/cal/", "Cal", ["read"])])
        service = self._service_with_body(body)
        assert not service.list_calendars(writable_only=True)


# ---------------------------------------------------------------------------
# Credential contract: Basic Auth user = OIDC identity email, password = setting
# ---------------------------------------------------------------------------


@pytest.mark.django_db()
def test_instance_config_sends_oidc_email_as_basic_auth_user(settings):
    """Instance-level auth: Basic Auth user must be the requesting user's
    OIDC identity email (NOT the mailbox address — the CalDAV provider
    keys principals on the OIDC ``email`` claim) and the password must be
    CALDAV_DEFAULT_PASSWORD verbatim."""
    settings.CALDAV_DEFAULT_URL = "https://caldav.example.com/"
    settings.CALDAV_DEFAULT_PASSWORD = "shared-secret-xyz"

    oidc_email = "alice@identity.example"
    service = CalDAVService.from_instance_config(oidc_email)

    assert service.username == oidc_email
    assert service.password == "shared-secret-xyz"
    assert service.session.auth == (oidc_email, "shared-secret-xyz")
