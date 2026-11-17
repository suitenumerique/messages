import { convertIcsCalendar, IcsEvent, IcsRecurrenceRule } from "ts-ics";

import {
    collapseRecurringEvents,
    formatEventDateRange,
    formatRecurrenceRule,
    getEventEnd,
    getEventStart,
    validDate,
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

// ts-ics turns unparseable dates into ``Invalid Date`` instead of throwing;
// formatting one (``toISOString``, ``Intl.DateTimeFormat.format``) throws a
// RangeError. Invites come from the sender.
function parseVevent(lines: string): IcsEvent {
    const ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Test//Test//EN",
        "BEGIN:VEVENT",
        "UID:bad-date@example.com",
        "DTSTAMP:20250101T000000Z",
        "SUMMARY:Bad date",
        lines,
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ].join("\r\n");
    return convertIcsCalendar(undefined, ics).events![0];
}

describe("invalid invite dates", () => {
    it("validDate rejects Invalid Date and non-dates", () => {
        expect(validDate(new Date("garbage"))).toBeUndefined();
        expect(validDate(undefined)).toBeUndefined();
        expect(validDate(null)).toBeUndefined();
        const date = new Date("2025-06-15T10:00:00Z");
        expect(validDate(date)).toBe(date);
    });

    it("drops an unparseable DTSTART", () => {
        const event = parseVevent("DTSTART:garbage\r\nDTEND:20250615T110000Z");
        expect(Number.isNaN(event.start.date.getTime())).toBe(true);
        expect(getEventStart(event)).toBeUndefined();
    });

    it("drops an unparseable DTEND", () => {
        const event = parseVevent("DTSTART:20250615T100000Z\r\nDTEND:garbage");
        expect(getEventStart(event)).toEqual(new Date("2025-06-15T10:00:00Z"));
        expect(getEventEnd(event)).toBeUndefined();
    });

    it("drops an end computed from a DURATION past the Date range", () => {
        const event = parseVevent("DTSTART:20250615T100000Z\r\nDURATION:P99999999999W");
        expect(getEventEnd(event)).toBeUndefined();
    });

    it("does not compute an end from an invalid DTSTART", () => {
        const event = parseVevent("DTSTART:garbage\r\nDURATION:PT1H");
        expect(getEventEnd(event)).toBeUndefined();
    });

    it("formatEventDateRange does not throw on invalid dates", () => {
        const invalid = new Date("garbage");
        const start = new Date("2025-06-15T10:00:00Z");
        expect(formatEventDateRange(invalid, undefined, "en-US")).toBe("");
        expect(formatEventDateRange(invalid, start, "en-US")).toBe("");
        expect(formatEventDateRange(start, invalid, "en-US")).toBe(
            formatEventDateRange(start, undefined, "en-US"),
        );
    });

    it("formatRecurrenceRule leaves out an invalid UNTIL", () => {
        const rule: IcsRecurrenceRule = {
            frequency: "WEEKLY",
            until: { date: new Date("garbage") },
        };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Weekly");
    });

    it("formatRecurrenceRule does not throw on an unparseable UNTIL from a real invite", () => {
        const event = parseVevent(
            "DTSTART:20250615T100000Z\r\nDTEND:20250615T110000Z\r\nRRULE:FREQ=WEEKLY;UNTIL=garbage",
        );
        expect(event.recurrenceRule).toBeDefined();
        expect(() => formatRecurrenceRule(event.recurrenceRule!, t, "en-US")).not.toThrow();
    });

    it("collapseRecurringEvents prefers a valid start over an invalid one, in either order", () => {
        const valid = parseVevent(
            "RECURRENCE-ID:20250622T100000Z\r\nDTSTART:20250622T100000Z\r\nDTEND:20250622T110000Z",
        );
        const invalid = parseVevent(
            "RECURRENCE-ID:20250615T100000Z\r\nDTSTART:garbage\r\nDTEND:20250615T110000Z",
        );
        expect(collapseRecurringEvents([invalid, valid])).toEqual([valid]);
        expect(collapseRecurringEvents([valid, invalid])).toEqual([valid]);
    });

    it("keeps a far-future but valid date", () => {
        const event = parseVevent("DTSTART:99991231T235959Z\r\nDTEND:99991231T235959Z");
        expect(getEventStart(event)).toEqual(new Date("9999-12-31T23:59:59Z"));
        expect(() => getEventStart(event)!.toISOString()).not.toThrow();
    });
});
