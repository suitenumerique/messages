import type { ReactNode } from "react";
import { useState, useMemo, useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import {
    convertIcsCalendar,
    IcsCalendar,
    IcsEvent,
    IcsAttendee,
} from "ts-ics";
import { Attachment } from "@/features/api/gen/models";
import { StatusEnum } from "@/features/api/gen";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { ContactChip } from "@/features/ui/components/contact-chip";
import { Badge } from "@/features/ui/components/badge";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { fetchAPI } from "@/features/api/fetch-api";
import { useTaskStatus } from "@/hooks/use-task-status";
import {
    getEventEnd,
    TextHelper,
    formatEventDateRange,
    formatRecurrenceRule,
    getAttendeeStatusInfo,
    createContactFromAttendee,
} from "./calendar-helper";
import { cleanEventForDisplay } from "./event-display";
import { CalendarSelect } from "./calendar-select";

type CalendarInviteProps = {
    attachment: Attachment;
    canDownload?: boolean;
    mailboxId?: string;
    // Acting mailbox email. Used to (a) check whether the mailbox is on
    // the ATTENDEE list — RSVP buttons are hidden otherwise to avoid a
    // misleading "Response saved" toast on a no-op write — and (b) match
    // the user's PARTSTAT in the stored copy when conflicts return
    // ``existing_partstat``.
    mailboxEmail?: string;
};

type CalendarInfo = {
    id: string;
    name: string;
    color?: string | null;
};

type ConflictInfo = {
    summary: string;
    start: string | null;
    end: string | null;
    // All-day events carry a date (not datetime). The backend surfaces
    // the distinction so the UI can format without a timezone shift —
    // ``new Date("2026-06-01")`` parses as midnight UTC and converts
    // to local time, which can render the day before for users west
    // of UTC.
    all_day?: boolean;
    calendar_name: string;
};

// PARTSTAT values for an already-stored copy of the invite, surfaced by
// /conflicts/ when ``exclude_uid`` matches. The UI uses this to
// pre-select the user's prior RSVP so a re-open doesn't re-prompt.
type ExistingPartstat = "ACCEPTED" | "DECLINED" | "TENTATIVE" | string | null;

type RsvpResponse = "ACCEPTED" | "DECLINED" | "TENTATIVE";

const MAX_VISIBLE_ATTENDEES = 3;
const MAX_DESCRIPTION_LENGTH = 200;

/**
 * Extracted download button to avoid duplication.
 * Variant "link" renders as a compact, borderless link used in the top-right
 * corner of the widget header.
 */
const DownloadButton = ({
    downloadUrl,
    name,
    variant = "secondary",
}: {
    downloadUrl: string;
    name: string;
    variant?: "primary" | "secondary" | "tertiary" | "link";
}) => {
    const { t } = useTranslation();
    return (
        <Button
            size="small"
            variant={variant === "link" ? "tertiary" : variant}
            color={variant === "link" ? "neutral" : undefined}
            icon={<Icon name="download" type={IconType.OUTLINED} />}
            href={downloadUrl}
            download={name.startsWith("unnamed") ? "invitation.ics" : name}
        >
            {t("Download invitation")}
        </Button>
    );
};

/**
 * Format a conflict's ``start`` value for display.
 *
 * All-day events arrive as plain ISO dates (``"2026-06-01"``). Passing
 * that to ``new Date(...).toLocaleString()`` parses it as midnight UTC
 * and converts to local time, which renders the day before for users
 * west of UTC. Format date-only inputs through ``toLocaleDateString``
 * with the UTC TZ pinned, so the day matches what the user actually
 * agreed to.
 */
const formatConflictTime = (
    iso: string,
    allDay: boolean,
    language: string,
): string => {
    if (allDay) {
        return new Date(iso).toLocaleDateString(language, {
            timeZone: "UTC",
            year: "numeric",
            month: "long",
            day: "numeric",
        });
    }
    return new Date(iso).toLocaleString(language);
};

/**
 * Conflict detection display
 */
const ConflictWarning = ({ conflicts, language }: { conflicts: ConflictInfo[]; language: string }) => {
    const { t } = useTranslation();
    if (conflicts.length === 0) return null;

    return (
        <div className="calendar-invite__conflicts" role="alert">
            <div className="calendar-invite__conflicts-header">
                <Icon name="warning" type={IconType.OUTLINED} />
                <span>
                    {t("{{count}} conflicting events", { count: conflicts.length })}
                </span>
            </div>
            <ul className="calendar-invite__conflicts-list">
                {conflicts.map((conflict, idx) => (
                    <li
                        key={`${conflict.calendar_name}-${conflict.summary}-${conflict.start ?? idx}`}
                        className="calendar-invite__conflict-item"
                    >
                        <span className="calendar-invite__conflict-summary">
                            {conflict.summary}
                        </span>
                        <span className="calendar-invite__conflict-calendar">
                            {conflict.calendar_name}
                        </span>
                        {conflict.start && (
                            <span className="calendar-invite__conflict-time">
                                {formatConflictTime(
                                    conflict.start,
                                    conflict.all_day === true,
                                    language,
                                )}
                            </span>
                        )}
                    </li>
                ))}
            </ul>
        </div>
    );
};

/**
 * Calendar chooser: compact dropdown with a color swatch for the selected
 * calendar. When a single calendar exists we still show a read-only pill so
 * the user knows which calendar actions will target.
 */
const CalendarChooser = ({
    calendars,
    selectedCalendarId,
    onSelect,
    calendarsWebUrl,
}: {
    calendars: CalendarInfo[];
    selectedCalendarId: string | null;
    onSelect: (id: string | null) => void;
    calendarsWebUrl: string | null;
}) => {
    const { t } = useTranslation();
    const selected =
        calendars.find((c) => c.id === selectedCalendarId) ?? calendars[0];

    const icon = (
        <Icon
            name="calendar_today"
            type={IconType.OUTLINED}
            className="calendar-invite__detail-icon"
        />
    );
    const iconSlot = calendarsWebUrl ? (
        <a
            className="calendar-invite__open-calendar"
            href={calendarsWebUrl}
            target="_blank"
            rel="noopener noreferrer"
            title={t("Open calendar")}
            aria-label={t("Open calendar")}
        >
            {icon}
        </a>
    ) : (
        icon
    );

    if (calendars.length <= 1) {
        if (!selected) return null;
        const swatchColor = selected.color || "var(--c--contextuals--content--semantic--neutral--secondary)";
        return (
            <div
                className="calendar-invite__calendar-pill"
                title={t("Target calendar")}
            >
                {iconSlot}
                <span
                    className="calendar-invite__calendar-swatch"
                    style={{ backgroundColor: swatchColor }}
                    aria-hidden="true"
                />
                <span className="calendar-invite__calendar-pill-name">
                    {selected.name}
                </span>
            </div>
        );
    }

    return (
        <div className="calendar-invite__calendar-chooser">
            {iconSlot}
            <CalendarSelect
                className="calendar-invite__calendar-select"
                calendars={calendars}
                value={selectedCalendarId ?? selected.id}
                onChange={(id) => onSelect(id || null)}
            />
        </div>
    );
};

/**
 * RSVP action buttons
 */
const RsvpButtons = ({
    onRespond,
    isPending,
    currentResponse,
    isCancellation,
}: {
    onRespond: (response: RsvpResponse) => void;
    isPending: boolean;
    currentResponse: RsvpResponse | null;
    isCancellation: boolean;
}) => {
    const { t } = useTranslation();

    if (isCancellation) return null;

    const buttons: { response: RsvpResponse; label: string; icon: string; variant: "primary" | "secondary" | "tertiary" }[] = [
        { response: "ACCEPTED", label: t("Accept"), icon: "check_circle", variant: currentResponse === "ACCEPTED" ? "primary" : "secondary" },
        { response: "TENTATIVE", label: t("Maybe"), icon: "help", variant: currentResponse === "TENTATIVE" ? "primary" : "secondary" },
        { response: "DECLINED", label: t("Decline"), icon: "cancel", variant: currentResponse === "DECLINED" ? "primary" : "secondary" },
    ];

    return (
        <div className="calendar-invite__rsvp-buttons">
            {buttons.map(({ response, label, icon, variant }) => (
                <Button
                    key={response}
                    size="small"
                    variant={variant}
                    icon={
                        isPending ? (
                            <Spinner size="sm" />
                        ) : (
                            <Icon name={icon} type={IconType.OUTLINED} />
                        )
                    }
                    onClick={() => onRespond(response)}
                    disabled={isPending}
                >
                    {label}
                </Button>
            ))}
        </div>
    );
};

type AttendeeEntry = {
    attendee: IcsAttendee;
    isOrganizer: boolean;
};

/**
 * Build the combined attendee list: organizer first (labelled), followed by
 * remaining attendees. If the organizer also appears in the attendees list,
 * it is deduplicated so a single entry is shown.
 */
function buildAttendeeList(event: IcsEvent): AttendeeEntry[] {
    const attendees = event.attendees ?? [];
    const organizer = event.organizer;
    if (!organizer) {
        return attendees.map((a) => ({ attendee: a, isOrganizer: false }));
    }
    const organizerEmail = organizer.email?.toLowerCase();
    const organizerAsAttendee = attendees.find(
        (a) => a.email?.toLowerCase() === organizerEmail,
    );
    const organizerEntry: AttendeeEntry = {
        attendee: organizerAsAttendee ?? (organizer as IcsAttendee),
        isOrganizer: true,
    };
    const rest = attendees
        .filter((a) => a.email?.toLowerCase() !== organizerEmail)
        .map<AttendeeEntry>((a) => ({ attendee: a, isOrganizer: false }));
    return [organizerEntry, ...rest];
}

/**
 * Renders a single event's details with its own state for attendees/description
 */
const EventCard = ({
    event,
    language,
    conflicts,
    headerRight,
}: {
    event: IcsEvent;
    language: string;
    conflicts: ConflictInfo[];
    headerRight?: ReactNode;
}) => {
    const { t } = useTranslation();
    const [showAllAttendees, setShowAllAttendees] = useState(false);
    const [showFullDescription, setShowFullDescription] = useState(false);

    const eventStart = event.start?.date;
    const eventEnd = getEventEnd(event);
    const attendeeEntries = useMemo(() => buildAttendeeList(event), [event]);
    const hasAttendees = attendeeEntries.length > 0;

    // Strip Google-style conference blocks, known location prefixes, and
    // cross-field duplicates before rendering (matches the Calendars app).
    const display = useMemo(
        () =>
            cleanEventForDisplay({
                description: event.description ?? "",
                location: event.location ?? "",
                url: event.url ?? "",
            }),
        [event.description, event.location, event.url],
    );

    const descriptionTruncated =
        !!display.description &&
        display.description.length > MAX_DESCRIPTION_LENGTH;

    const { visibleAttendees, hiddenCount } = useMemo(() => {
        const total = attendeeEntries.length;
        if (total === 0) {
            return { visibleAttendees: [] as AttendeeEntry[], hiddenCount: 0 };
        }
        if (showAllAttendees || total <= MAX_VISIBLE_ATTENDEES) {
            return { visibleAttendees: attendeeEntries, hiddenCount: 0 };
        }
        return {
            visibleAttendees: attendeeEntries.slice(0, MAX_VISIBLE_ATTENDEES),
            hiddenCount: total - MAX_VISIBLE_ATTENDEES,
        };
    }, [attendeeEntries, showAllAttendees]);

    const displayedDescription = useMemo(() => {
        if (!display.description) return null;
        if (showFullDescription || !descriptionTruncated) {
            return display.description;
        }
        return display.description.slice(0, MAX_DESCRIPTION_LENGTH) + "\u2026";
    }, [display.description, showFullDescription, descriptionTruncated]);

    // STATUS=CONFIRMED is the default for most invites (Google, etc.) and
    // conveys no actionable information to the recipient — it is the
    // organizer confirming their own event, not an RSVP. TENTATIVE means the
    // organizer has not firmed up the event; CANCELLED is meaningful and
    // already surfaced via the cancellation banner for METHOD:CANCEL, but we
    // still show the pill when the STATUS alone signals cancellation.
    const statusPill =
        event.status === "TENTATIVE"
            ? { label: t("Tentative"), title: t("The organizer marked this event as tentative."), cls: "tentative" }
            : event.status === "CANCELLED"
              ? { label: t("Cancelled"), title: t("This event has been cancelled by the organizer."), cls: "cancelled" }
              : null;

    return (
        <div className="calendar-invite__event">
            <header className="calendar-invite__header">
                <div className="calendar-invite__icon">
                    <Icon name="event" type={IconType.OUTLINED} />
                </div>
                <div className="calendar-invite__title-section">
                    <h3 className="calendar-invite__title">{event.summary}</h3>
                    {statusPill && (
                        <Badge
                            className={`calendar-invite__event-status calendar-invite__event-status--${statusPill.cls}`}
                            title={statusPill.title}
                        >
                            {statusPill.label}
                        </Badge>
                    )}
                </div>
                {headerRight && (
                    <div className="calendar-invite__header-right">
                        {headerRight}
                    </div>
                )}
            </header>

            <div className="calendar-invite__details">
                {/* Date and Time */}
                {eventStart && (
                    <div className="calendar-invite__detail-row">
                        <Icon
                            name="schedule"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <span>
                            {formatEventDateRange(
                                eventStart,
                                eventEnd,
                                language,
                            )}
                        </span>
                    </div>
                )}

                {/* Recurrence */}
                {event.recurrenceRule && (
                    <div className="calendar-invite__detail-row">
                        <Icon
                            name="repeat"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <span>
                            {formatRecurrenceRule(
                                event.recurrenceRule,
                                t,
                                language,
                            )}
                        </span>
                    </div>
                )}

                {/* Location */}
                {display.location && (
                    <div className="calendar-invite__detail-row">
                        <Icon
                            name="location_on"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <span>
                            {TextHelper.renderLinks(
                                [display.location],
                                { props: { className: "calendar-invite__link" } }
                            )}
                        </span>
                    </div>
                )}

                {/* Conference / URL */}
                {display.url && (
                    <div className="calendar-invite__detail-row">
                        <Icon
                            name="videocam"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <span>
                            {TextHelper.renderLinks(
                                [display.url],
                                { props: { className: "calendar-invite__link" } }
                            )}
                        </span>
                    </div>
                )}

                {/* Description */}
                {displayedDescription && (
                    <div className="calendar-invite__description">
                        <Icon
                            name="notes"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <div>
                            <p>{TextHelper.renderLinks([displayedDescription])}</p>
                            {descriptionTruncated && (
                                <button
                                    type="button"
                                    className="calendar-invite__show-more"
                                    onClick={() =>
                                        setShowFullDescription(
                                            !showFullDescription,
                                        )
                                    }
                                    aria-expanded={showFullDescription}
                                >
                                    {showFullDescription
                                        ? t("Show less")
                                        : t("Show more")}
                                </button>
                            )}
                        </div>
                    </div>
                )}

                {/* Conflict Warning */}
                {conflicts.length > 0 && (
                    <ConflictWarning conflicts={conflicts} language={language} />
                )}

                {/* Participants (organizer first, then attendees) */}
                {hasAttendees && (
                    <div className="calendar-invite__attendees">
                        <ul className="calendar-invite__attendee-list">
                            {visibleAttendees.map(({ attendee, isOrganizer }) => {
                                const statusInfo = getAttendeeStatusInfo(
                                    attendee.partstat,
                                    t,
                                );
                                return (
                                    <li
                                        key={attendee.email}
                                        className={
                                            isOrganizer
                                                ? "calendar-invite__attendee"
                                                : "calendar-invite__attendee calendar-invite__attendee--indented"
                                        }
                                    >
                                        {isOrganizer && (
                                            <Icon
                                                name="person"
                                                type={IconType.OUTLINED}
                                                className="calendar-invite__detail-icon"
                                            />
                                        )}
                                        <ContactChip
                                            contact={createContactFromAttendee(
                                                attendee,
                                            )}
                                            displayEmail={isOrganizer}
                                        />
                                        {isOrganizer ? (
                                            <span className="calendar-invite__organizer-label">
                                                {t("Organizer")}
                                            </span>
                                        ) : (
                                            <span
                                                className={`calendar-invite__attendee-pill ${statusInfo.className}`}
                                                title={statusInfo.label}
                                            >
                                                <Icon
                                                    name={statusInfo.icon}
                                                    type={IconType.OUTLINED}
                                                    size={14}
                                                />
                                                {statusInfo.label}
                                            </span>
                                        )}
                                    </li>
                                );
                            })}
                        </ul>
                        {attendeeEntries.length > MAX_VISIBLE_ATTENDEES && (
                            <button
                                type="button"
                                className="calendar-invite__show-more"
                                onClick={() =>
                                    setShowAllAttendees(!showAllAttendees)
                                }
                                aria-expanded={showAllAttendees}
                            >
                                {showAllAttendees
                                    ? t("Show less")
                                    : t("Show {{count}} more", {
                                          count: hiddenCount,
                                      })}
                            </button>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

const fetchAndParseCalendar = async (url: string): Promise<IcsCalendar> => {
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
    }
    const icsContent = await response.text();
    return convertIcsCalendar(undefined, icsContent);
};

const fetchIcsContent = async (url: string): Promise<string> => {
    // Bare fetch (not fetchAPI): this is a blob download URL serving
    // raw ICS text, not a JSON API endpoint.
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
    }
    return response.text();
};

type CalendarsApiResponse = {
    data: {
        calendars: CalendarInfo[];
        web_url?: string | null;
        configured?: boolean;
    };
    status: number;
};

type ConflictsApiResponse = {
    data: {
        conflicts: ConflictInfo[];
        existing_partstat?: ExistingPartstat;
    };
    status: number;
};

type RsvpApiResponse = {
    data: { task_id: string };
    status: number;
};

export const CalendarInvite = ({
    attachment,
    canDownload = true,
    mailboxId,
    mailboxEmail,
}: CalendarInviteProps) => {
    const { t, i18n } = useTranslation();
    const [selectedCalendarId, setSelectedCalendarId] = useState<string | null>(null);
    const [rsvpResponse, setRsvpResponse] = useState<RsvpResponse | null>(null);

    const downloadUrl = AttachmentHelper.getDownloadUrl(attachment);
    const language = i18n.resolvedLanguage || "en";

    // Fetch and parse calendar data
    const { data: calendar, isLoading, isError, refetch } = useQuery<IcsCalendar>({
        queryKey: ["calendar-invite", downloadUrl],
        queryFn: () => fetchAndParseCalendar(downloadUrl),
        meta: { noGlobalError: true },
    });

    // Fetch raw ICS content (for sending to backend)
    const { data: icsContent } = useQuery<string>({
        queryKey: ["calendar-invite-raw", downloadUrl],
        queryFn: () => fetchIcsContent(downloadUrl),
        enabled: !!mailboxId,
        meta: { noGlobalError: true },
    });

    // Fetch available calendars
    const {
        data: calendarsResponse,
        isError: isCalendarsError,
        isLoading: isCalendarsLoading,
    } = useQuery<CalendarsApiResponse>({
        queryKey: ["calendar-calendars", mailboxId],
        queryFn: () =>
            fetchAPI<CalendarsApiResponse>(
                `/api/v1.0/mailboxes/${mailboxId}/calendar/calendars/`,
            ),
        enabled: !!mailboxId,
        meta: { noGlobalError: true },
    });

    const calendars = isCalendarsError ? [] : (calendarsResponse?.data?.calendars ?? []);
    const calendarsWebUrl = calendarsResponse?.data?.web_url || null;
    // When the backend reports the CalDAV integration is not configured at all
    // for this deployment/mailbox we hide the footer entirely instead of
    // nudging the user toward a service that doesn't exist. Treat the flag
    // as optional for backwards compatibility (older backends omit it).
    const isCalDAVConfigured = calendarsResponse?.data?.configured !== false;
    const hasCalDAV = calendars.length > 0;
    // True when the server reached CalDAV successfully but the user has no
    // calendars yet — distinct from the service being unreachable/unavailable.
    const isCalDAVEmpty = !isCalendarsError && isCalDAVConfigured && calendars.length === 0 && !!mailboxId && !isCalendarsLoading;
    // While calendars are loading we want the footer to reserve space (no
    // layout shift) but not show the final controls yet.
    const isCalendarsPending = !!mailboxId && isCalendarsLoading;

    // Set default calendar when calendars load
    const effectiveCalendarId = selectedCalendarId ?? (calendars.length > 0 ? calendars[0].id : null);

    const events = calendar?.events ?? [];
    const isCancellation = calendar?.method === "CANCEL";

    // Conflict detection for the first event
    const firstEvent = events[0];

    // RSVP semantics only apply when the mailbox is actually on the
    // ATTENDEE list. Without this gate, clicking Accept on a forwarded
    // invite (where the mailbox isn't an attendee) would PUT a stored
    // copy with no PARTSTAT change for the user — the backend already
    // refuses such RSVPs (see ``respond_to_event``), but the buttons
    // shouldn't appear in the UI in the first place.
    const isMailboxAttendee = useMemo(() => {
        if (!firstEvent || !mailboxEmail) return false;
        const target = mailboxEmail.toLowerCase();
        return (firstEvent.attendees ?? []).some(
            (a) => (a.email ?? "").toLowerCase() === target,
        );
    }, [firstEvent, mailboxEmail]);
    const eventStart = firstEvent?.start?.date;
    const eventEnd = getEventEnd(firstEvent);
    const eventUid = firstEvent?.uid;

    const { data: conflictsResponse, isError: isConflictsError } = useQuery<ConflictsApiResponse>({
        queryKey: [
            "calendar-conflicts",
            mailboxId,
            eventStart?.toISOString(),
            eventEnd?.toISOString(),
            eventUid,
        ],
        queryFn: () =>
            fetchAPI<ConflictsApiResponse>(
                `/api/v1.0/mailboxes/${mailboxId}/calendar/conflicts/`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        start: eventStart!.toISOString(),
                        end: eventEnd!.toISOString(),
                        // Exclude prior imports of this same invite so we don't
                        // flag the event as conflicting with itself.
                        exclude_uid: eventUid,
                    }),
                },
            ),
        enabled: !!mailboxId && hasCalDAV && !!eventStart && !!eventEnd,
        meta: { noGlobalError: true },
    });

    const conflicts = isConflictsError ? [] : (conflictsResponse?.data?.conflicts ?? []);
    // Conflict-query failure is non-blocking: calendars loaded fine, so the
    // user can still RSVP / add the event. Only treat calendar-listing
    // failures or actual emptiness as "unavailable".
    const showUnavailable = isCalendarsError && !isCalDAVEmpty;

    // Pre-select the user's prior RSVP from the stored copy (if any),
    // so re-opening the email doesn't re-prompt for a choice already made.
    // Only seeds ``rsvpResponse`` once per mount per UID — manual clicks
    // afterwards take precedence.
    const existingPartstat = conflictsResponse?.data?.existing_partstat ?? null;
    const seededFromExistingRef = useRef<string | null>(null);
    useEffect(() => {
        if (!eventUid || existingPartstat == null) return;
        if (seededFromExistingRef.current === eventUid) return;
        if (
            existingPartstat === "ACCEPTED" ||
            existingPartstat === "DECLINED" ||
            existingPartstat === "TENTATIVE"
        ) {
            setRsvpResponse(existingPartstat);
            seededFromExistingRef.current = eventUid;
        }
    }, [existingPartstat, eventUid]);

    // Task polling for RSVP/add-to-calendar (shared with import code).
    // The hook's default exhausted-retries message is import-flavored;
    // pass a calendar-appropriate one so a polling timeout doesn't tell
    // the user something failed "while importing messages".
    const [taskId, setTaskId] = useState<string | null>(null);
    const [isSubmitting, setIsSubmitting] = useState(false);
    const taskStatus = useTaskStatus(taskId, {
        exhaustedError: t("Could not confirm the calendar update."),
    });
    const isPending = isSubmitting || (!!taskId && taskStatus?.state !== StatusEnum.SUCCESS && taskStatus?.state !== StatusEnum.FAILURE);

    useEffect(() => {
        if (!taskStatus) return;
        if (taskStatus.state === StatusEnum.SUCCESS) {
            setTaskId(null);
            setIsSubmitting(false);
            addToast(
                <ToasterItem type="info">
                    <span className="material-icons">check_circle</span>
                    <span>
                        {rsvpResponse
                            ? t("Response saved — the organizer will be notified")
                            : t("Event added to calendar")}
                    </span>
                </ToasterItem>,
            );
        } else if (taskStatus.state === StatusEnum.FAILURE) {
            setTaskId(null);
            setIsSubmitting(false);
            setRsvpResponse(null);
            addToast(
                <ToasterItem type="error">
                    <span className="material-icons">error</span>
                    <span>{taskStatus.error ?? t("An unexpected error occurred.")}</span>
                </ToasterItem>,
            );
        }
    }, [taskStatus, rsvpResponse, t]);

    const handleRsvp = useCallback(
        async (response: RsvpResponse) => {
            if (!mailboxId || !icsContent || isPending) return;

            setIsSubmitting(true);
            try {
                const result = await fetchAPI<RsvpApiResponse>(
                    `/api/v1.0/mailboxes/${mailboxId}/calendar/rsvp/`,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            ics_data: icsContent,
                            response,
                            calendar_id: effectiveCalendarId,
                        }),
                    },
                );
                setRsvpResponse(response);
                setTaskId(result.data.task_id);
            } catch {
                setIsSubmitting(false);
                addToast(
                    <ToasterItem type="error">
                        <span>{t("An unexpected error occurred.")}</span>
                    </ToasterItem>,
                );
            }
        },
        [mailboxId, icsContent, effectiveCalendarId, isPending, t],
    );

    const handleAddToCalendar = useCallback(async () => {
        if (!mailboxId || !icsContent || isPending) return;

        setIsSubmitting(true);
        try {
            const result = await fetchAPI<RsvpApiResponse>(
                `/api/v1.0/mailboxes/${mailboxId}/calendar/add/`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        ics_data: icsContent,
                        calendar_id: effectiveCalendarId,
                    }),
                },
            );
            setRsvpResponse(null);
            setTaskId(result.data.task_id);
        } catch {
            setIsSubmitting(false);
            addToast(
                <ToasterItem type="error">
                    <span>{t("An unexpected error occurred.")}</span>
                </ToasterItem>,
            );
        }
    }, [mailboxId, icsContent, effectiveCalendarId, isPending, t]);

    if (isLoading) {
        return (
            <div
                className="calendar-invite calendar-invite--loading"
                role="status"
                aria-live="polite"
            >
                <Spinner />
                <span>{t("Loading calendar invite...")}</span>
            </div>
        );
    }

    if (isError || !calendar) {
        return (
            <div
                className="calendar-invite calendar-invite--error"
                role="alert"
            >
                <Icon name="error" type={IconType.OUTLINED} />
                <span>{t("Failed to load calendar invite")}</span>
                <Button
                    size="small"
                    variant="tertiary"
                    onClick={() => refetch()}
                >
                    {t("Try again")}
                </Button>
                {canDownload && (
                    <DownloadButton
                        downloadUrl={downloadUrl}
                        name={attachment.name}
                        variant="tertiary"
                    />
                )}
            </div>
        );
    }

    if (events.length === 0) {
        return (
            <div
                className="calendar-invite calendar-invite--empty"
                role="status"
            >
                <Icon name="event" type={IconType.OUTLINED} />
                <span>{t("No event found in calendar invite")}</span>
                {canDownload && (
                    <DownloadButton
                        downloadUrl={downloadUrl}
                        name={attachment.name}
                        variant="tertiary"
                    />
                )}
            </div>
        );
    }

    // Header-right slot: top-right download link on the first event card.
    const headerRight = canDownload ? (
        <DownloadButton
            downloadUrl={downloadUrl}
            name={attachment.name}
            variant="link"
        />
    ) : null;

    // Footer reserves a stable height: loading → placeholder; error →
    // "Calendar service unavailable"; ready → dropdown + RSVP + add.
    const renderFooter = () => {
        if (isCancellation) {
            // For cancellations the cancellation banner is the primary signal;
            // we still reserve the footer slot so layout is stable but show
            // nothing actionable.
            return null;
        }
        if (!isCalDAVConfigured) {
            // CalDAV integration disabled for this deployment/mailbox — no
            // actionable affordance fits, so hide the footer entirely.
            return null;
        }
        if (isCalendarsPending) {
            return (
                <div className="calendar-invite__connection calendar-invite__connection--loading" role="status">
                    <Spinner size="sm" />
                    <span>{t("Connecting to calendar…")}</span>
                </div>
            );
        }
        if (showUnavailable) {
            return (
                <div
                    className="calendar-invite__connection calendar-invite__connection--unavailable"
                    role="alert"
                >
                    <Icon name="cloud_off" type={IconType.OUTLINED} />
                    <span>{t("Calendar service unavailable")}</span>
                </div>
            );
        }
        if (isCalDAVEmpty) {
            return (
                <div
                    className="calendar-invite__connection calendar-invite__connection--empty"
                    role="status"
                >
                    <Icon name="event_note" type={IconType.OUTLINED} />
                    <span>{t("You don't have a calendar yet.")}</span>
                    {calendarsWebUrl && (
                        <Button
                            size="small"
                            variant="tertiary"
                            icon={<Icon name="open_in_new" type={IconType.OUTLINED} />}
                            href={calendarsWebUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                        >
                            {t("Create one")}
                        </Button>
                    )}
                </div>
            );
        }
        if (!hasCalDAV) {
            return (
                <div
                    className="calendar-invite__connection calendar-invite__connection--unavailable"
                    role="alert"
                >
                    <Icon name="cloud_off" type={IconType.OUTLINED} />
                    <span>{t("Calendar service unavailable")}</span>
                </div>
            );
        }
        return (
            <div className="calendar-invite__connection">
                <CalendarChooser
                    calendars={calendars}
                    selectedCalendarId={effectiveCalendarId}
                    onSelect={setSelectedCalendarId}
                    calendarsWebUrl={calendarsWebUrl}
                />
                <div className="calendar-invite__connection-actions">
                    {isMailboxAttendee && (
                        <RsvpButtons
                            onRespond={handleRsvp}
                            isPending={isPending || !icsContent}
                            currentResponse={rsvpResponse}
                            isCancellation={isCancellation}
                        />
                    )}
                    <Button
                        size="small"
                        variant="tertiary"
                        icon={
                            isPending ? (
                                <Spinner size="sm" />
                            ) : (
                                <Icon name="add" type={IconType.OUTLINED} />
                            )
                        }
                        onClick={handleAddToCalendar}
                        disabled={isPending || !icsContent}
                    >
                        {t("Add to calendar")}
                    </Button>
                </div>
            </div>
        );
    };

    return (
        <article className="calendar-invite" aria-label={t("Calendar invite")}>
            {isCancellation && (
                <div
                    className="calendar-invite__method-banner calendar-invite__method-banner--cancel"
                    role="alert"
                >
                    <Icon name="event_busy" type={IconType.OUTLINED} />
                    <span>{t("This event has been cancelled")}</span>
                </div>
            )}

            {events.map((event, index) => (
                <EventCard
                    key={event.uid || index}
                    event={event}
                    language={language}
                    conflicts={index === 0 ? conflicts : []}
                    headerRight={index === 0 ? headerRight : null}
                />
            ))}

            {(() => {
                const footer = renderFooter();
                return footer ? (
                    <footer className="calendar-invite__actions">{footer}</footer>
                ) : null;
            })()}
        </article>
    );
};
