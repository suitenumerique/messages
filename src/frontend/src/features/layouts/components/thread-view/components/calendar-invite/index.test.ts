import {
    generateIcsCalendar,
    convertIcsCalendar,
    IcsCalendar,
    IcsEvent,
    IcsDuration,
    IcsRecurrenceRule,
} from "ts-ics";

import {
    durationToMs,
    getEventEnd,
    isAllDayEvent,
    formatEventDateRange,
    formatRecurrenceRule,
    getAttendeeStatusInfo,
    createContactFromAttendee,
} from "./calendar-helper";

// Simple pass-through translation mock
const t = (key: string, options?: Record<string, unknown>): string => {
    let result = key;
    if (options) {
        for (const [k, v] of Object.entries(options)) {
            result = result.replace(`{{${k}}}`, String(v));
        }
    }
    return result;
};

// ─── Helpers for building ICS test data ──────────────────────────────────────

function makeEvent(overrides: Partial<IcsEvent> & { summary: string; uid: string }): IcsEvent {
    return {
        stamp: { date: new Date("2025-01-01T00:00:00Z") },
        start: { date: new Date("2025-06-15T10:00:00Z") },
        end: { date: new Date("2025-06-15T11:00:00Z") },
        ...overrides,
    } as IcsEvent;
}

function makeCalendar(overrides: Partial<IcsCalendar> = {}): IcsCalendar {
    return {
        version: "2.0",
        prodId: "-//Test//Test//EN",
        ...overrides,
    };
}

// ─── durationToMs ────────────────────────────────────────────────────────────

describe("durationToMs", () => {
    it("should convert weeks to ms", () => {
        expect(durationToMs({ weeks: 2 })).toBe(2 * 7 * 86400000);
    });

    it("should convert days to ms", () => {
        expect(durationToMs({ days: 3 })).toBe(3 * 86400000);
    });

    it("should convert hours to ms", () => {
        expect(durationToMs({ hours: 5 })).toBe(5 * 3600000);
    });

    it("should convert minutes to ms", () => {
        expect(durationToMs({ minutes: 30 })).toBe(30 * 60000);
    });

    it("should convert seconds to ms", () => {
        expect(durationToMs({ seconds: 45 })).toBe(45 * 1000);
    });

    it("should handle combined duration", () => {
        const d: IcsDuration = { days: 1, hours: 2, minutes: 30 };
        expect(durationToMs(d)).toBe(86400000 + 7200000 + 1800000);
    });

    it("should return 0 for empty duration", () => {
        expect(durationToMs({})).toBe(0);
    });
});

// ─── getEventEnd ─────────────────────────────────────────────────────────────

describe("getEventEnd", () => {
    it("should return end date when event has end", () => {
        const endDate = new Date("2025-06-15T11:00:00Z");
        const event = makeEvent({
            summary: "Test",
            uid: "1",
            end: { date: endDate },
        });
        expect(getEventEnd(event)).toEqual(endDate);
    });

    it("should compute end from duration when event has duration instead of end", () => {
        const start = new Date("2025-06-15T10:00:00Z");
        const event = {
            summary: "Test",
            uid: "1",
            stamp: { date: new Date() },
            start: { date: start },
            duration: { hours: 1, minutes: 30 },
        } as IcsEvent;
        const expected = new Date(start.getTime() + 90 * 60000);
        expect(getEventEnd(event)).toEqual(expected);
    });

    it("should compute end for all-day duration event (P1D)", () => {
        const start = new Date("2025-06-15T00:00:00Z");
        const event = {
            summary: "All day",
            uid: "2",
            stamp: { date: new Date() },
            start: { date: start },
            duration: { days: 1 },
        } as IcsEvent;
        const expected = new Date("2025-06-16T00:00:00Z");
        expect(getEventEnd(event)).toEqual(expected);
    });
});

// ─── isAllDayEvent ───────────────────────────────────────────────────────────

describe("isAllDayEvent", () => {
    it("should return true when both start and end are midnight", () => {
        const start = new Date("2025-06-15T00:00:00");
        const end = new Date("2025-06-16T00:00:00");
        expect(isAllDayEvent(start, end)).toBe(true);
    });

    it("should return false when start is not midnight", () => {
        const start = new Date("2025-06-15T10:00:00");
        const end = new Date("2025-06-16T00:00:00");
        expect(isAllDayEvent(start, end)).toBe(false);
    });

    it("should return false when end is not midnight", () => {
        const start = new Date("2025-06-15T00:00:00");
        const end = new Date("2025-06-15T17:00:00");
        expect(isAllDayEvent(start, end)).toBe(false);
    });

    it("should return false when end is undefined", () => {
        const start = new Date("2025-06-15T00:00:00");
        expect(isAllDayEvent(start, undefined)).toBe(false);
    });

    it("should handle multi-day all-day events", () => {
        const start = new Date("2025-06-15T00:00:00");
        const end = new Date("2025-06-18T00:00:00");
        expect(isAllDayEvent(start, end)).toBe(true);
    });
});

// ─── formatEventDateRange ────────────────────────────────────────────────────

describe("formatEventDateRange", () => {
    it("should format a single all-day event (same day)", () => {
        const start = new Date("2025-06-15T00:00:00");
        const end = new Date("2025-06-16T00:00:00"); // ICS end is exclusive
        const result = formatEventDateRange(start, end, "en-US");
        // Single day: end - 1 day = same as start
        expect(result).toContain("June");
        expect(result).toContain("15");
        expect(result).not.toContain("16");
    });

    it("should format a multi-day all-day event", () => {
        const start = new Date("2025-06-15T00:00:00");
        const end = new Date("2025-06-18T00:00:00"); // 3-day event (15, 16, 17)
        const result = formatEventDateRange(start, end, "en-US");
        expect(result).toContain("15");
        expect(result).toContain("17"); // adjusted end: 18 - 1 day = 17
        expect(result).toContain("–");
    });

    it("should format same-day timed event with time range", () => {
        const start = new Date("2025-06-15T10:00:00");
        const end = new Date("2025-06-15T11:30:00");
        const result = formatEventDateRange(start, end, "en-US");
        expect(result).toContain("June");
        expect(result).toContain("15");
        expect(result).toContain("–");
    });

    it("should format multi-day timed event", () => {
        const start = new Date("2025-06-15T10:00:00");
        const end = new Date("2025-06-16T14:00:00");
        const result = formatEventDateRange(start, end, "en-US");
        expect(result).toContain("15");
        expect(result).toContain("16");
    });

    it("should format event with no end date", () => {
        const start = new Date("2025-06-15T10:00:00");
        const result = formatEventDateRange(start, undefined, "en-US");
        expect(result).toContain("June");
        expect(result).toContain("15");
    });

    it("should respect the language parameter", () => {
        const start = new Date("2025-06-15T00:00:00");
        const end = new Date("2025-06-16T00:00:00");
        const result = formatEventDateRange(start, end, "fr-FR");
        expect(result).toContain("juin");
    });
});

// ─── formatRecurrenceRule ────────────────────────────────────────────────────

describe("formatRecurrenceRule", () => {
    it("should format daily with interval 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "DAILY" };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Daily");
    });

    it("should format weekly with interval 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "WEEKLY" };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Weekly");
    });

    it("should format monthly with interval 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "MONTHLY" };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Monthly");
    });

    it("should format yearly with interval 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "YEARLY" };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Yearly");
    });

    it("should format daily with interval > 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "DAILY", interval: 3 };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Every 3 days");
    });

    it("should format weekly with interval > 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "WEEKLY", interval: 2 };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Every 2 weeks");
    });

    it("should format monthly with interval > 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "MONTHLY", interval: 6 };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Every 6 months");
    });

    it("should format yearly with interval > 1", () => {
        const rule: IcsRecurrenceRule = { frequency: "YEARLY", interval: 2 };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Every 2 years");
    });

    it("should append occurrence count", () => {
        const rule: IcsRecurrenceRule = { frequency: "DAILY", count: 10 };
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Daily · 10 occurrences");
    });

    it("should append until date", () => {
        const rule: IcsRecurrenceRule = {
            frequency: "WEEKLY",
            until: { date: new Date("2025-12-31T00:00:00Z") },
        };
        const result = formatRecurrenceRule(rule, t, "en-US");
        expect(result).toContain("Weekly");
        expect(result).toContain("·");
        expect(result).toContain("until");
    });

    it("should fallback to Recurring for unknown frequency", () => {
        const rule = { frequency: "SECONDLY" } as unknown as IcsRecurrenceRule;
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Recurring");
    });

    it("should fallback to Recurring for unknown frequency with interval > 1", () => {
        const rule = { frequency: "SECONDLY", interval: 5 } as unknown as IcsRecurrenceRule;
        expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Recurring");
    });
});

// ─── getAttendeeStatusInfo ───────────────────────────────────────────────────

describe("getAttendeeStatusInfo", () => {
    it("should return accepted info", () => {
        const info = getAttendeeStatusInfo("ACCEPTED", t);
        expect(info.icon).toBe("check_circle");
        expect(info.label).toBe("Accepted");
        expect(info.className).toBe("calendar-invite__attendee-status--accepted");
    });

    it("should return declined info", () => {
        const info = getAttendeeStatusInfo("DECLINED", t);
        expect(info.icon).toBe("cancel");
        expect(info.label).toBe("Declined");
        expect(info.className).toBe("calendar-invite__attendee-status--declined");
    });

    it("should return tentative info", () => {
        const info = getAttendeeStatusInfo("TENTATIVE", t);
        expect(info.icon).toBe("help");
        expect(info.label).toBe("Tentative");
        expect(info.className).toBe("calendar-invite__attendee-status--tentative");
    });

    it("should return delegated info", () => {
        const info = getAttendeeStatusInfo("DELEGATED", t);
        expect(info.icon).toBe("forward");
        expect(info.label).toBe("Delegated");
        expect(info.className).toBe("calendar-invite__attendee-status--delegated");
    });

    it("should return needs-action info", () => {
        const info = getAttendeeStatusInfo("NEEDS-ACTION", t);
        expect(info.icon).toBe("schedule");
        expect(info.label).toBe("Awaiting response");
        expect(info.className).toBe("calendar-invite__attendee-status--pending");
    });

    it("should return pending info for undefined partstat", () => {
        const info = getAttendeeStatusInfo(undefined, t);
        expect(info.icon).toBe("schedule");
        expect(info.label).toBe("Awaiting response");
        expect(info.className).toBe("calendar-invite__attendee-status--pending");
    });
});

// ─── createContactFromAttendee ───────────────────────────────────────────────

describe("createContactFromAttendee", () => {
    it("should create contact with email and name", () => {
        const contact = createContactFromAttendee({
            email: "alice@example.com",
            name: "Alice",
        });
        expect(contact).toEqual({
            id: "calendar-alice@example.com",
            email: "alice@example.com",
            name: "Alice",
        });
    });

    it("should set name to null when no name provided", () => {
        const contact = createContactFromAttendee({
            email: "bob@example.com",
        });
        expect(contact).toEqual({
            id: "calendar-bob@example.com",
            email: "bob@example.com",
            name: null,
        });
    });

    it("should set name to null for empty string name", () => {
        const contact = createContactFromAttendee({
            email: "carol@example.com",
            name: "",
        });
        expect(contact.name).toBeNull();
    });
});

// ─── ICS round-trip tests (generate → string → parse) ───────────────────────

describe("ICS round-trip with ts-ics", () => {
    it("should round-trip a simple event with end date", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({
                    summary: "Team Meeting",
                    uid: "meeting-001",
                    start: { date: new Date("2025-06-15T10:00:00Z") },
                    end: { date: new Date("2025-06-15T11:00:00Z") },
                }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);

        expect(parsed.events).toHaveLength(1);
        expect(parsed.events![0].summary).toBe("Team Meeting");
        expect(parsed.events![0].uid).toBe("meeting-001");
    });

    it("should round-trip an event with duration", () => {
        const calendar = makeCalendar({
            events: [
                {
                    summary: "Quick Sync",
                    uid: "sync-001",
                    stamp: { date: new Date("2025-01-01T00:00:00Z") },
                    start: { date: new Date("2025-06-15T14:00:00Z") },
                    duration: { hours: 1, minutes: 30 },
                } as IcsEvent,
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);

        expect(parsed.events).toHaveLength(1);
        const event = parsed.events![0];
        expect(event.summary).toBe("Quick Sync");

        const endDate = getEventEnd(event);
        expect(endDate).toBeDefined();
        // 14:00 + 1h30m = 15:30
        expect(endDate!.getUTCHours()).toBe(15);
        expect(endDate!.getUTCMinutes()).toBe(30);
    });

    it("should round-trip an all-day event", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({
                    summary: "Holiday",
                    uid: "holiday-001",
                    start: { date: new Date("2025-12-25T00:00:00Z") },
                    end: { date: new Date("2025-12-26T00:00:00Z") },
                }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);
        const event = parsed.events![0];
        const endDate = getEventEnd(event);

        expect(isAllDayEvent(event.start.date, endDate)).toBe(true);
        const formatted = formatEventDateRange(event.start.date, endDate, "en-US");
        expect(formatted).toContain("December");
        expect(formatted).toContain("25");
        // Should NOT contain "26" since it's a single all-day event
        expect(formatted).not.toContain("26");
    });

    it("should round-trip an event with attendees", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({
                    summary: "Sprint Review",
                    uid: "sprint-001",
                    attendees: [
                        { email: "alice@example.com", name: "Alice", partstat: "ACCEPTED" },
                        { email: "bob@example.com", name: "Bob", partstat: "TENTATIVE" },
                        { email: "carol@example.com", partstat: "DECLINED" },
                    ],
                    organizer: { email: "manager@example.com", name: "Manager" },
                }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);
        const event = parsed.events![0];

        expect(event.attendees).toHaveLength(3);
        expect(event.organizer?.email).toBe("manager@example.com");

        // Verify contact creation works with parsed data
        const contact = createContactFromAttendee(event.organizer!);
        expect(contact.email).toBe("manager@example.com");
        expect(contact.name).toBe("Manager");
    });

    it("should round-trip an event with recurrence rule", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({
                    summary: "Standup",
                    uid: "standup-001",
                    recurrenceRule: {
                        frequency: "WEEKLY",
                        interval: 1,
                        count: 52,
                    },
                }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);
        const event = parsed.events![0];

        expect(event.recurrenceRule).toBeDefined();
        expect(event.recurrenceRule!.frequency).toBe("WEEKLY");

        const formatted = formatRecurrenceRule(event.recurrenceRule!, t, "en-US");
        expect(formatted).toBe("Weekly · 52 occurrences");
    });

    it("should round-trip an event with location and description", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({
                    summary: "Office Meeting",
                    uid: "office-001",
                    location: "Room 42",
                    description: "Discuss quarterly results",
                }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);
        const event = parsed.events![0];

        expect(event.location).toBe("Room 42");
        expect(event.description).toBe("Discuss quarterly results");
    });

    it("should round-trip a calendar with multiple events", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({ summary: "Event A", uid: "a-001" }),
                makeEvent({ summary: "Event B", uid: "b-001" }),
                makeEvent({ summary: "Event C", uid: "c-001" }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);

        expect(parsed.events).toHaveLength(3);
        const summaries = parsed.events!.map((e) => e.summary);
        expect(summaries).toEqual(["Event A", "Event B", "Event C"]);
    });

    it("should round-trip a calendar with CANCEL method", () => {
        const calendar = makeCalendar({
            method: "CANCEL",
            events: [
                makeEvent({ summary: "Cancelled Meeting", uid: "cancel-001" }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);

        expect(parsed.method).toBe("CANCEL");
    });

    it("should round-trip an event with confirmed status", () => {
        const calendar = makeCalendar({
            events: [
                makeEvent({
                    summary: "Confirmed Event",
                    uid: "confirmed-001",
                    status: "CONFIRMED",
                }),
            ],
        });

        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);
        const event = parsed.events![0];

        expect(event.status).toBe("CONFIRMED");
    });

    it("should handle empty calendar (no events)", () => {
        const calendar = makeCalendar({ events: [] });
        const icsString = generateIcsCalendar(calendar);
        const parsed = convertIcsCalendar(undefined, icsString);

        expect(parsed.events ?? []).toHaveLength(0);
    });
});

// ─── Edge cases and integration ──────────────────────────────────────────────

describe("edge cases", () => {
    describe("formatEventDateRange with duration-based events", () => {
        it("should display correct range for 1h30m duration event", () => {
            const start = new Date("2025-06-15T14:00:00");
            const end = new Date(start.getTime() + durationToMs({ hours: 1, minutes: 30 }));
            const result = formatEventDateRange(start, end, "en-US");
            // Same day, so should show time range
            expect(result).toContain("–");
            expect(result).toContain("15");
        });

        it("should detect all-day for P1D duration event starting at midnight", () => {
            const start = new Date("2025-06-15T00:00:00");
            const end = new Date(start.getTime() + durationToMs({ days: 1 }));
            expect(isAllDayEvent(start, end)).toBe(true);
            const result = formatEventDateRange(start, end, "en-US");
            expect(result).toContain("15");
            // Single all-day: should not show end date
            expect(result).not.toContain("–");
        });

        it("should detect multi-day all-day for P3D duration", () => {
            const start = new Date("2025-06-15T00:00:00");
            const end = new Date(start.getTime() + durationToMs({ days: 3 }));
            expect(isAllDayEvent(start, end)).toBe(true);
            const result = formatEventDateRange(start, end, "en-US");
            expect(result).toContain("–");
            // Adjusted end: 18 - 1 day = 17
            expect(result).toContain("17");
        });
    });

    describe("getEventEnd with various IcsEvent shapes", () => {
        it("should prefer end over duration when both are absent", () => {
            // Edge case: event with neither end nor duration
            // This shouldn't happen per ICS spec, but handle gracefully
            const event = {
                summary: "No end",
                uid: "noend",
                stamp: { date: new Date() },
                start: { date: new Date("2025-06-15T10:00:00Z") },
            } as unknown as IcsEvent;
            expect(getEventEnd(event)).toBeUndefined();
        });
    });

    describe("formatRecurrenceRule edge cases", () => {
        it("should treat interval of 0 as 1 (fallback)", () => {
            const rule: IcsRecurrenceRule = { frequency: "DAILY", interval: 0 };
            // interval || 1 → 0 is falsy → becomes 1
            expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Daily");
        });

        it("should handle count = 1", () => {
            const rule: IcsRecurrenceRule = { frequency: "DAILY", count: 1 };
            expect(formatRecurrenceRule(rule, t, "en-US")).toBe("Daily · 1 occurrences");
        });

        it("should prefer count over until when both present", () => {
            const rule: IcsRecurrenceRule = {
                frequency: "WEEKLY",
                count: 5,
                until: { date: new Date("2025-12-31T00:00:00Z") },
            };
            const result = formatRecurrenceRule(rule, t, "en-US");
            expect(result).toContain("5 occurrences");
            expect(result).not.toContain("until");
        });
    });

    describe("createContactFromAttendee with special characters", () => {
        it("should handle email with special characters", () => {
            const contact = createContactFromAttendee({
                email: "user+tag@example.com",
                name: "Tàg User",
            });
            expect(contact.id).toBe("calendar-user+tag@example.com");
            expect(contact.email).toBe("user+tag@example.com");
            expect(contact.name).toBe("Tàg User");
        });
    });

    describe("getAttendeeStatusInfo exhaustive coverage", () => {
        it.each([
            ["ACCEPTED", "check_circle", "accepted"],
            ["DECLINED", "cancel", "declined"],
            ["TENTATIVE", "help", "tentative"],
            ["DELEGATED", "forward", "delegated"],
            ["NEEDS-ACTION", "schedule", "pending"],
        ] as const)("partstat %s → icon %s, class suffix %s", (partstat, expectedIcon, classSuffix) => {
            const info = getAttendeeStatusInfo(partstat, t);
            expect(info.icon).toBe(expectedIcon);
            expect(info.className).toContain(classSuffix);
        });
    });

    describe("isAllDayEvent boundary checks", () => {
        it("should return false if only seconds differ from midnight", () => {
            const start = new Date("2025-06-15T00:00:01");
            const end = new Date("2025-06-16T00:00:00");
            expect(isAllDayEvent(start, end)).toBe(false);
        });

        it("should return false if end has only minutes set", () => {
            const start = new Date("2025-06-15T00:00:00");
            const end = new Date("2025-06-16T00:01:00");
            expect(isAllDayEvent(start, end)).toBe(false);
        });
    });

    describe("durationToMs with weeks + days combined", () => {
        it("should correctly combine weeks and days", () => {
            const d: IcsDuration = { weeks: 1, days: 3 };
            expect(durationToMs(d)).toBe((7 + 3) * 86400000);
        });
    });
});

// ─── Malformed ICS reproduction tests ────────────────────────────────────────
// These reproduce bugs found with real-world .ics files where ts-ics returns
// undefined for fields its TypeScript types declare as required.

describe("malformed ICS: missing UID", () => {
    const ICS_NO_UID = `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nSUMMARY:Event Without UID\r\nDTSTART:20250615T100000Z\r\nDTEND:20250615T110000Z\r\nDTSTAMP:20250101T000000Z\r\nEND:VEVENT\r\nEND:VCALENDAR`;

    it("should parse event even when UID is missing from ICS", () => {
        const parsed = convertIcsCalendar(undefined, ICS_NO_UID);
        expect(parsed.events).toHaveLength(1);
        expect(parsed.events![0].summary).toBe("Event Without UID");
    });

    it("should have undefined uid despite TypeScript type saying string", () => {
        const parsed = convertIcsCalendar(undefined, ICS_NO_UID);
        // ts-ics types say uid: string, but at runtime it's undefined
        expect(parsed.events![0].uid).toBeUndefined();
    });

    it("should not produce duplicate keys for multiple events without UID", () => {
        const icsMultiNoUid = `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nSUMMARY:First No UID\r\nDTSTART:20250615T100000Z\r\nDTEND:20250615T110000Z\r\nDTSTAMP:20250101T000000Z\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nSUMMARY:Second No UID\r\nDTSTART:20250616T100000Z\r\nDTEND:20250616T110000Z\r\nDTSTAMP:20250101T000000Z\r\nEND:VEVENT\r\nEND:VCALENDAR`;

        const parsed = convertIcsCalendar(undefined, icsMultiNoUid);
        expect(parsed.events).toHaveLength(2);
        // Both uids are undefined - component uses fallback `event.uid || index`
        const keys = parsed.events!.map((e, i) => e.uid || i);
        const uniqueKeys = new Set(keys);
        expect(uniqueKeys.size).toBe(2);
    });

    it("should still allow helper functions to work on events without UID", () => {
        const parsed = convertIcsCalendar(undefined, ICS_NO_UID);
        const event = parsed.events![0];
        const endDate = getEventEnd(event);
        expect(endDate).toBeDefined();
        expect(isAllDayEvent(event.start.date, endDate)).toBe(false);
    });
});

describe("malformed ICS: missing DTSTART", () => {
    const ICS_NO_START = `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nSUMMARY:Event Without Start\r\nUID:no-start-001\r\nDTEND:20250615T110000Z\r\nDTSTAMP:20250101T000000Z\r\nEND:VEVENT\r\nEND:VCALENDAR`;

    it("should parse event even when DTSTART is missing from ICS", () => {
        const parsed = convertIcsCalendar(undefined, ICS_NO_START);
        expect(parsed.events).toHaveLength(1);
        expect(parsed.events![0].summary).toBe("Event Without Start");
    });

    it("should have undefined start despite TypeScript type saying IcsDateObject", () => {
        const parsed = convertIcsCalendar(undefined, ICS_NO_START);
        // ts-ics types say start: IcsDateObject, but at runtime it's undefined
        expect(parsed.events![0].start).toBeUndefined();
    });

    it("should not crash getEventEnd when start is undefined", () => {
        const parsed = convertIcsCalendar(undefined, ICS_NO_START);
        const event = parsed.events![0];
        // getEventEnd accesses event.start.date - must not throw
        expect(() => getEventEnd(event)).not.toThrow();
        // Should still return end date since DTEND is present
        expect(getEventEnd(event)).toBeDefined();
    });

    it("should not crash getEventEnd for duration event with missing start", () => {
        const ics = `BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nSUMMARY:Duration No Start\r\nUID:dur-no-start-001\r\nDURATION:PT1H\r\nDTSTAMP:20250101T000000Z\r\nEND:VEVENT\r\nEND:VCALENDAR`;

        const parsed = convertIcsCalendar(undefined, ics);
        const event = parsed.events![0];
        // Duration + missing start: cannot compute end, should return undefined (not crash)
        expect(() => getEventEnd(event)).not.toThrow();
        expect(getEventEnd(event)).toBeUndefined();
    });
});
