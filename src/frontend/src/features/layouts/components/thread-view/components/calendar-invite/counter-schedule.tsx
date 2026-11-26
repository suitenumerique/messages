import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { IconType, Spinner } from "@gouvfr-lasuite/ui-components";
import { IcsEvent } from "ts-ics";
import { Icon } from "@/features/ui/components/icon";
import { ContactChip } from "@/features/ui/components/contact-chip";
import { fetchAPI } from "@/features/api/fetch-api";
import { CalendarStoredEventResponse } from "@/features/api/gen/models";
import {
    createContactFromAttendee,
    findCounterProposer,
    formatEventDateRange,
    getEventEnd,
} from "./calendar-helper";

type StoredEventApiResponse = {
    data: CalendarStoredEventResponse;
    status: number;
};

/**
 * Return a date-only ISO value ("2026-12-01") as local midnight.
 *
 * ``new Date("2026-12-01")`` gives UTC midnight. West of UTC, that is the
 * day before in local time.
 */
const parseLocalDate = (iso: string): Date => {
    const [year, month, day] = iso.split("-").map(Number);
    return new Date(year, month - 1, day);
};

const ScheduleRow = ({
    icon,
    label,
    children,
}: {
    icon: string;
    label: string;
    children: ReactNode;
}) => (
    <div className="calendar-invite__detail-row">
        <Icon
            name={icon}
            type={IconType.OUTLINED}
            className="calendar-invite__detail-icon"
        />
        <span className="calendar-invite__schedule-label">{label}</span>
        <span>{children}</span>
    </div>
);

/**
 * Times of a proposal for a new time (iTIP COUNTER).
 *
 * Shows the proposer, the date of the series that the proposal is for, the
 * current time of the event in the user's calendars and the proposed time.
 * The current time is not in the message. The component reads it from the
 * calendars when it gets ``icsContent``.
 */
export const CounterSchedule = ({
    event,
    language,
    mailboxId,
    icsContent,
    isWaiting,
}: {
    event: IcsEvent;
    language: string;
    mailboxId?: string;
    icsContent?: string;
    // True while the card does not yet know if it can read the calendars.
    isWaiting: boolean;
}) => {
    const { t } = useTranslation();
    const proposer = findCounterProposer(event);

    const { data, isLoading } = useQuery<StoredEventApiResponse>({
        queryKey: ["calendar-stored-event", mailboxId, icsContent],
        queryFn: () =>
            fetchAPI<StoredEventApiResponse>(
                `/api/v1.0/mailboxes/${mailboxId}/calendar/stored-event/`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ ics_data: icsContent }),
                },
            ),
        enabled: !!mailboxId && !!icsContent,
        meta: { noGlobalError: true },
    });

    const stored = data?.data.event;
    let currentTime: ReactNode = t("Not available");
    if (isWaiting || isLoading) {
        currentTime = <Spinner size="sm" />;
    } else if (stored) {
        const toDate = stored.all_day ? parseLocalDate : (iso: string) => new Date(iso);
        currentTime = formatEventDateRange(
            toDate(stored.start),
            toDate(stored.end),
            language,
        );
    }

    const occurrence = event.recurrenceId?.value;
    const proposedStart = event.start?.date;

    return (
        <>
            <div className="calendar-invite__detail-row">
                <Icon
                    name="person"
                    type={IconType.OUTLINED}
                    className="calendar-invite__detail-icon"
                />
                {proposer ? (
                    <>
                        <ContactChip contact={createContactFromAttendee(proposer)} />
                        <span className="calendar-invite__organizer-label">
                            {t("Proposes a new time")}
                        </span>
                    </>
                ) : (
                    <span>{t("A guest proposes a new time")}</span>
                )}
            </div>
            {occurrence && (
                <ScheduleRow icon="repeat" label={t("Occurrence")}>
                    {new Intl.DateTimeFormat(language, {
                        weekday: "long",
                        year: "numeric",
                        month: "long",
                        day: "numeric",
                        // A DATE value is a UTC midnight. A local format can
                        // show the day before.
                        timeZone: occurrence.type === "DATE" ? "UTC" : undefined,
                    }).format(occurrence.date)}
                </ScheduleRow>
            )}
            <ScheduleRow icon="schedule" label={t("Current time")}>
                {currentTime}
            </ScheduleRow>
            {proposedStart && (
                <ScheduleRow icon="update" label={t("Proposed time")}>
                    {formatEventDateRange(
                        proposedStart,
                        getEventEnd(event),
                        language,
                    )}
                </ScheduleRow>
            )}
        </>
    );
};
