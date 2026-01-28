import { useState, useMemo } from "react";
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
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { ContactChip } from "@/features/ui/components/contact-chip";
import { Badge } from "@/features/ui/components/badge";
import {
    getEventEnd,
    linkifyText,
    formatEventDateRange,
    formatRecurrenceRule,
    getAttendeeStatusInfo,
    createContactFromAttendee,
} from "./calendar-helper";

type CalendarInviteProps = {
    attachment: Attachment;
    canDownload?: boolean;
};

const MAX_VISIBLE_ATTENDEES = 3;
const MAX_DESCRIPTION_LENGTH = 200;

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
            {t("Download invitation")}
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

const fetchAndParseCalendar = async (url: string): Promise<IcsCalendar> => {
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
    }
    const icsContent = await response.text();
    return convertIcsCalendar(undefined, icsContent);
};

export const CalendarInvite = ({
    attachment,
    canDownload = true,
}: CalendarInviteProps) => {
    const { t, i18n } = useTranslation();

    const downloadUrl = AttachmentHelper.getDownloadUrl(attachment);
    const language = i18n.resolvedLanguage || "en";

    const { data: calendar, isLoading, isError, refetch } = useQuery<IcsCalendar>({
        queryKey: ["calendar-invite", downloadUrl],
        queryFn: () => fetchAndParseCalendar(downloadUrl),
        meta: { noGlobalError: true },
    });

    const events = calendar?.events ?? [];
    const isCancellation = calendar?.method === "CANCEL";

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
