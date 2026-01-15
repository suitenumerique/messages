import { useEffect, useState, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { convertIcsCalendar, IcsCalendar, IcsEvent, IcsAttendee } from "ts-ics";
import { Attachment, Contact } from "@/features/api/gen/models";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { ContactChip } from "@/features/ui/components/contact-chip";

type CalendarInviteProps = {
    attachment: Attachment;
    canDownload?: boolean;
};

type LoadingState = "loading" | "success" | "error";

const MAX_VISIBLE_ATTENDEES = 3;

/**
 * Convert URL strings in text to clickable links
 */
function linkifyText(text: string): React.ReactNode[] {
    const urlRegex = /(https?:\/\/[^\s<>"{}|\\^`[\]]+)/gi;
    const parts = text.split(urlRegex);

    return parts.map((part, index) => {
        if (urlRegex.test(part)) {
            // Reset regex lastIndex since we're reusing it
            urlRegex.lastIndex = 0;
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
 * Format a date range for display, handling all-day events and same-day events
 */
function formatEventDateRange(
    start: Date,
    end: Date | undefined,
    language: string
): string {
    const dateFormatter = new Intl.DateTimeFormat(language, {
        weekday: "long",
        year: "numeric",
        month: "long",
        day: "numeric",
    });

    const timeFormatter = new Intl.DateTimeFormat(language, {
        hour: "numeric",
        minute: "2-digit",
    });

    const startDate = dateFormatter.format(start);
    const startTime = timeFormatter.format(start);

    if (!end) {
        return `${startDate} ${startTime}`;
    }

    const endDate = dateFormatter.format(end);
    const endTime = timeFormatter.format(end);

    // Check if same day
    const sameDay = start.toDateString() === end.toDateString();

    if (sameDay) {
        return `${startDate}, ${startTime} - ${endTime}`;
    }

    return `${startDate} ${startTime} - ${endDate} ${endTime}`;
}

/**
 * Get the appropriate icon and label for an attendee's participation status
 */
function getAttendeeStatusInfo(
    partstat: IcsAttendee["partstat"],
    t: (key: string) => string
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
    attendee: { email?: string; name?: string },
    index: number
): Contact {
    return {
        id: `calendar-${attendee.email || index}`,
        email: attendee.email || "",
        name: attendee.name || null,
    };
}

export const CalendarInvite = ({
    attachment,
    canDownload = true,
}: CalendarInviteProps) => {
    const { t, i18n } = useTranslation();
    const [loadingState, setLoadingState] = useState<LoadingState>("loading");
    const [calendar, setCalendar] = useState<IcsCalendar | null>(null);
    const [errorMessage, setErrorMessage] = useState<string>("");
    const [showAllAttendees, setShowAllAttendees] = useState(false);

    const downloadUrl = AttachmentHelper.getDownloadUrl(attachment);

    useEffect(() => {
        const fetchAndParseCalendar = async () => {
            try {
                setLoadingState("loading");
                const response = await fetch(downloadUrl, {
                    credentials: "include",
                });

                if (!response.ok) {
                    throw new Error(`HTTP error: ${response.status}`);
                }

                const icsContent = await response.text();
                const parsedCalendar = convertIcsCalendar(undefined, icsContent);
                setCalendar(parsedCalendar);
                setLoadingState("success");
            } catch (error) {
                console.error("Failed to parse calendar invite:", error);
                setErrorMessage(
                    error instanceof Error
                        ? error.message
                        : t("Failed to load calendar invite")
                );
                setLoadingState("error");
            }
        };

        fetchAndParseCalendar();
    }, [downloadUrl, t]);

    // Get the first event (most calendar invites have one event)
    const event: IcsEvent | undefined = calendar?.events?.[0];

    // Memoize visible attendees based on showAllAttendees state
    const { visibleAttendees, hiddenCount } = useMemo(() => {
        if (!event?.attendees) {
            return { visibleAttendees: [], hiddenCount: 0 };
        }

        const total = event.attendees.length;
        if (showAllAttendees || total <= MAX_VISIBLE_ATTENDEES) {
            return { visibleAttendees: event.attendees, hiddenCount: 0 };
        }

        return {
            visibleAttendees: event.attendees.slice(0, MAX_VISIBLE_ATTENDEES),
            hiddenCount: total - MAX_VISIBLE_ATTENDEES,
        };
    }, [event?.attendees, showAllAttendees]);

    if (loadingState === "loading") {
        return (
            <div className="calendar-invite calendar-invite--loading">
                <Spinner />
                <span>{t("Loading calendar invite...")}</span>
            </div>
        );
    }

    if (loadingState === "error" || !calendar) {
        return (
            <div className="calendar-invite calendar-invite--error">
                <Icon name="error" type={IconType.OUTLINED} />
                <span>{errorMessage || t("Failed to load calendar invite")}</span>
                {canDownload && (
                    <Button
                        size="small"
                        variant="tertiary"
                        icon={<Icon name="download" />}
                        href={downloadUrl}
                        download={attachment.name}
                    >
                        {t("Download .ics file")}
                    </Button>
                )}
            </div>
        );
    }

    if (!event) {
        return (
            <div className="calendar-invite calendar-invite--empty">
                <Icon name="event" type={IconType.OUTLINED} />
                <span>{t("No event found in calendar invite")}</span>
                {canDownload && (
                    <Button
                        size="small"
                        variant="tertiary"
                        icon={<Icon name="download" />}
                        href={downloadUrl}
                        download={attachment.name}
                    >
                        {t("Download .ics file")}
                    </Button>
                )}
            </div>
        );
    }

    const eventStart = event.start?.date;
    const eventEnd = event.end?.date;
    const hasAttendees = event.attendees && event.attendees.length > 0;

    return (
        <article className="calendar-invite">
            <header className="calendar-invite__header">
                <div className="calendar-invite__icon">
                    <Icon name="event" type={IconType.OUTLINED} />
                </div>
                <div className="calendar-invite__title-section">
                    <h3 className="calendar-invite__title">{event.summary}</h3>
                    {event.status && (
                        <span
                            className={`calendar-invite__event-status calendar-invite__event-status--${event.status.toLowerCase()}`}
                        >
                            {t(`event.status.${event.status.toLowerCase()}`)}
                        </span>
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
                                i18n.resolvedLanguage || "en"
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
                            contact={createContactFromAttendee(event.organizer, -1)}
                            displayEmail
                        />
                    </div>
                )}

                {/* Description */}
                {event.description && (
                    <div className="calendar-invite__description">
                        <Icon
                            name="notes"
                            type={IconType.OUTLINED}
                            className="calendar-invite__detail-icon"
                        />
                        <p>{linkifyText(event.description)}</p>
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
                                    count: event.attendees!.length,
                                })}
                            </span>
                        </div>
                        <ul className="calendar-invite__attendee-list">
                            {visibleAttendees.map((attendee, index) => {
                                const statusInfo = getAttendeeStatusInfo(
                                    attendee.partstat,
                                    t
                                );
                                return (
                                    <li
                                        key={`${attendee.email}-${index}`}
                                        className="calendar-invite__attendee"
                                    >
                                        <ContactChip
                                            contact={createContactFromAttendee(attendee, index)}
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
                        {hiddenCount > 0 && (
                            <button
                                type="button"
                                className="calendar-invite__show-more"
                                onClick={() => setShowAllAttendees(true)}
                            >
                                {t("Show {{count}} more", { count: hiddenCount })}
                            </button>
                        )}
                        {showAllAttendees && event.attendees!.length > MAX_VISIBLE_ATTENDEES && (
                            <button
                                type="button"
                                className="calendar-invite__show-more"
                                onClick={() => setShowAllAttendees(false)}
                            >
                                {t("Show less")}
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* Actions */}
            <footer className="calendar-invite__actions">
                {canDownload && (
                    <Button
                        size="small"
                        variant="primary"
                        icon={<Icon name="download" />}
                        href={downloadUrl}
                        download={attachment.name}
                    >
                        {t("Download .ics file")}
                    </Button>
                )}
            </footer>
        </article>
    );
};
