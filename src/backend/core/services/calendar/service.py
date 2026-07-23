"""Minimal CalDAV client for calendar invite management.

Implements only the CalDAV operations we need (list calendars, search
events, add event, RSVP) directly over HTTP using ``requests`` and
``icalendar`` for parsing, rather than pulling in the full ``caldav``
library's dependency surface.
"""

# pylint: disable=too-many-lines

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, unquote, urljoin, urlparse

from django.conf import settings as django_settings

import defusedxml.ElementTree as ET
import requests
from defusedxml.ElementTree import ParseError as DefusedParseError
from icalendar import Calendar as ICalendar

from core.services.calendar.ics_rebuild import rebuild_for_storage
from core.services.ssrf import (
    SSRFProtectedAdapter,
    SSRFValidationError,
    validate_hostname,
)

logger = logging.getLogger(__name__)

CALDAV_TIMEOUT = 20

DAV_NS = "DAV:"
CALDAV_NS = "urn:ietf:params:xml:ns:caldav"
APPLE_ICAL_NS = "http://apple.com/ns/ical/"
CS_NS = "http://calendarserver.org/ns/"
# suitenumerique/calendars custom extension. Currently exposes
# ``calendar-owner-type`` (MAILBOX vs. user's own) so we don't have to
# infer it from the principal URL shape.
LASUITE_NS = "http://lasuite.numerique.gouv.fr/ns/"

# Accept only #RRGGBB / #RGB hex from the calendar server's color property —
# anything else is treated as no color (defensive against attacker-controlled
# values flowing into React inline styles).
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


def _format_utc(dt):
    # Reject naive datetimes — silently formatting them as UTC would lie
    # about the wall-clock value sent to the CalDAV server.
    if dt.tzinfo is None:
        raise ValueError("Naive datetime; pass a timezone-aware datetime.")
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class CalDAVError(Exception):
    """CalDAV protocol or server error.

    ``status_code`` is the upstream HTTP status when the failure was an
    HTTP response (4xx/5xx); None for network-level errors, validation
    failures, or anything that didn't yield an HTTP status.
    """

    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class CalDAVService:  # pylint: disable=too-many-instance-attributes
    """Minimal CalDAV client (HTTP + icalendar, no full caldav lib)."""

    def __init__(self, url, username="", password="", headers=None, ssrf_protect=False):
        self.url = url
        self.username = username
        self.password = password
        self.extra_headers = headers or {}
        self.ssrf_protect = ssrf_protect
        self._session = None
        self._home_set = None
        # Resolved-and-pinned destination, populated lazily on session
        # creation when ``ssrf_protect`` is True.
        self._ssrf_adapter = None

    @property
    def session(self):
        """Lazily-created ``requests.Session`` with auth and headers applied.

        When ``ssrf_protect`` is True (per-mailbox channels — URL is
        user-supplied and untrusted), an ``SSRFProtectedAdapter`` is
        mounted so every request resolves the hostname through
        ``validate_hostname`` (rejecting private/loopback IPs) and pins
        the resulting IP to defeat DNS-rebinding. Untrusted-input
        scheme/private-IP checks at config time (see ``from_channel``)
        already filter the obvious cases; this is the per-request
        guarantee.
        """
        if self._session is None:
            s = requests.Session()
            if self.username or self.password:
                s.auth = (self.username, self.password)
            s.headers.update(self.extra_headers)
            if self.ssrf_protect:
                adapter = self._build_ssrf_adapter()
                # Mount under the *scheme* prefix so every absolute URL
                # we issue (PROPFIND/REPORT/PUT, possibly to the server's
                # advertised href on the same origin) flows through the
                # pinned adapter.
                s.mount("https://", adapter)
                s.mount("http://", adapter)
                self._ssrf_adapter = adapter
            self._session = s
        return self._session

    def _build_ssrf_adapter(self):
        """Resolve the configured URL once, pin the IP, return adapter.

        Called lazily on session creation. Raises ``CalDAVError`` if the
        hostname resolves to a blocked range — propagating up turns this
        into a 502 to the caller, surfaced as
        "CalDAV server returned an error" in the UI.
        """
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"}:
            raise CalDAVError(
                f"CalDAV URL scheme '{parsed.scheme}' is not allowed (http/https only)."
            )
        if not parsed.hostname:
            raise CalDAVError("CalDAV URL has no hostname.")
        try:
            ips = validate_hostname(parsed.hostname, allow_ip_literal=False)
        except SSRFValidationError as exc:
            raise CalDAVError(f"CalDAV URL host rejected: {exc}") from exc
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return SSRFProtectedAdapter(
            dest_ip=ips[0],
            dest_port=port,
            original_hostname=parsed.hostname,
            original_scheme=parsed.scheme,
        )

    def _request(self, method, url, **kwargs):
        # Belt-and-braces SSRF guard: the session carries Basic Auth that
        # would otherwise leak to any host the server response steers us
        # toward (PROPFIND hrefs are server-controlled and can be absolute,
        # cross-origin URLs). Pin every outbound request to the configured
        # CalDAV origin.
        if not self._same_origin(url):
            logger.warning(
                "CalDAV SSRF guard tripped: refused %s to %s (configured: %s)",
                method,
                url,
                self.url,
            )
            raise CalDAVError(
                f"Refusing {method} to {url}: not on configured CalDAV origin."
            )
        kwargs.setdefault("timeout", CALDAV_TIMEOUT)
        # Never follow redirects. The same-origin guard above only validates
        # the *initial* URL; without this, a CalDAV server (especially a
        # third-party one configured via a per-mailbox Channel) could 302
        # us to an attacker-controlled host, bypassing the guard.
        kwargs.setdefault("allow_redirects", False)
        try:
            resp = self.session.request(method, url, **kwargs)
        except requests.exceptions.RequestException as exc:
            # Surface network-level failures (timeout, connection reset, DNS,
            # SSL) as CalDAVError so callers can handle them uniformly with
            # protocol errors instead of leaking ``requests`` exceptions.
            raise CalDAVError(f"{method} {url} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise CalDAVError(
                f"{method} {url} failed: HTTP {resp.status_code}",
                status_code=resp.status_code,
            )
        return resp

    def _propfind(self, url, body, depth="0"):
        return self._request(
            "PROPFIND",
            url,
            data=body.encode("utf-8"),
            headers={
                "Depth": str(depth),
                "Content-Type": "application/xml; charset=utf-8",
            },
        )

    @property
    def home_set(self):
        """Calendar-home-set URL, resolved lazily via principal discovery.

        Falls back to the configured URL if discovery fails (for servers or
        URLs that already point directly at the home set).
        """
        if self._home_set is not None:
            return self._home_set
        try:
            self._home_set = self._discover_home_set() or self.url
        except (CalDAVError, DefusedParseError, AttributeError) as exc:
            # CalDAVError: protocol/HTTP/network. DefusedParseError: malformed
            # PROPFIND XML. AttributeError: a defusedxml node was None where
            # we expected it (server returned partial body).
            # Anything else (programmer error) should NOT be swallowed —
            # let it bubble so a true bug surfaces instead of silently
            # falling back to the configured URL.
            logger.debug(
                "home-set discovery failed for %s (%s), using URL directly",
                self.url,
                exc,
                exc_info=True,
            )
            self._home_set = self.url
        return self._home_set

    def _discover_home_set(self):
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:">'
            "<d:prop><d:current-user-principal/></d:prop>"
            "</d:propfind>"
        )
        root = ET.fromstring(self._propfind(self.url, body, depth="0").text)
        principal_href = root.findtext(
            f".//{_q(DAV_NS, 'current-user-principal')}/{_q(DAV_NS, 'href')}"
        )
        principal_url = (
            urljoin(self.url, principal_href.strip()) if principal_href else self.url
        )

        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><c:calendar-home-set/></d:prop>"
            "</d:propfind>"
        )
        root = ET.fromstring(self._propfind(principal_url, body, depth="0").text)
        home_href = root.findtext(
            f".//{_q(CALDAV_NS, 'calendar-home-set')}/{_q(DAV_NS, 'href')}"
        )
        if not home_href:
            return principal_url
        return urljoin(self.url, home_href.strip())

    def list_calendars(self, writable_only=False):
        """List all calendars with a single PROPFIND depth=1 (no N+1).

        When ``writable_only`` is True, calendars the current user cannot
        write to (read-only shares, subscribed calendars) are filtered out
        based on the DAV ``current-user-privilege-set``. Servers that do
        not advertise the privilege set are trusted (the calendar is kept)
        to avoid hiding legitimate writable calendars on minimal CalDAV
        implementations.
        """
        body = (
            '<?xml version="1.0"?>'
            '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"'
            ' xmlns:a="http://apple.com/ns/ical/"'
            ' xmlns:cs="http://calendarserver.org/ns/"'
            ' xmlns:ls="http://lasuite.numerique.gouv.fr/ns/">'
            "<d:prop>"
            "<d:displayname/><d:resourcetype/><a:calendar-color/>"
            "<a:calendar-order/>"
            "<d:current-user-privilege-set/>"
            "<cs:invite/>"
            "<ls:calendar-owner-type/>"
            "</d:prop>"
            "</d:propfind>"
        )
        root = ET.fromstring(self._propfind(self.home_set, body, depth="1").text)
        result = []
        for response in root.findall(_q(DAV_NS, "response")):
            href = response.findtext(_q(DAV_NS, "href"))
            if not href:
                continue
            href = href.strip()
            rtype = response.find(f".//{_q(DAV_NS, 'resourcetype')}")
            if rtype is None or rtype.find(_q(CALDAV_NS, "calendar")) is None:
                continue
            if writable_only and not self._response_is_writable(response):
                continue
            displayname = response.findtext(f".//{_q(DAV_NS, 'displayname')}") or href
            color = self._parse_color(
                response.findtext(f".//{_q(APPLE_ICAL_NS, 'calendar-color')}")
            )
            order = self._parse_order(
                response.findtext(f".//{_q(APPLE_ICAL_NS, 'calendar-order')}")
            )
            owner_email, owner_type = self._parse_owner(response)
            result.append(
                {
                    "id": urljoin(self.url, href),
                    "name": displayname.strip(),
                    "color": color,
                    "order": order,
                    "owner_email": owner_email,
                    "owner_type": owner_type,
                }
            )
        # Honour the user's manual ordering from the Calendars UI (Apple's
        # ``calendar-order`` property, written via PROPPATCH by the
        # suitenumerique/calendars frontend). Calendars without an order
        # value go after the ordered ones, in original server order — so
        # adding a new calendar doesn't push it ahead of explicitly-ranked
        # ones. Python's ``sorted`` is stable, which preserves server
        # order within each bucket.
        result.sort(key=lambda c: (c["order"] is None, c["order"] or 0))
        return result

    @staticmethod
    def _parse_owner(response):
        """Extract (owner_email, owner_type) from a calendar PROPFIND response.

        Owner type comes from the suitenumerique
        ``ls:calendar-owner-type`` extension: ``MAILBOX`` for shared-mailbox
        calendars, absent (404 in the propstat) for the user's own
        calendars — which we report as ``USER``. We deliberately avoid
        guessing the type from the principal URL shape; the extension is
        authoritative.

        The owner's email is the last non-empty path segment of
        ``cs:invite/cs:organizer/d:href`` — the suite emits a principal
        href like ``/.../principals/users/<email>`` or
        ``/.../principals/mailboxes/<email>``, and the trailing segment is
        the address. URL-decoded so percent-encoded ``@`` (RFC-legal but
        emitted by some servers) matches the plain mailto: addresses in
        event ATTENDEE entries.

        Returns ``(None, None)`` for CalDAV servers that don't expose
        ``cs:invite`` (non-suitenumerique implementations) — callers should
        treat that as "owner unknown" and fall back to mailbox-email
        matching.
        """
        organizer_href = response.findtext(
            f".//{_q(CS_NS, 'invite')}/{_q(CS_NS, 'organizer')}/{_q(DAV_NS, 'href')}"
        )
        if not organizer_href:
            return None, None
        last_segment = organizer_href.strip().rstrip("/").rsplit("/", 1)[-1]
        if not last_segment:
            return None, None
        owner_email = unquote(last_segment)

        raw_type = response.findtext(f".//{_q(LASUITE_NS, 'calendar-owner-type')}")
        owner_type = "MAILBOX" if (raw_type or "").strip() == "MAILBOX" else "USER"
        return owner_email, owner_type

    @staticmethod
    def _parse_order(raw):
        """Parse Apple ``calendar-order`` (integer sort key) defensively.

        The property is written by the suitenumerique/calendars frontend
        as a decimal integer, but the value flows through user input
        (PROPPATCH from the browser) so we must not trust the format.
        Returns None for missing/malformed values — those calendars sort
        last while preserving server order among themselves.
        """
        if not raw:
            return None
        try:
            return int(raw.strip())
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_color(raw):
        """Validate a CalDAV ``calendar-color`` value as a 3- or 6-digit hex.

        Some servers return 8-hex (#RRGGBBAA) — trim alpha for CSS. Anything
        else (named colors, rgb(), garbage) is rejected to keep
        attacker-controlled strings out of the frontend's inline ``style``.
        """
        if not raw:
            return None
        value = raw.strip()
        if len(value) == 9 and value.startswith("#"):
            value = value[:7]
        return value if _HEX_COLOR_RE.match(value) else None

    @staticmethod
    def _response_is_writable(response):
        """Whether the DAV response advertises a write privilege.

        Absence of ``current-user-privilege-set`` is treated as writable
        (minimal CalDAV servers don't advertise ACLs); presence with no
        write-family privilege is treated as read-only.
        """
        priv_set = response.find(f".//{_q(DAV_NS, 'current-user-privilege-set')}")
        if priv_set is None:
            return True
        write_tags = {
            _q(DAV_NS, "write"),
            _q(DAV_NS, "write-content"),
            _q(DAV_NS, "all"),
        }
        for priv in priv_set.iter(_q(DAV_NS, "privilege")):
            for child in priv:
                if child.tag in write_tags:
                    return True
        return False

    def check_conflicts(self, start, end, exclude_uid=None, attendee_email=None):
        """Find conflicts and the existing PARTSTAT per identity for the UID.

        Returns ``{"conflicts": [...], "existing_partstats": {identity: str}}``.

        Events whose UID matches ``exclude_uid`` are NOT returned as
        conflicts (a prior import of the same invite should not flag the
        event as conflicting with itself).

        A mailbox can act through several attendee-owned calendars (the
        suitenumerique/calendars CalDAV server exposes each calendar's
        ``owner_email``). The stored copy living in calendar X speaks for
        X's owner, so the excluded event is inspected for *that owner's*
        PARTSTAT, keyed by identity in ``existing_partstats`` — letting the
        UI pre-select the right prior RSVP for whichever calendar is
        selected (and avoid re-prompting for a choice already made).
        ``attendee_email`` is the fallback identity for servers that don't
        expose ``owner_email``.
        """
        conflicts = []
        existing_partstats = {}
        for cal in self.list_calendars():
            try:
                events = self._calendar_query(cal["id"], start, end)
            except CalDAVError:
                logger.exception(
                    "Error searching for conflicts on calendar %s", cal["name"]
                )
                continue
            # The identity a copy in this calendar speaks for: its owner
            # when the server exposes it, otherwise the acting mailbox
            # (servers without owner metadata behave as before).
            owner_lc = (cal.get("owner_email") or attendee_email or "").lower() or None
            for ics_text in events:
                summary = self._summarize_event(ics_text, cal["name"])
                if summary is None:
                    continue
                if exclude_uid and summary.get("uid") == exclude_uid:
                    # Per-identity PARTSTAT, keyed by the calendar owner.
                    # First match wins per identity.
                    if owner_lc and owner_lc not in existing_partstats:
                        owner_partstat = self._extract_partstat(ics_text, owner_lc)
                        if owner_partstat is not None:
                            existing_partstats[owner_lc] = owner_partstat
                    continue
                # UIDs are used only for the self-exclusion filter above —
                # they can carry internal routing info (incident IDs, etc.)
                # so don't leak them to the API client.
                summary.pop("uid", None)
                conflicts.append(summary)
        return {"conflicts": conflicts, "existing_partstats": existing_partstats}

    @staticmethod
    def _extract_partstat(ics_text, attendee_email_lc):
        """Return PARTSTAT of ``attendee_email_lc`` (lowercased) in ``ics_text``.

        Returns ``None`` if the event is unparseable or the attendee
        is absent — callers should treat ``None`` as "no prior RSVP".
        """
        try:
            cal = ICalendar.from_ical(ics_text)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        for comp in cal.walk("VEVENT"):
            attendees = comp.get("ATTENDEE")
            if attendees is None:
                continue
            if not isinstance(attendees, list):
                attendees = [attendees]
            for att in attendees:
                addr = str(att).strip().lower()
                if addr.startswith("mailto:"):
                    addr = addr[len("mailto:") :]
                if addr == attendee_email_lc:
                    val = att.params.get("PARTSTAT")
                    return str(val) if val else None
        return None

    def _calendar_query(self, calendar_url, start, end):
        body = (
            '<?xml version="1.0"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><c:calendar-data/></d:prop>"
            "<c:filter>"
            '<c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            f'<c:time-range start="{_format_utc(start)}" end="{_format_utc(end)}"/>'
            "</c:comp-filter>"
            "</c:comp-filter>"
            "</c:filter>"
            "</c:calendar-query>"
        )
        resp = self._request(
            "REPORT",
            calendar_url,
            data=body.encode("utf-8"),
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
            },
        )
        root = ET.fromstring(resp.text)
        data_key = f".//{_q(CALDAV_NS, 'calendar-data')}"
        return [
            data
            for r in root.findall(_q(DAV_NS, "response"))
            if (data := r.findtext(data_key))
        ]

    def _calendar_query_detailed(self, calendar_url, start, end):
        """Like ``_calendar_query`` but yields ``(href, etag, calendar_data)``.

        The href is the event's real resource path and the etag its version —
        both needed to write back to the right resource with If-Match.
        """
        body = (
            '<?xml version="1.0"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            "<c:filter>"
            '<c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            f'<c:time-range start="{_format_utc(start)}" end="{_format_utc(end)}"/>'
            "</c:comp-filter>"
            "</c:comp-filter>"
            "</c:filter>"
            "</c:calendar-query>"
        )
        resp = self._request(
            "REPORT",
            calendar_url,
            data=body.encode("utf-8"),
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
            },
        )
        root = ET.fromstring(resp.text)
        data_key = f".//{_q(CALDAV_NS, 'calendar-data')}"
        etag_key = f".//{_q(DAV_NS, 'getetag')}"
        out = []
        for r in root.findall(_q(DAV_NS, "response")):
            href = r.findtext(_q(DAV_NS, "href"))
            data = r.findtext(data_key)
            if href and data:
                # REPORT hrefs are relative to the request URI; resolve against
                # the queried calendar URL (absolute hrefs pass through unchanged)
                # so the follow-up GET/PUT hits the right resource.
                resource_url = urljoin(calendar_url, href.strip())
                out.append((resource_url, (r.findtext(etag_key) or "").strip(), data))
        return out

    def _put_resource(self, resource_url, ics_data, etag=None):
        """PUT to an existing resource URL, optionally If-Match ``etag``.

        Raises ``CalDAVError(status_code=412)`` when the precondition fails.
        """
        data = ics_data.encode("utf-8") if isinstance(ics_data, str) else ics_data
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if etag:
            headers["If-Match"] = etag
        self._request("PUT", resource_url, data=data, headers=headers)

    def _get_resource(self, resource_url):
        """Return ``(ics_text, etag)`` for a resource, or ``(None, None)`` if gone."""
        try:
            resp = self._request(
                "GET", resource_url, headers={"Accept": "text/calendar"}
            )
        except CalDAVError as exc:
            if exc.status_code == 404:
                return None, None
            raise
        return resp.text, (resp.headers.get("ETag") or "").strip()

    @staticmethod
    def _summarize_event(ics_text, calendar_name):
        try:
            cal = ICalendar.from_ical(ics_text)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Could not parse conflicting event", exc_info=True)
            return None
        for comp in cal.walk("VEVENT"):
            dtstart = comp.get("DTSTART")
            dtend = comp.get("DTEND")
            uid = comp.get("UID")
            # All-day events carry a ``date`` (not ``datetime``) — surface
            # the distinction so the UI can format without TZ conversion.
            # ``new Date("2026-06-01")`` in JS parses as midnight UTC, then
            # converts to local time; for users west of UTC the date can
            # display as the day *before*. The ``all_day`` flag lets the
            # client opt into a date-only formatter for those.
            all_day = bool(
                dtstart
                and hasattr(dtstart, "dt")
                and not isinstance(dtstart.dt, datetime)
            )
            return {
                "uid": str(uid) if uid else None,
                "summary": str(comp.get("SUMMARY") or "Untitled event"),
                "start": dtstart.dt.isoformat() if dtstart else None,
                "end": dtend.dt.isoformat() if dtend else None,
                "all_day": all_day,
                "calendar_name": calendar_name,
            }
        return None

    def add_event(self, ics_data, calendar_id=None):
        """Store an event on the selected calendar (or the default one)."""
        cal = ICalendar.from_ical(ics_data)
        cal = rebuild_for_storage(cal)
        # Suppress server-side iTIP REQUEST fan-out. sabre/dav's Schedule
        # plugin (used by suitenumerique/calendars) auto-dispatches one
        # iTIP REQUEST per ATTENDEE on any PUT where the calendar owner is
        # the ORGANIZER — turning a single /add/ call into a mass-mailer.
        # RFC 6638 §7.1 SCHEDULE-AGENT=CLIENT on ORGANIZER tells the server
        # the client owns scheduling, so the server takes no action.
        # /add/ is a personal-calendar copy, not an invitation send, so
        # this is always the right value here. The RSVP path keeps
        # SCHEDULE-AGENT=SERVER so the REPLY reaches the organizer.
        self._set_schedule_agent_client(cal)
        self._put_event(
            self._pick_calendar_url(calendar_id),
            cal.to_ical().decode("utf-8"),
        )
        return True

    def respond_to_event(self, ics_data, response, attendee_email, calendar_id=None):
        """Store an RSVP'd copy of the event on the user's calendar.

        Relies on the CalDAV server's scheduling extension (RFC 6638) to
        dispatch the iTIP REPLY back to the organizer — ORGANIZER keeps
        its default SCHEDULE-AGENT=SERVER, so the broker emits the REPLY
        on PUT. This includes ``DECLINED``: until the organizer removes
        the user from ATTENDEEs, they remain invited, so the canonical
        place to record the decline is a stored copy with
        ``PARTSTAT=DECLINED``. The user can re-accept later by changing
        PARTSTAT on the same event.

        When ``calendar_id`` points to a calendar whose owner principal is
        itself an ATTENDEE on the event, the PARTSTAT update targets that
        owner address rather than ``attendee_email``. This is how a user
        viewing an invite from their *personal* mailbox can RSVP on behalf
        of a *shared mailbox* by picking the mailbox calendar: the iTIP
        REPLY is then sent from the right identity (the mailbox, not the
        personal address). Falls back to ``attendee_email`` when the
        calendar's owner is not on the invite or the CalDAV server doesn't
        expose owner info.

        Raises ``CalDAVError`` if neither candidate is on the ATTENDEE
        list — without that, the iTIP REPLY would never reach the
        organizer (the broker uses ATTENDEE matching to decide who to
        notify) and the user would see a "Response saved" toast for a
        no-op write.
        """
        cal = ICalendar.from_ical(ics_data)
        calendar = self._pick_calendar(calendar_id)

        # Prefer the selected calendar's owner address — that's the identity
        # this RSVP speaks for. Fall back to the mailbox address passed in
        # by the viewset for backends that don't expose owner info.
        candidates = []
        owner = calendar.get("owner_email")
        if owner:
            candidates.append(owner)
        if attendee_email and attendee_email not in candidates:
            candidates.append(attendee_email)

        # Update PARTSTAT on the input first, then rebuild — the rebuild
        # preserves ATTENDEE entries (with our updated PARTSTAT) and
        # drops everything else.
        if not any(
            self._update_partstat(cal, candidate, response) for candidate in candidates
        ):
            raise CalDAVError(
                "Mailbox is not an attendee of this event; "
                "RSVP would not notify the organizer."
            )

        cal = rebuild_for_storage(cal)

        self._put_event(
            calendar["id"],
            cal.to_ical().decode("utf-8"),
        )
        return True

    _REPLY_DTSTAMP_PARAM = "X-STMSG-REPLY-DTSTAMP"

    # How far a recorded reply DTSTAMP may sit in the future (clock skew) before
    # it's clamped — stops a far-future DTSTAMP bricking that attendee's updates.
    _REPLY_DTSTAMP_SKEW = timedelta(minutes=5)

    # Max span of the (attacker-controlled) reply time-range REPORT window.
    _MAX_QUERY_SPAN_DAYS = 366

    @staticmethod
    def _parse_reply(ics_data, attendee_email):
        """Reply fields for the verified attendee → ``(dict, None)``, else
        ``(None, reason)``.

        Reads the PARTSTAT of the ATTENDEE matching ``attendee_email`` (never
        another line), + UID/time/SEQUENCE/DTSTAMP and whether it's a recurrence
        instance. reason is ``unparseable-reply`` (bad ICS or no VEVENT with a
        UID) or ``attendee-mismatch`` (a UID event exists but that attendee has
        no PARTSTAT there).
        """
        target = (attendee_email or "").strip().lower()
        if not target:
            return None, "attendee-mismatch"
        try:
            cal = ICalendar.from_ical(ics_data)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Could not parse inbound iTIP REPLY", exc_info=True)
            return None, "unparseable-reply"
        saw_uid = False
        for comp in cal.walk("VEVENT"):
            uid = comp.get("UID")
            if not uid:
                continue
            saw_uid = True
            attendees = comp.get("ATTENDEE")
            if attendees is None:
                continue
            if not isinstance(attendees, list):
                attendees = [attendees]
            partstat = None
            for att in attendees:
                addr = str(att).strip().lower()
                if addr.startswith("mailto:"):
                    addr = addr[len("mailto:") :]
                if addr == target:
                    ps = att.params.get("PARTSTAT")
                    if ps:
                        partstat = str(ps)
                    break
            if partstat is None:
                continue
            dtstart = comp.get("DTSTART")
            if dtstart is None:
                # RFC 5546 makes DTSTART optional in a REPLY, but in practice
                # every mainstream client echoes it; without it we can't bound
                # the calendar-query REPORT, so reject rather than scan every
                # calendar over an unbounded window. If minimal replies ever turn
                # up in the field, a bounded (~±60 day) fallback is the escape
                # hatch.
                return None, "unparseable-reply"
            dtend = comp.get("DTEND")
            seq = comp.get("SEQUENCE")
            dtstamp = comp.get("DTSTAMP")
            if dtstamp is None:
                # DTSTAMP is required in every iTIP component (RFC 5546). Without
                # it the per-attendee staleness guard can't record anything, so
                # a same-SEQUENCE redelivery could overwrite a newer PARTSTAT.
                return None, "unparseable-reply"
            return {
                "uid": str(uid),
                "attendee": target,
                "partstat": partstat,
                "start": dtstart.dt,
                "end": dtend.dt if dtend is not None else None,
                "sequence": int(seq) if seq is not None else 0,
                "dtstamp": dtstamp.dt,
                "is_recurrence_instance": comp.get("RECURRENCE-ID") is not None,
            }, None
        return None, ("attendee-mismatch" if saw_uid else "unparseable-reply")

    def apply_reply(self, ics_data, attendee_email, organizer_email=None):
        """Apply an inbound iTIP ``METHOD:REPLY`` to the organizer's stored event.

        Mirror of ``respond_to_event``. Only ever modifies the ATTENDEE matching
        ``attendee_email`` (the caller's DMARC-verified sender), reading its
        PARTSTAT from that same entry — so a crafted multi-ATTENDEE payload can't
        move a third party. When ``organizer_email`` is given, only a stored copy
        whose ORGANIZER matches it is updated — so a same-UID copy on which the
        mailbox is merely an ATTENDEE (not the organizer) is skipped. Stamps
        ``SCHEDULE-AGENT=CLIENT`` to stop server-side iTIP re-fanout. Returns
        ``{"applied": bool, "reason": str}``; never raises for expected no-ops.
        """
        reply, reason = self._parse_reply(ics_data, attendee_email)
        if reply is None:
            return {"applied": False, "reason": reason}

        if reply["is_recurrence_instance"]:
            # TODO(itip-recurrence): applying a single-instance REPLY against the
            # master VEVENT (matched by UID) would flip the whole series. No-op.
            logger.info(
                "iTIP REPLY for %s targets a recurrence instance; skipping",
                reply["uid"],
            )
            return {"applied": False, "reason": "recurrence-not-supported"}

        organizer_norm = (organizer_email or "").strip().lower()
        start, end = self._reply_query_window(reply)

        for calendar in self.list_calendars(writable_only=True):
            for resource_url, etag, stored_ics in self._calendar_query_detailed(
                calendar["id"], start, end
            ):
                try:
                    stored = ICalendar.from_ical(stored_ics)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.debug("Skipping unparseable stored event", exc_info=True)
                    continue
                if not self._event_has_uid(stored, reply["uid"]):
                    continue
                # Only touch the copy the mailbox actually organizes — skip a
                # same-UID copy where it's merely an attendee, keep searching.
                if organizer_norm and self._event_organizer(stored) != organizer_norm:
                    continue
                return self._apply_reply_to_resource(
                    resource_url, stored, etag, reply
                )

        logger.info("No stored event matches iTIP REPLY UID %s", reply["uid"])
        return {"applied": False, "reason": "no-matching-event"}

    @staticmethod
    def _event_organizer(cal):
        """Bare (mailto-stripped, lower-cased) ORGANIZER of the first VEVENT."""
        for comp in cal.walk("VEVENT"):
            org = comp.get("ORGANIZER")
            if org is None:
                continue
            addr = str(org).strip().lower()
            if addr.startswith("mailto:"):
                addr = addr[len("mailto:") :]
            return addr
        return ""

    def _apply_reply_to_resource(self, resource_url, stored, etag, reply):
        """Mutate ``stored`` for the reply and conditional-PUT to its resource.

        If-Match on ``etag`` guards the lost-update race (concurrent replies, or
        a reply racing the organizer's own edit). On 412 the resource is
        re-fetched and the mutation re-applied once, then it gives up with
        ``concurrent-modification``.
        """
        if not etag:
            # The REPORT returned no etag; fetch one so the write stays
            # conditional. Never PUT unconditionally — that would silently drop
            # lost-update protection.
            fresh_ics, etag = self._get_resource(resource_url)
            if not etag or fresh_ics is None:
                logger.warning(
                    "No etag for %s; refusing unconditional iTIP write", resource_url
                )
                return {"applied": False, "reason": "no-etag"}
            try:
                stored = ICalendar.from_ical(fresh_ics)
            except Exception:  # pylint: disable=broad-exception-caught
                return {"applied": False, "reason": "no-etag"}
        for attempt in (1, 2):
            stale = self._reply_is_stale(stored, reply)
            if stale:
                return {"applied": False, "reason": stale}
            if not self._update_partstat(
                stored, reply["attendee"], reply["partstat"]
            ):
                return {"applied": False, "reason": "attendee-not-on-event"}
            self._record_reply_dtstamp(stored, reply)
            self._set_schedule_agent_client(stored)
            body = rebuild_for_storage(stored).to_ical().decode("utf-8")
            try:
                self._put_resource(resource_url, body, etag)
                return {"applied": True, "reason": "applied"}
            except CalDAVError as exc:
                if exc.status_code != 412:
                    raise
                if attempt == 2:
                    return {"applied": False, "reason": "concurrent-modification"}
                fresh_ics, etag = self._get_resource(resource_url)
                if fresh_ics is None:
                    return {"applied": False, "reason": "concurrent-modification"}
                try:
                    stored = ICalendar.from_ical(fresh_ics)
                except Exception:  # pylint: disable=broad-exception-caught
                    return {"applied": False, "reason": "concurrent-modification"}
        return {"applied": False, "reason": "concurrent-modification"}

    @staticmethod
    def _event_has_uid(cal, uid):
        for comp in cal.walk("VEVENT"):
            if str(comp.get("UID") or "") == uid:
                return True
        return False

    def _reply_query_window(self, reply):
        """(start, end) TZ-aware UTC window (± a day) around the reply's event
        time. DTSTART is guaranteed present by ``_parse_reply``.

        DTSTART/DTEND are attacker-controlled, so the window is normalized
        (inverted ranges collapsed), the span capped, and the ± day padding
        clamped to safe absolute bounds to avoid datetime over/underflow.
        """
        start = self._as_utc(reply["start"])
        end = self._as_utc(reply["end"] or reply["start"])
        if end < start:
            end = start
        max_span = timedelta(days=self._MAX_QUERY_SPAN_DAYS)
        if end - start > max_span:
            end = start + max_span
        floor = datetime(1, 1, 2, tzinfo=timezone.utc)
        ceil = datetime(9999, 12, 30, tzinfo=timezone.utc)
        start = max(min(start, ceil), floor) - timedelta(days=1)
        end = min(max(end, floor), ceil) + timedelta(days=1)
        return start, end

    @staticmethod
    def _as_utc(dt):
        if not isinstance(dt, datetime):
            dt = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _reply_is_stale(self, stored, reply):
        """Stale-reason string if the REPLY is older than any stored VEVENT
        (SEQUENCE, then per-attendee DTSTAMP), else None.

        All components are evaluated so a multi-VEVENT object isn't order-
        dependent. The DTSTAMP guard only protects against replay when the prior
        state was recorded by this path; a DKIM replay of an old signed reply can
        still land when the newer state arrived via another channel.
        """
        reply_seq = reply["sequence"]
        reply_stamp = reply["dtstamp"]
        for comp in stored.walk("VEVENT"):
            stored_seq = comp.get("SEQUENCE")
            stored_seq = int(stored_seq) if stored_seq is not None else 0
            if reply_seq < stored_seq:
                return "stale-sequence"
            if reply_seq == stored_seq:
                last = self._stored_reply_dtstamp(comp, reply["attendee"])
                if (
                    last is not None
                    and reply_stamp is not None
                    and self._as_utc(reply_stamp) < self._as_utc(last)
                ):
                    return "stale-dtstamp"
        return None

    @classmethod
    def _stored_reply_dtstamp(cls, comp, attendee_email):
        """Last-applied REPLY DTSTAMP for ``attendee_email`` on this VEVENT."""
        attendees = comp.get("ATTENDEE")
        if attendees is None:
            return None
        if not isinstance(attendees, list):
            attendees = [attendees]
        email_lower = attendee_email.lower()
        for att in attendees:
            addr = str(att).strip().lower()
            if addr.startswith("mailto:"):
                addr = addr[len("mailto:") :]
            if addr != email_lower:
                continue
            raw = att.params.get(cls._REPLY_DTSTAMP_PARAM)
            if not raw:
                return None
            try:
                return datetime.strptime(str(raw), "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                return None
        return None

    @classmethod
    def _record_reply_dtstamp(cls, stored, reply):
        """Stamp the REPLY's DTSTAMP onto the attendee for future staleness checks."""
        if reply["dtstamp"] is None:
            return
        # Clamp to now + skew so a far-future DTSTAMP can't permanently mark this
        # attendee's later replies as stale.
        ceiling = datetime.now(timezone.utc) + cls._REPLY_DTSTAMP_SKEW
        stamp = min(cls._as_utc(reply["dtstamp"]), ceiling).strftime("%Y%m%dT%H%M%SZ")
        email_lower = reply["attendee"].lower()
        for comp in stored.walk("VEVENT"):
            attendees = comp.get("ATTENDEE")
            if attendees is None:
                continue
            if not isinstance(attendees, list):
                attendees = [attendees]
            for att in attendees:
                addr = str(att).strip().lower()
                if addr.startswith("mailto:"):
                    addr = addr[len("mailto:") :]
                if addr == email_lower:
                    att.params[cls._REPLY_DTSTAMP_PARAM] = stamp

    @staticmethod
    def _set_schedule_agent_client(cal):
        """Stamp ``SCHEDULE-AGENT=CLIENT`` on every ORGANIZER (RFC 6638 §7.1).

        Tells the CalDAV server's scheduling extension that the client is
        handling iTIP delivery itself, so the server MUST NOT auto-dispatch
        REQUEST/REPLY/CANCEL messages on this PUT. Used by ``add_event``
        to keep "save a personal copy of this invite" from turning into a
        mass-mailer via the server's scheduling plugin.

        **Call only from import paths.** The RSVP path
        (``respond_to_event``) *wants* the server to dispatch the iTIP
        REPLY back to the organizer, so it must NOT call this helper —
        flipping SCHEDULE-AGENT to CLIENT there would silently suppress
        decline/accept notifications while leaving the PUT itself
        successful (and the user's success toast unchanged). The
        ``test_rsvp_stores_sanitized_copy`` regression test pins the
        invariant; do not relax it without replacing the notification
        channel.
        """
        for comp in cal.walk("VEVENT"):
            organizer = comp.get("ORGANIZER")
            if organizer is not None:
                organizer.params["SCHEDULE-AGENT"] = "CLIENT"

    @staticmethod
    def _update_partstat(cal, attendee_email, new_partstat):
        """Update PARTSTAT (and drop RSVP=TRUE) for the given attendee, in-place.

        Returns True if at least one matching ATTENDEE was updated, False
        otherwise. The boolean lets callers distinguish "RSVP recorded"
        from "no-op write" — for ``respond_to_event``, a False result
        means the iTIP REPLY would never reach the organizer.
        """
        email_lower = attendee_email.lower()
        updated = False
        for comp in cal.walk("VEVENT"):
            attendees = comp.get("ATTENDEE")
            if attendees is None:
                continue
            if not isinstance(attendees, list):
                attendees = [attendees]
            for att in attendees:
                addr = str(att).strip().lower()
                if addr.startswith("mailto:"):
                    addr = addr[len("mailto:") :]
                if addr != email_lower:
                    continue
                att.params["PARTSTAT"] = new_partstat
                att.params.pop("RSVP", None)
                updated = True
        return updated

    def _pick_calendar(self, calendar_id):
        """Resolve ``calendar_id`` to the matching calendar dict.

        Used by both add_event (needs the URL) and respond_to_event (also
        needs ``owner_email`` to know which identity the RSVP speaks for).
        Returns the full calendar dict from ``list_calendars(writable_only=True)``.
        """
        if calendar_id and not self._same_origin(calendar_id):
            raise CalDAVError(
                "Calendar URL host does not match the configured CalDAV server."
            )
        # Filter to writable calendars: PUT to a read-only share would fail
        # at the server anyway, and accepting a read-only id here turned
        # the writeability filter on the list endpoint into UX polish
        # rather than an enforced precondition. Now the two paths agree.
        calendars = self.list_calendars(writable_only=True)
        if not calendars:
            raise CalDAVError("No writable calendars available on this CalDAV server.")
        if calendar_id:
            for cal in calendars:
                if cal["id"] == calendar_id:
                    return cal
            raise CalDAVError("Calendar is not in this user's writable calendar list.")
        return calendars[0]

    def _pick_calendar_url(self, calendar_id):
        return self._pick_calendar(calendar_id)["id"]

    def _same_origin(self, candidate_url):
        """Whether ``candidate_url`` shares scheme + host + port with ``self.url``.

        Normalizes default ports so ``https://host/`` and
        ``https://host:443/`` compare equal — otherwise the SSRF guard
        falsely rejects legit deployments where the configured URL and
        server-returned hrefs disagree on whether to include the port.
        """
        cand = urlparse(candidate_url)
        base = urlparse(self.url)
        if not cand.scheme or not cand.hostname:
            return False
        defaults = {"http": 80, "https": 443}

        def _norm(parsed):
            scheme = parsed.scheme.lower()
            try:
                port = parsed.port
            except ValueError:
                # Malformed port (e.g. non-numeric) → treat as no match.
                return None
            return (scheme, parsed.hostname.lower(), port or defaults.get(scheme))

        return _norm(cand) is not None and _norm(cand) == _norm(base)

    def _put_event(self, calendar_url, ics_data):
        uid = ""
        try:
            cal = ICalendar.from_ical(ics_data)
            for comp in cal.walk("VEVENT"):
                uid = str(comp.get("UID") or "")
                break
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Could not extract UID from ICS, using random", exc_info=True)
        # UID comes from attacker-controlled ICS data — reject path
        # separators, traversal sequences and CRLF/NUL before percent-encoding
        # so the event URL cannot escape the calendar collection.
        if uid and (any(c in uid for c in "/\\\r\n\x00") or ".." in uid):
            uid = ""
        if not uid:
            uid = str(uuid.uuid4())

        event_url = calendar_url.rstrip("/") + "/" + quote(uid, safe="") + ".ics"
        data = ics_data.encode("utf-8") if isinstance(ics_data, str) else ics_data
        self._request(
            "PUT",
            event_url,
            data=data,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )

    @classmethod
    def from_channel(cls, channel):
        """Create a CalDAVService from a Channel model instance.

        TODO(caldav-per-channel): there is no DRF write path for this yet.
        ``ChannelSerializer.RESERVED_SETTINGS_KEYS`` rejects
        ``username``/``password`` in plaintext ``settings``, and
        ``encrypted_settings`` is not a serializer field — so the only
        way to provision per-mailbox CalDAV credentials today is via the
        Django admin / management commands / test factories. The code
        path is kept live so the test suite exercises it and so the
        future write path is a small, well-scoped change rather than a
        new feature.

        Reads non-secret config from ``channel.settings``:
          - ``url`` — CalDAV server URL.
        Reads secrets from ``channel.encrypted_settings``:
          - ``username`` — Basic Auth user.
          - ``password`` — Basic Auth password.

        Storing credentials in ``settings`` (the plaintext JSONField) is
        rejected at the serializer layer; the secrets MUST live in
        ``encrypted_settings`` so a DB read does not surface them.
        """
        settings = channel.settings or {}
        secrets = channel.encrypted_settings or {}
        url = settings.get("url")
        if not url:
            raise ValueError("CalDAV channel is missing 'url' in settings.")
        # Per-channel URLs are user-supplied (channel configured by a
        # mailbox admin via Django admin). Opt into the SSRF-pinned
        # session: ``validate_hostname`` at session-creation time rejects
        # private/loopback/metadata IPs, and the ``SSRFProtectedAdapter``
        # pins the resolved IP per request to defeat DNS-rebinding —
        # neither check applies to ``from_instance_config`` because
        # operator-supplied env vars are trusted (they may legitimately
        # point at a private CalDAV instance on the same network).
        return cls(
            url=url,
            username=secrets.get("username") or "",
            password=secrets.get("password") or "",
            ssrf_protect=True,
        )

    @classmethod
    def from_instance_config(cls, username):
        """Create a CalDAVService from the deployment-default CalDAV config.

        Used when no per-mailbox Channel overrides the integration (users
        can point a Channel at any CalDAV provider; see
        ``from_channel_or_instance``).

        Authenticates with HTTP Basic Auth: ``username`` is the requesting
        user's *OIDC identity email* (``User.email``) — NOT the mailbox
        address. The companion CalDAV provider (suitenumerique/calendars)
        keys principals on the OIDC email claim, and provisions a
        principal on first request, so the right addressing identity is
        the human's OIDC email even when they are acting on a mailbox
        whose ``local_part@domain.name`` differs.

        The password is the single ``CALDAV_DEFAULT_PASSWORD`` value — at
        the protocol level it is just an HTTP Basic password, but the same
        value is sent for every user, so it effectively authenticates
        messages-as-a-service rather than any individual user.

        Trust model: see the comment block on ``CALDAV_DEFAULT_PASSWORD``
        in ``messages/settings.py``. In short: the CalDAV server trusts
        whichever email messages claims to act as, so the load-bearing
        safety property is that the OIDC identity provider does not let
        one human assert another human's email claim.
        """
        url = django_settings.CALDAV_DEFAULT_URL
        password = django_settings.CALDAV_DEFAULT_PASSWORD
        if not url or not password:
            raise ValueError(
                "Instance-level CalDAV is not configured "
                "(CALDAV_DEFAULT_URL and CALDAV_DEFAULT_PASSWORD are required)."
            )
        return cls(url=url, username=username, password=password)

    @classmethod
    def from_channel_or_instance(cls, channel, username):
        """Prefer a per-mailbox Channel, falling back to the default config.

        The per-mailbox path lets users override the integration to point
        at a CalDAV provider of their choice with credentials they own.
        The default path (``CALDAV_DEFAULT_*`` env vars) is the
        deployment-wide fallback — see ``from_instance_config`` for its
        trust model.

        ``username`` is the requesting user's OIDC identity email; it is
        only used by the default path (per-channel credentials are
        self-contained). See ``from_instance_config`` for why it must
        be the OIDC email rather than the mailbox address.
        Returns None if neither is available.
        """
        # TODO(caldav-per-channel): no DRF write path exists for CalDAV
        # channels yet (see ``from_channel``). In practice this branch is
        # only reached via admin/management/factory-provisioned rows.
        if channel:
            return cls.from_channel(channel)

        if (
            django_settings.CALDAV_DEFAULT_URL
            and django_settings.CALDAV_DEFAULT_PASSWORD
        ):
            return cls.from_instance_config(username)

        return None
