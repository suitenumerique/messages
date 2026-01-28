import { useEffect, useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import {
    convertIcsCalendar,
    IcsCalendar,
    IcsEvent,
    IcsAttendee,
    IcsDuration,
    IcsRecurrenceRule,
} from "ts-ics";
import { Attachment, Contact } from "@/features/api/gen/models";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { ContactChip } from "@/features/ui/components/contact-chip";
import { Badge } from "@/features/ui/components/badge";

type CalendarInviteProps = {
    attachment: Attachment;
    canDownload?: boolean;
};

type LoadingState = "loading" | "success" | "error";

const MAX_VISIBLE_ATTENDEES = 3;
const MAX_DESCRIPTION_LENGTH = 200;
const MAX_CACHE_SIZE = 50;

// Module-level cache for parsed calendars to avoid re-fetching on remount
const calendarCache = new Map<string, IcsCalendar>();

/**
 * Convert an ICS duration to milliseconds
 */
function durationToMs(d: IcsDuration): number {
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
function getEventEnd(event: IcsEvent): Date | undefined {
    if (event.end) return event.end.date;
    if (event.duration && event.start) {
        return new Date(event.start.date.getTime() + durationToMs(event.duration));
    }
    return undefined;
}

/**
 * Convert URL strings in text to clickable links
 */
function linkifyText(text: string): React.ReactNode[] {
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
 * Detect all-day events by checking if start/end are both at midnight
 */
function isAllDayEvent(start: Date, end?: Date): boolean {
    const startMidnight =
        start.getHours() === 0 &&
        start.getMinutes() === 0 &&
        start.getSeconds() === 0;
    if (!startMidnight) return false;
    if (!end) return false;
    return (
        end.getHours() === 0 &&
        end.getMinutes() === 0 &&
        end.getSeconds() === 0
    );
}

/**
 * Format a date range for display, handling all-day events and same-day events
 */
function formatEventDateRange(
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
function formatRecurrenceRule(
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
function getAttendeeStatusInfo(
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
function createContactFromAttendee(
    attendee: { email: string; name?: string },
): Contact {
    return {
        id: `calendar-${attendee.email}`,
        email: attendee.email,
        name: attendee.name || null,
    };
}

/**
 * Extracted download button to avoid duplication
 */
const DownloadButton = ({
    downloadUrl,
    name,
    variant = "secondary",
}: {
    downloadUrl: string;
    name: string;
    variant?: "primary" | "secondary" | "tertiary";
}) => {
    const { t } = useTranslation();
    return (
        <Button
            size="small"
            variant={variant}
            icon={<Icon name="download" type={IconType.OUTLINED} />}
            href={downloadUrl}
            download={name}
        >
            {t("Download .ics file")}
        </Button>
    );
};

/**
 * Renders a single event's details with its own state for attendees/description
 */
const EventCard = ({
    event,
    language,
}: {
    event: IcsEvent;
    language: string;
}) => {
    const { t } = useTranslation();
    const [showAllAttendees, setShowAllAttendees] = useState(false);
    const [showFullDescription, setShowFullDescription] = useState(false);

    const eventStart = event.start?.date;
    const eventEnd = getEventEnd(event);
    const attendeeCount = event.attendees?.length ?? 0;
    const hasAttendees = attendeeCount > 0;
    const descriptionTruncated =
        !!event.description &&
        event.description.length > MAX_DESCRIPTION_LENGTH;

    const { visibleAttendees, hiddenCount } = useMemo(() => {
        if (!event.attendees) {
            return { visibleAttendees: [] as IcsAttendee[], hiddenCount: 0 };
        }

        const total = event.attendees.length;
        if (showAllAttendees || total <= MAX_VISIBLE_ATTENDEES) {
            return { visibleAttendees: event.attendees, hiddenCount: 0 };
        }

        return {
            visibleAttendees: event.attendees.slice(0, MAX_VISIBLE_ATTENDEES),
            hiddenCount: total - MAX_VISIBLE_ATTENDEES,
        };
    }, [event.attendees, showAllAttendees]);

    const displayedDescription = useMemo(() => {
        if (!event.description) return null;
        if (showFullDescription || !descriptionTruncated) {
            return event.description;
        }
        return event.description.slice(0, MAX_DESCRIPTION_LENGTH) + "…";
    }, [event.description, showFullDescription]);

    return (
        <div className="calendar-invite__event">
            <header className="calendar-invite__header">
                <div className="calendar-invite__icon">
                    <Icon name="event" type={IconType.OUTLINED} />
                </div>
                <div className="calendar-invite__title-section">
                    <h3 className="calendar-invite__title">{event.summary}</h3>
                    {event.status && (
                        <Badge
                            className={`calendar-invite__event-status calendar-invite__event-status--${event.status.toLowerCase()}`}
                        >
                            {t(`event.status.${event.status.toLowerCase()}`)}
                        </Badge>
                    )}
                </div>
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
                {event.location && (
                    <div className="calendar-invite__detail-row">
                        <Icon
                            name="location_on"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <span>{linkifyText(event.location)}</span>
                    </div>
                )}

                {/* Organizer */}
                {event.organizer && (
                    <div className="calendar-invite__detail-row">
                        <Icon
                            name="person"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <ContactChip
                            contact={createContactFromAttendee(
                                event.organizer,
                            )}
                            displayEmail
                        />
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
                            <p>{linkifyText(displayedDescription)}</p>
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

                {/* Attendees */}
                {hasAttendees && (
                    <div className="calendar-invite__attendees">
                        <div className="calendar-invite__attendees-header">
                            <Icon
                                name="group"
                                type={IconType.OUTLINED}
                                className="calendar-invite__detail-icon"
                            />
                            <span>
                                {t("{{count}} attendees", {
                                    count: attendeeCount,
                                })}
                            </span>
                        </div>
                        <ul className="calendar-invite__attendee-list">
                            {visibleAttendees.map((attendee) => {
                                const statusInfo = getAttendeeStatusInfo(
                                    attendee.partstat,
                                    t,
                                );
                                return (
                                    <li
                                        key={attendee.email}
                                        className="calendar-invite__attendee"
                                    >
                                        <ContactChip
                                            contact={createContactFromAttendee(
                                                attendee,
                                            )}
                                        />
                                        <span
                                            className={`calendar-invite__attendee-status ${statusInfo.className}`}
                                            title={statusInfo.label}
                                        >
                                            <Icon
                                                name={statusInfo.icon}
                                                type={IconType.OUTLINED}
                                            />
                                            <span>{statusInfo.label}</span>
                                        </span>
                                    </li>
                                );
                            })}
                        </ul>
                        {attendeeCount > MAX_VISIBLE_ATTENDEES && (
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

// Exported for testing
export {
    durationToMs,
    getEventEnd,
    linkifyText,
    isAllDayEvent,
    formatEventDateRange,
    formatRecurrenceRule,
    getAttendeeStatusInfo,
    createContactFromAttendee,
};

export const CalendarInvite = ({
    attachment,
    canDownload = true,
}: CalendarInviteProps) => {
    const { t, i18n } = useTranslation();
    const [loadingState, setLoadingState] = useState<LoadingState>("loading");
    const [calendar, setCalendar] = useState<IcsCalendar | null>(null);
    const [retryCount, setRetryCount] = useState(0);

    const downloadUrl = AttachmentHelper.getDownloadUrl(attachment);
    const language = i18n.resolvedLanguage || "en";

    useEffect(() => {
        const cached = calendarCache.get(downloadUrl);
        if (cached) {
            setCalendar(cached);
            setLoadingState("success");
            return;
        }

        const abortController = new AbortController();

        const fetchAndParse = async () => {
            try {
                setLoadingState("loading");
                const response = await fetch(downloadUrl, {
                    credentials: "include",
                    signal: abortController.signal,
                });

                if (!response.ok) {
                    throw new Error(`HTTP error: ${response.status}`);
                }

                const icsContent = await response.text();
                const parsedCalendar = convertIcsCalendar(
                    undefined,
                    icsContent,
                );
                if (calendarCache.size >= MAX_CACHE_SIZE) {
                    const oldest = calendarCache.keys().next().value;
                    if (oldest) calendarCache.delete(oldest);
                }
                calendarCache.set(downloadUrl, parsedCalendar);
                setCalendar(parsedCalendar);
                setLoadingState("success");
            } catch (error) {
                if (
                    error instanceof Error &&
                    error.name === "AbortError"
                ) {
                    return;
                }
                console.error("Failed to parse calendar invite:", error);
                setLoadingState("error");
            }
        };

        fetchAndParse();

        return () => {
            abortController.abort();
        };
    }, [downloadUrl, retryCount]);

    const handleRetry = () => {
        calendarCache.delete(downloadUrl);
        setRetryCount((c) => c + 1);
    };

    const events = calendar?.events ?? [];
    const isCancellation = calendar?.method === "CANCEL";

    if (loadingState === "loading") {
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

    if (loadingState === "error" || !calendar) {
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
                    onClick={handleRetry}
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
                <EventCard key={event.uid || index} event={event} language={language} />
            ))}

            <footer className="calendar-invite__actions">
                {canDownload && (
                    <DownloadButton
                        downloadUrl={downloadUrl}
                        name={attachment.name}
                    />
                )}
            </footer>
        </article>
    );
};
