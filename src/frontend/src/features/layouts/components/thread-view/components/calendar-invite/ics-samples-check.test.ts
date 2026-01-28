/**
 * Smoke tests: parse real-world ICS samples and verify our helpers don't crash.
 * This file is used to discover bugs, then the failing cases are added as
 * anonymised reproduction tests in index.test.ts.
 */
import { convertIcsCalendar } from "ts-ics";
import {
    getEventEnd,
    isAllDayEvent,
    formatEventDateRange,
    formatRecurrenceRule,
    getAttendeeStatusInfo,
    createContactFromAttendee,
} from "./calendar-helper";

const t = (key: string, options?: Record<string, unknown>): string => {
    let result = key;
    if (options) {
        for (const [k, v] of Object.entries(options)) {
            result = result.replace(`{{${k}}}`, String(v));
        }
    }
    return result;
};

// ─── Sample 1: Multiple events, VALARM, TZID without VTIMEZONE ──────────────
const SAMPLE_MULTI_EVENT = `BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
BEGIN:VEVENT
SUMMARY:Access-A-Ride Pickup
DTSTART;TZID=America/New_York:20130802T103400
DTEND;TZID=America/New_York:20130802T110400
LOCATION:1000 Broadway Ave.\\, Brooklyn
DESCRIPTION: Access-A-Ride to 900 Jay St.\\, Brooklyn
STATUS:CONFIRMED
SEQUENCE:3
BEGIN:VALARM
TRIGGER:-PT10M
DESCRIPTION:Pickup Reminder
ACTION:DISPLAY
END:VALARM
END:VEVENT
BEGIN:VEVENT
SUMMARY:Access-A-Ride Return
DTSTART;TZID=America/New_York:20130802T200000
DTEND;TZID=America/New_York:20130802T203000
LOCATION:900 Jay St.\\, Brooklyn
DESCRIPTION: Access-A-Ride to 1000 Broadway Ave.\\, Brooklyn
STATUS:CONFIRMED
SEQUENCE:3
BEGIN:VALARM
TRIGGER:-PT10M
DESCRIPTION:Pickup Reminder
ACTION:DISPLAY
END:VALARM
END:VEVENT
END:VCALENDAR`;

// ─── Sample 2: VTIMEZONE + VALARM (Mozilla) ─────────────────────────────────
const SAMPLE_VTIMEZONE = `BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Mozilla.org/NONSGML Mozilla Calendar V1.1//EN
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:unique_id@stubegru.org
DTSTART;TZID=Europe/Berlin:20191204T120000
DTEND;TZID=Europe/Berlin:20191204T154500
SUMMARY:Hiwi Sitzung
DESCRIPTION:A very important meeting by all important people.
DTSTAMP:20190401T125230Z
CREATED:20180913T081139Z
LAST-MODIFIED:20180913T081140Z
STATUS:CONFIRMED
LOCATION:Town Hall
SEQUENCE:0
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER;VALUE=DURATION:-PT30M
DESCRIPTION:This is an event reminder
END:VALARM
END:VEVENT
END:VCALENDAR`;

// ─── Sample 3: METHOD:REQUEST with ATTENDEE/ORGANIZER ────────────────────────
const SAMPLE_METHOD_REQUEST = `BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:REQUEST
BEGIN:VEVENT
UID:unique-id@site.com
DTSTAMP:20210605T073803Z
DTSTART;TZID=America/Guayaquil:20210614T030000
DTEND;TZID=America/Guayaquil:20210614T040000
SUMMARY:My Event
ORGANIZER;CN="Juan Perez":mailto:jperez@organizer.com
ATTENDEE;PARTSTAT=ACCEPTED;CN="Jane Doe";EMAIL=jdoe@gmail.com:MAILTO:jdoe@gmail.com
URL;VALUE=URI:https://example.com/event/123
END:VEVENT
END:VCALENDAR`;

// ─── Sample 4: METHOD:CANCEL ─────────────────────────────────────────────────
const SAMPLE_METHOD_CANCEL = `BEGIN:VCALENDAR
PRODID:-//Events Calendar//iCal4j 1.0//EN
CALSCALE:GREGORIAN
VERSION:2.0
METHOD:CANCEL
BEGIN:VEVENT
DTSTAMP:20200805T194909Z
DTSTART:20200805T194909Z
DTEND:20200805T200409Z
SUMMARY:Telehealth visit
UID:82aaf1aa-9522-4000-8a29-084c5a4762d5
ATTENDEE;ROLE=REQ-PARTICIPANT;CN=John Doe:mailto:john@example.com
ORGANIZER;ROLE=REQ-PARTICIPANT;CN=Jane Smith:mailto:jane@example.com
URL:https://www.example.com
LOCATION:https://www.example.com
END:VEVENT
END:VCALENDAR`;

// ─── Sample 5: Microsoft Exchange with X-MICROSOFT extensions ────────────────
const SAMPLE_EXCHANGE = `BEGIN:VCALENDAR
METHOD:REQUEST
PRODID:Microsoft Exchange Server 2010
VERSION:2.0
BEGIN:VTIMEZONE
TZID:Romance Standard Time
BEGIN:STANDARD
DTSTART:16010101T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=10
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:16010101T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;INTERVAL=1;BYDAY=-1SU;BYMONTH=3
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
ORGANIZER;CN=Miquel:mailto:someuser@hotmail.com
ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE;CN=Miquel:mailto:invited@example.com
DESCRIPTION;LANGUAGE=es-MX:\\n
UID:040000008200E00074C5B7101A82E00800000000B014D4F3C93CD801000000000000000010000000A891E37E3E7D4844B5F7D6DE29A9F9BE
SUMMARY;LANGUAGE=es-MX:Test resposta
DTSTART;TZID=Romance Standard Time:20231019T100000
DTEND;TZID=Romance Standard Time:20231019T103000
CLASS:PUBLIC
PRIORITY:5
DTSTAMP:20231018T161542Z
TRANSP:OPAQUE
STATUS:CONFIRMED
SEQUENCE:0
LOCATION;LANGUAGE=es-MX:
X-MICROSOFT-CDO-APPT-SEQUENCE:0
X-MICROSOFT-CDO-BUSYSTATUS:TENTATIVE
X-MICROSOFT-CDO-ALLDAYEVENT:FALSE
BEGIN:VALARM
DESCRIPTION:REMINDER
TRIGGER;RELATED=START:-PT15M
ACTION:DISPLAY
END:VALARM
END:VEVENT
END:VCALENDAR`;

// ─── Sample 6: Google Calendar with multiple VALARMs ─────────────────────────
const SAMPLE_GOOGLE = `BEGIN:VCALENDAR
PRODID:-//Google Inc//Google Calendar 70.9054//EN
VERSION:2.0
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Test Calendar
X-WR-TIMEZONE:Europe/London
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTART:20241004T181500Z
DTEND:20241004T190000Z
DTSTAMP:20241002T074726Z
UID:79fs7pkqvht9m5igs0vjv1sfra@google.com
CREATED:20241002T074656Z
LAST-MODIFIED:20241002T074656Z
SEQUENCE:0
STATUS:CONFIRMED
SUMMARY:event with alarms
TRANSP:OPAQUE
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:This is an event reminder
TRIGGER:-PT10M
END:VALARM
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:This is an event reminder
TRIGGER:-PT14M
END:VALARM
END:VEVENT
END:VCALENDAR`;

// ─── Sample 7: RRULE with EXDATE ────────────────────────────────────────────
const SAMPLE_RRULE_EXDATE = `BEGIN:VCALENDAR
VERSION:2.0
PRODID:icalendar-ruby
CALSCALE:GREGORIAN
BEGIN:VEVENT
DTSTAMP:20161206T084106Z
UID:1a5d1a84-edd8-4d0d-b93e-0cac9497c091
DTSTART:20160101T120000Z
DTEND:20160101T160000Z
SUMMARY:Each day from 1 Jan until 31 Jan except 10 20 30
RRULE:FREQ=DAILY;COUNT=30;INTERVAL=1
EXDATE:20160110T120000Z
EXDATE:20160120T120000Z
EXDATE:20160130T120000Z
END:VEVENT
END:VCALENDAR`;

// ─── Sample 8: DURATION instead of DTEND ─────────────────────────────────────
const SAMPLE_DURATION = `BEGIN:VCALENDAR
VERSION:2.0
CALSCALE:GREGORIAN
PRODID:adamgibbons/ics
METHOD:PUBLISH
X-PUBLISHED-TTL:PT1H
BEGIN:VEVENT
UID:S8h0Vj7mTB74p9vt5pQzJ
SUMMARY:Bolder Boulder
DTSTAMP:20181017T204900Z
DTSTART:20180530T043000Z
DESCRIPTION:Annual 10-kilometer run in Boulder\\, Colorado
URL:http://www.bolderboulder.com/
GEO:40.0095;105.2669
LOCATION:Folsom Field\\, University of Colorado (finish line)
STATUS:CONFIRMED
CATEGORIES:10k races,Memorial Day Weekend,Boulder CO
ORGANIZER;CN=Admin:mailto:Race@BolderBOULDER.com
ATTENDEE;RSVP=TRUE;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;CN=Adam Gibbons:mailto:adam@example.com
ATTENDEE;RSVP=FALSE;ROLE=OPT-PARTICIPANT;CN=Brittany Seaton:mailto:brittany@example2.org
DURATION:PT6H30M
END:VEVENT
END:VCALENDAR`;

// ─── The actual tests ────────────────────────────────────────────────────────

describe("real-world ICS samples", () => {
    describe("Sample 1: Multiple events with TZID", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_MULTI_EVENT);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
        });

        it("should have 2 events", () => {
            expect(parsed.events).toHaveLength(2);
        });

        it("should have correct summaries", () => {
            expect(parsed.events![0].summary).toBe("Access-A-Ride Pickup");
            expect(parsed.events![1].summary).toBe("Access-A-Ride Return");
        });

        it("should parse escaped commas in location", () => {
            expect(parsed.events![0].location).toContain("Brooklyn");
        });

        it("should parse status", () => {
            expect(parsed.events![0].status).toBe("CONFIRMED");
        });

        it("should format date range without error", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            const result = formatEventDateRange(event.start.date, end, "en-US");
            expect(result).toBeTruthy();
            expect(result).toContain("–");
        });
    });

    describe("Sample 2: VTIMEZONE with timezone-aware dates", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_VTIMEZONE);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
            expect(parsed.events).toHaveLength(1);
        });

        it("should parse timezone-aware event", () => {
            const event = parsed.events![0];
            expect(event.summary).toBe("Hiwi Sitzung");
            expect(event.location).toBe("Town Hall");
        });

        it("should have valid start/end dates", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            expect(end).toBeDefined();
            expect(event.start.date.getTime()).toBeLessThan(end!.getTime());
        });

        it("should not detect as all-day", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            expect(isAllDayEvent(event.start.date, end)).toBe(false);
        });

        it("should format in German locale", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            const result = formatEventDateRange(event.start.date, end, "de-DE");
            expect(result).toBeTruthy();
            expect(result).toContain("Dezember");
        });
    });

    describe("Sample 3: METHOD:REQUEST with attendees", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_METHOD_REQUEST);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
        });

        it("should detect REQUEST method", () => {
            expect(parsed.method).toBe("REQUEST");
        });

        it("should parse organizer", () => {
            const event = parsed.events![0];
            expect(event.organizer).toBeDefined();
            expect(event.organizer!.email).toBe("jperez@organizer.com");
            expect(event.organizer!.name).toBe("Juan Perez");
        });

        it("should create valid contact from organizer", () => {
            const event = parsed.events![0];
            const contact = createContactFromAttendee(event.organizer!);
            expect(contact.email).toBe("jperez@organizer.com");
            expect(contact.name).toBe("Juan Perez");
            expect(contact.id).toContain("jperez@organizer.com");
        });

        it("should parse attendees", () => {
            const event = parsed.events![0];
            expect(event.attendees).toBeDefined();
            expect(event.attendees!.length).toBeGreaterThanOrEqual(1);
        });

        it("should get attendee status info", () => {
            const event = parsed.events![0];
            const attendee = event.attendees![0];
            const info = getAttendeeStatusInfo(attendee.partstat, t);
            expect(info.icon).toBeDefined();
            expect(info.label).toBeDefined();
            expect(info.className).toBeDefined();
        });
    });

    describe("Sample 4: METHOD:CANCEL", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_METHOD_CANCEL);
        });

        it("should detect CANCEL method", () => {
            expect(parsed.method).toBe("CANCEL");
        });

        it("should still have event data", () => {
            expect(parsed.events).toHaveLength(1);
            expect(parsed.events![0].summary).toBe("Telehealth visit");
        });

        it("should parse organizer and attendee", () => {
            const event = parsed.events![0];
            expect(event.organizer).toBeDefined();
            expect(event.attendees).toBeDefined();
        });

        it("should have URL as location", () => {
            const event = parsed.events![0];
            expect(event.location).toBe("https://www.example.com");
        });
    });

    describe("Sample 5: Microsoft Exchange", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_EXCHANGE);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
            expect(parsed.events).toHaveLength(1);
        });

        it("should detect REQUEST method", () => {
            expect(parsed.method).toBe("REQUEST");
        });

        it("should parse event with X-MICROSOFT extensions", () => {
            const event = parsed.events![0];
            expect(event.summary).toBeTruthy();
            expect(event.status).toBe("CONFIRMED");
        });

        it("should parse NEEDS-ACTION attendee", () => {
            const event = parsed.events![0];
            expect(event.attendees).toBeDefined();
            const info = getAttendeeStatusInfo(event.attendees![0].partstat, t);
            expect(info.label).toBe("Awaiting response");
            expect(info.className).toContain("pending");
        });

        it("should handle empty location gracefully", () => {
            const event = parsed.events![0];
            // Location may be empty string or undefined
            if (event.location) {
                const formatted = formatEventDateRange(event.start.date, getEventEnd(event), "en-US");
                expect(formatted).toBeTruthy();
            }
        });
    });

    describe("Sample 6: Google Calendar with alarms", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_GOOGLE);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
            expect(parsed.events).toHaveLength(1);
        });

        it("should detect PUBLISH method", () => {
            expect(parsed.method).toBe("PUBLISH");
        });

        it("should parse event with alarms", () => {
            const event = parsed.events![0];
            expect(event.summary).toBe("event with alarms");
            expect(event.alarms).toBeDefined();
            expect(event.alarms!.length).toBeGreaterThanOrEqual(2);
        });

        it("should format date range correctly", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            expect(end).toBeDefined();
            const result = formatEventDateRange(event.start.date, end, "en-US");
            expect(result).toContain("–");
        });
    });

    describe("Sample 7: RRULE with EXDATE", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_RRULE_EXDATE);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
            expect(parsed.events).toHaveLength(1);
        });

        it("should parse recurrence rule", () => {
            const event = parsed.events![0];
            expect(event.recurrenceRule).toBeDefined();
            expect(event.recurrenceRule!.frequency).toBe("DAILY");
            expect(event.recurrenceRule!.count).toBe(30);
        });

        it("should format recurrence rule", () => {
            const event = parsed.events![0];
            const result = formatRecurrenceRule(event.recurrenceRule!, t, "en-US");
            expect(result).toBe("Daily · 30 occurrences");
        });

        it("should parse exception dates", () => {
            const event = parsed.events![0];
            expect(event.exceptionDates).toBeDefined();
        });
    });

    describe("Sample 8: DURATION instead of DTEND", () => {
        let parsed: ReturnType<typeof convertIcsCalendar>;

        beforeAll(() => {
            parsed = convertIcsCalendar(undefined, SAMPLE_DURATION);
        });

        it("should parse without error", () => {
            expect(parsed).toBeDefined();
            expect(parsed.events).toHaveLength(1);
        });

        it("should have duration, not end", () => {
            const event = parsed.events![0];
            expect(event.duration).toBeDefined();
        });

        it("should compute correct end via getEventEnd", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            expect(end).toBeDefined();
            // DTSTART:20180530T043000Z + PT6H30M = 20180530T110000Z
            expect(end!.getUTCHours()).toBe(11);
            expect(end!.getUTCMinutes()).toBe(0);
        });

        it("should not be detected as all-day", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            expect(isAllDayEvent(event.start.date, end)).toBe(false);
        });

        it("should format date range correctly", () => {
            const event = parsed.events![0];
            const end = getEventEnd(event);
            const result = formatEventDateRange(event.start.date, end, "en-US");
            expect(result).toContain("–");
            expect(result).toContain("May");
        });

        it("should parse multiple attendees with different roles", () => {
            const event = parsed.events![0];
            expect(event.attendees).toBeDefined();
            expect(event.attendees!.length).toBe(2);
        });

        it("should parse organizer", () => {
            const event = parsed.events![0];
            expect(event.organizer).toBeDefined();
            expect(event.organizer!.name).toBe("Admin");
        });

        it("should parse location with escaped comma", () => {
            const event = parsed.events![0];
            expect(event.location).toBeTruthy();
        });

        it("should create valid contacts from all attendees", () => {
            const event = parsed.events![0];
            for (const attendee of event.attendees!) {
                const contact = createContactFromAttendee(attendee);
                expect(contact.email).toBeTruthy();
                expect(contact.id).toContain("calendar-");
            }
        });
    });
});
