"""Parse the ICS of an email without its time zones.

icalendar keeps the first VTIMEZONE that it sees for each unknown TZID, for
the life of the process. A Windows name such as ``Romance Standard Time`` is
unknown, and Outlook uses these names. One email can thus change how all the
later emails of the worker read their times, or make them hang with a rule
that repeats each second.
"""

from icalendar import Calendar as ICalendar
from icalendar.parser import Contentlines
from icalendar.timezone import tzp

_VTIMEZONE = "VTIMEZONE"


def parse_untrusted_ics(text):
    """Parse ``text`` without its VTIMEZONE components.

    A TZID then resolves only from the IANA and Windows names that icalendar
    knows. An unknown TZID gives a floating time.

    Raises the errors of ``Calendar.from_ical``.
    """
    # Another parse in this process can have kept a time zone of an email.
    tzp.use_default()
    kept = []
    depth = 0
    for line in Contentlines.from_ical(text):
        if not line:
            continue
        try:
            name, _params, value = line.parts()
        except ValueError:
            if not depth:
                kept.append(line)
            continue
        name = name.upper()
        is_vtimezone = value.upper() == _VTIMEZONE
        if depth:
            # Count the components as the parser does, whatever their names.
            depth += {"BEGIN": 1, "END": -1}.get(name, 0)
        elif name == "BEGIN" and is_vtimezone:
            depth = 1
        # The parser keeps a time zone when it reads END:VTIMEZONE.
        elif not (name == "END" and is_vtimezone):
            kept.append(line)
    return ICalendar.from_ical("\r\n".join(kept) + "\r\n")
