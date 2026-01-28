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
 * Compute the end Date from an event that may use end or duration
 */
export function getEventEnd(event: IcsEvent): Date | undefined {
    if (event.end) return event.end.date;
    if (event.duration && event.start) {
        return new Date(event.start.date.getTime() + durationToMs(event.duration));
    }
    return undefined;
}

/**
 * Convert URL strings in text to clickable links
 */
export function linkifyText(text: string): React.ReactNode[] {
    const urlRegex = /(https?:\/\/[^\s<>"{}|\\^`[\]]+)/i;
    const parts = text.split(urlRegex);

    return parts.map((part, index) => {
        if (urlRegex.test(part)) {
            return (
                <a
                    key={index}
                    href={part}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="calendar-invite__link"
                >
                    {part}
                </a>
            );
        }
        return part;
    });
}

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
 * Format a date range for display, handling all-day events and same-day events
 */
export function formatEventDateRange(
    start: Date,
    end: Date | undefined,
    language: string,
): string {
    const allDay = isAllDayEvent(start, end);

    const dateFormatter = new Intl.DateTimeFormat(language, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
    });

    if (allDay) {
        const startDate = dateFormatter.format(start);
        if (!end) return startDate;
        // All-day events: end date is exclusive in ICS (next day at midnight)
        const adjustedEnd = new Date(end.getTime() - 86400000);
        if (start.toDateString() === adjustedEnd.toDateString()) {
            return startDate; // Single all-day event
        }
        return `${startDate} – ${dateFormatter.format(adjustedEnd)}`;
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

    if (rule.count) {
        text += ` · ${t("{{count}} occurrences", { count: rule.count })}`;
    } else if (rule.until) {
        const dateFormatter = new Intl.DateTimeFormat(language, {
            dateStyle: "long",
        });
        text += ` · ${t("until {{date}}", { date: dateFormatter.format(rule.until.date) })}`;
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
