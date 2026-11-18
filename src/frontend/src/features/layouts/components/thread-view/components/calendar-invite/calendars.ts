import { useQuery } from "@tanstack/react-query";
import { fetchAPI } from "@/features/api/fetch-api";

export const DEFAULT_CALENDAR_COLOR = "#3788d8";

export type CalendarInfo = {
    id: string;
    name: string;
    color?: string | null;
    // User-controlled sort order from Apple's ``calendar-order`` property —
    // the backend already sorts the list, so this is informational only.
    // Null when the CalDAV server doesn't expose it.
    order?: number | null;
    // Email of the principal that owns this calendar (the OIDC user for a
    // personal calendar, the shared mailbox address for a MAILBOX calendar).
    // Used to match the calendar against the event's ATTENDEE list so RSVP
    // buttons are only shown for a calendar that can credibly speak for an
    // attendee. Null when the CalDAV server doesn't expose it.
    owner_email?: string | null;
    // "USER" or "MAILBOX" — matches suitenumerique's calendar-owner-type
    // extension. Null when the CalDAV server doesn't expose it.
    owner_type?: string | null;
    // Whether events can be saved to it; read-only calendars are only
    // checked for conflicts.
    writable?: boolean;
    // Linked to this mailbox in Calendars.
    linked?: boolean;
};

export type CalendarsApiResponse = {
    data: {
        calendars: CalendarInfo[];
        default_calendar_id?: string | null;
        default_conflict_calendar_ids?: string[];
        web_url?: string | null;
        configured?: boolean;
    };
    status: number;
};

/** A mailbox's calendars, shared by the invitation card and the mailbox settings. */
export const useMailboxCalendars = (mailboxId?: string) =>
    useQuery<CalendarsApiResponse>({
        queryKey: ["calendar-calendars", mailboxId],
        queryFn: () =>
            fetchAPI<CalendarsApiResponse>(`/api/v1.0/mailboxes/${mailboxId}/calendar/calendars/`),
        enabled: !!mailboxId,
        meta: { noGlobalError: true },
    });
