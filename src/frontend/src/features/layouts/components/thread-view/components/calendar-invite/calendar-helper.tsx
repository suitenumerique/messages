import {
    IcsEvent,
    IcsAttendee,
    IcsDuration,
    IcsRecurrenceRule,
} from "ts-ics";
import { Contact } from "@/features/api/gen/models";

/**
 * Convert an ICS duration to milliseconds
 */
export function durationToMs(d: IcsDuration): number {
    let ms = 0;
    if (d.weeks) ms += d.weeks * 7 * 86400000;
    if (d.days) ms += d.days * 86400000;
    if (d.hours) ms += d.hours * 3600000;
    if (d.minutes) ms += d.minutes * 60000;
    if (d.seconds) ms += d.seconds * 1000;
    return ms;
}

/**
 * ``date`` if it is a usable Date, else undefined.
 *
 * ts-ics returns ``Invalid Date`` for an unparseable DTSTART/DTEND/UNTIL
 * instead of throwing, and an invite is sender-controlled.
 * ``toISOString`` and ``Intl.DateTimeFormat.format`` throw a RangeError
 * on an invalid Date, which would take down the whole message view.
 */
export function validDate(date: Date | undefined | null): Date | undefined {
    return date instanceof Date && !Number.isNaN(date.getTime()) ? date : undefined;
}

/**
 * The event's start Date, or undefined if missing or invalid
 */
export function getEventStart(event: IcsEvent | undefined): Date | undefined {
    return validDate(event?.start?.date);
}

/**
 * Compute the end Date from an event that may use end or duration.
 * Undefined if missing or invalid (e.g. a DURATION past the Date range).
 */
export function getEventEnd(event: IcsEvent | undefined): Date | undefined {
    if (!event) return undefined;
    if (event.end) return validDate(event.end.date);
    const start = getEventStart(event);
    if (event.duration && start) {
        return validDate(new Date(start.getTime() + durationToMs(event.duration)));
    }
    return undefined;
}

/**
 * Of two VEVENTs sharing a UID, return the one that best represents the
 * series: prefer the master (no RECURRENCE-ID — it carries the RRULE), and
 * otherwise the earlier start.
 */
function pickSeriesRepresentative(a: IcsEvent, b: IcsEvent): IcsEvent {
    const aIsMaster = !a.recurrenceId;
    const bIsMaster = !b.recurrenceId;
    if (aIsMaster !== bIsMaster) return aIsMaster ? a : b;
    const aStart = getEventStart(a)?.getTime() ?? Infinity;
    const bStart = getEventStart(b)?.getTime() ?? Infinity;
    return aStart <= bStart ? a : b;
}

/**
 * Collapse a calendar's VEVENTs into one entry per recurrence set.
 *
 * A recurring invite is transmitted as several VEVENTs sharing a single UID:
 * a master event carrying the RRULE plus one VEVENT per modified occurrence,
 * each tagged with RECURRENCE-ID. Rendering them verbatim shows the same
 * meeting several times over. Keep one representative per UID — the master
 * when present (it describes the whole series), otherwise the earliest
 * occurrence. First-seen order is preserved, and UID-less events (which can't
 * be grouped) are each kept as-is.
 */
export function collapseRecurringEvents(events: IcsEvent[]): IcsEvent[] {
    const byKey = new Map<string, IcsEvent>();
    events.forEach((event, index) => {
        const key = event.uid || `__no-uid-${index}`;
        const existing = byKey.get(key);
        byKey.set(key, existing ? pickSeriesRepresentative(existing, event) : event);
    });
    return [...byKey.values()];
}

export { TextHelper } from "@/features/utils/text-helper";

/**
 * Detect all-day events by checking if start/end are both at midnight.
 * Checks both local and UTC midnight because ICS VALUE=DATE fields are
 * represented as UTC midnight by ts-ics, while dates constructed in local
 * time (e.g. from timezone-aware events) may be local midnight.
 */
export function isAllDayEvent(start: Date, end?: Date): boolean {
    const isMidnight = (d: Date) =>
        (d.getHours() === 0 && d.getMinutes() === 0 && d.getSeconds() === 0) ||
        (d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0);
    if (!isMidnight(start)) return false;
    if (!end) return false;
    return isMidnight(end);
}

/**
 * Whether the event's start is a calendar day (``VALUE=DATE``), not a time.
 */
export function isDateOnlyEvent(event: IcsEvent | undefined): boolean {
    return event?.start?.type === "DATE";
}

/**
 * Format a date range for display, handling all-day events and same-day events.
 * ``allDay`` comes from the event's ``VALUE=DATE`` when known; otherwise
 * midnight boundaries are taken for one.
 */
export function formatEventDateRange(
    start: Date,
    end: Date | undefined,
    language: string,
    allDay?: boolean,
): string {
    if (!validDate(start)) return "";
    end = validDate(end);
    allDay = allDay ?? isAllDayEvent(start, end);

    const dateFormatter = new Intl.DateTimeFormat(language, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
    });

    if (allDay) {
        // ts-ics gives a VALUE=DATE day as UTC midnight: read in local time,
        // it is the day before anywhere west of UTC.
        const utcMidnight = start.getUTCHours() === 0 && start.getUTCMinutes() === 0;
        const dayFormatter = utcMidnight
            ? new Intl.DateTimeFormat(language, {
                  weekday: "long",
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                  timeZone: "UTC",
              })
            : dateFormatter;
        const startDate = dayFormatter.format(start);
        if (!end) return startDate;
        // All-day events: end date is exclusive in ICS (next day at midnight)
        const lastDay = dayFormatter.format(new Date(end.getTime() - 86400000));
        if (end.getTime() - start.getTime() <= 86400000 || lastDay === startDate) {
            return startDate; // Single all-day event
        }
        return `${startDate} – ${lastDay}`;
    }

    const timeFormatter = new Intl.DateTimeFormat(language, {
        hour: "numeric",
        minute: "2-digit",
    });

    const startDate = dateFormatter.format(start);
    const startTime = timeFormatter.format(start);

    if (!end) {
        return `${startDate} ${startTime}`;
    }

    const endTime = timeFormatter.format(end);
    const sameDay = start.toDateString() === end.toDateString();

    if (sameDay) {
        return `${startDate}, ${startTime} – ${endTime}`;
    }

    return `${startDate} ${startTime} – ${dateFormatter.format(end)} ${endTime}`;
}

/**
 * Format a recurrence rule into a human-readable string
 */
export function formatRecurrenceRule(
    rule: IcsRecurrenceRule,
    t: (key: string, options?: Record<string, unknown>) => string,
    language: string,
): string {
    const interval = rule.interval || 1;

    let text: string;
    if (interval === 1) {
        switch (rule.frequency) {
            case "DAILY": text = t("Daily"); break;
            case "WEEKLY": text = t("Weekly"); break;
            case "MONTHLY": text = t("Monthly"); break;
            case "YEARLY": text = t("Yearly"); break;
            default: text = t("Recurring");
        }
    } else {
        switch (rule.frequency) {
            case "DAILY": text = t("Every {{count}} days", { count: interval }); break;
            case "WEEKLY": text = t("Every {{count}} weeks", { count: interval }); break;
            case "MONTHLY": text = t("Every {{count}} months", { count: interval }); break;
            case "YEARLY": text = t("Every {{count}} years", { count: interval }); break;
            default: text = t("Recurring");
        }
    }

    const until = validDate(rule.until?.date);
    if (rule.count) {
        text += ` · ${t("{{count}} occurrences", { count: rule.count })}`;
    } else if (until) {
        const dateFormatter = new Intl.DateTimeFormat(language, {
            dateStyle: "long",
        });
        text += ` · ${t("until {{date}}", { date: dateFormatter.format(until) })}`;
    }

    return text;
}

/**
 * Get the appropriate icon and label for an attendee's participation status
 */
export function getAttendeeStatusInfo(
    partstat: IcsAttendee["partstat"],
    t: (key: string) => string,
): { icon: string; label: string; className: string } {
    switch (partstat) {
        case "ACCEPTED":
            return {
                icon: "check_circle",
                label: t("Accepted"),
                className: "calendar-invite__attendee-status--accepted",
            };
        case "DECLINED":
            return {
                icon: "cancel",
                label: t("Declined"),
                className: "calendar-invite__attendee-status--declined",
            };
        case "TENTATIVE":
            return {
                icon: "help",
                label: t("Tentative"),
                className: "calendar-invite__attendee-status--tentative",
            };
        case "DELEGATED":
            return {
                icon: "forward",
                label: t("Delegated"),
                className: "calendar-invite__attendee-status--delegated",
            };
        case "NEEDS-ACTION":
        default:
            return {
                icon: "schedule",
                label: t("Awaiting response"),
                className: "calendar-invite__attendee-status--pending",
            };
    }
}

/**
 * Create a Contact-like object from ICS attendee/organizer data for ContactChip
 */
export function createContactFromAttendee(
    attendee: { email: string; name?: string },
): Contact {
    return {
        id: `calendar-${attendee.email}`,
        email: attendee.email,
        name: attendee.name || null,
    };
}
