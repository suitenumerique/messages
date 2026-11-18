import { useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button, IconType } from "@gouvfr-lasuite/ui-components";
import { Icon } from "@/features/ui/components/icon";
import { useAuth } from "@/features/auth";
import { DEFAULT_CALENDAR_COLOR } from "./calendars";

export type ConflictInfo = {
    summary: string;
    start: string | null;
    end: string | null;
    // All-day events carry a date (not datetime): the timeline draws them
    // across the whole range instead of converting a date to a time.
    all_day?: boolean;
    calendar_name: string;
    calendar_id: string;
    calendar_color: string | null;
};

type CalendarOption = {
    id: string;
    name: string;
    color?: string | null;
};

export type Span = { start: number; end: number };

const HOUR = 60 * 60 * 1000;

export const readStoredIds = (key: string | null): string[] | null => {
    if (!key) return null;
    try {
        const ids = JSON.parse(window.localStorage.getItem(key) ?? "null");
        return Array.isArray(ids) ? ids.filter((id) => typeof id === "string") : null;
    } catch {
        return null;
    }
};

/**
 * The calendars checked for conflicts in a mailbox: the user's last choice,
 * kept in this browser per user and mailbox, else the given defaults.
 */
export function useConflictCalendarIds(
    mailboxId: string | undefined,
    calendarIds: string[],
    defaultIds: string[],
): [string[], (ids: string[]) => void] {
    const { user } = useAuth();
    const key = mailboxId && user?.id ? `calendar-conflicts:${user.id}:${mailboxId}` : null;
    const [chosen, setChosen] = useState<{ key: string | null; ids: string[] } | null>(null);
    const ids = (chosen?.key === key ? chosen.ids : readStoredIds(key)) ?? defaultIds;
    const choose = (next: string[]) => {
        setChosen({ key, ids: next });
        try {
            if (key) window.localStorage.setItem(key, JSON.stringify(next));
        } catch {
            // Storage can be unavailable (private mode); the choice then lasts
            // until the page is left.
        }
    };
    return [ids.filter((id) => calendarIds.includes(id)), choose];
}

/** Where a conflict sits in time; all-day ones (or missing times) cover the whole range. */
export const spanOf = (conflict: ConflictInfo): Span | null => {
    if (conflict.all_day || !conflict.start || !conflict.end) return null;
    return { start: new Date(conflict.start).getTime(), end: new Date(conflict.end).getTime() };
};

/**
 * The part of a span that falls within the invitation; all-day on either side
 * (null) means the whole of the other one.
 */
export const overlapOf = (span: Span | null, event: Span | null): Span | null => {
    if (!event) return span;
    if (!span) return event;
    const start = Math.max(span.start, event.start);
    const end = Math.min(span.end, event.end);
    return end > start ? { start, end } : null;
};

/** The hours the timeline covers (at least two) and its tick marks. */
export const timelineScale = (spans: (Span | null)[]) => {
    const timed = spans.filter((s): s is Span => s !== null);
    if (timed.length === 0) return { from: 0, to: 2 * HOUR, ticks: [] as number[] };
    const from = Math.floor(Math.min(...timed.map((s) => s.start)) / HOUR) * HOUR;
    const to = Math.max(Math.ceil(Math.max(...timed.map((s) => s.end)) / HOUR) * HOUR, from + 2 * HOUR);
    const hours = (to - from) / HOUR;
    const step = hours <= 4 ? 1 : hours <= 8 ? 2 : 4;
    const ticks = Array.from({ length: Math.floor(hours / step) + 1 }, (_, i) => from + i * step * HOUR);
    return { from, to, ticks };
};

const swatch = (calendar: CalendarOption) => (
    <span
        className="calendar-select__color"
        style={{ backgroundColor: calendar.color || DEFAULT_CALENDAR_COLOR }}
    />
);

/** A bar on the timeline with its title on it. */
const TimelineBar = ({
    style,
    text,
    title,
    saved = false,
}: {
    style: CSSProperties;
    text: string;
    title: string;
    saved?: boolean;
}) => (
    <>
        <span className={`conflict-timeline__bar${saved ? " conflict-timeline__bar--saved" : ""}`} style={style} />
        <span
            className={`conflict-timeline__bar-text${saved ? " conflict-timeline__bar-text--saved" : ""}`}
            style={{ left: style.left, width: style.width }}
            title={title}
        >
            {text}
        </span>
    </>
);

type Invitation = { start: Date; end: Date; allDay: boolean; title: string };

const ConflictTimeline = ({
    conflicts,
    calendars,
    invitation,
    lockedId,
    savedIds,
    fetchedIds,
    language,
    onRemove,
}: {
    conflicts: ConflictInfo[];
    calendars: CalendarOption[];
    invitation: Invitation;
    lockedId: string | null;
    savedIds: string[];
    fetchedIds: string[];
    language: string;
    onRemove: (id: string) => void;
}) => {
    const { t } = useTranslation();
    const time = new Intl.DateTimeFormat(language, { hour: "numeric", minute: "2-digit" });
    const event = invitation.allDay ? null : { start: invitation.start.getTime(), end: invitation.end.getTime() };
    const { from, to, ticks } = timelineScale([event, ...conflicts.map(spanOf)]);
    const place = (span: Span | null) =>
        span
            ? { left: `${((span.start - from) / (to - from)) * 100}%`, width: `${((span.end - span.start) / (to - from)) * 100}%` }
            : { left: "0%", width: "100%" };
    const label = (span: Span | null) =>
        span ? `${time.format(span.start)}–${time.format(span.end)}` : t("All day");

    return (
        <div className="conflict-timeline">
            {ticks.length > 0 && (
                <div className="conflict-timeline__axis" aria-hidden="true">
                    {ticks.map((tick) => (
                        <span key={tick} style={{ left: place({ start: tick, end: tick }).left }}>
                            {time.format(tick)}
                        </span>
                    ))}
                </div>
            )}
            <div className="conflict-timeline__row conflict-timeline__row--invitation">
                <span className="conflict-timeline__label">{t("Invitation")}</span>
                <span className="conflict-timeline__track">
                    <TimelineBar
                        style={place(event)}
                        text={invitation.title}
                        title={`${invitation.title} · ${label(event)}`}
                    />
                </span>
                <span />
            </div>
            {calendars.map((calendar) => {
                const events = conflicts.filter((c) => c.calendar_id === calendar.id);
                const color = { "--bar-color": calendar.color || DEFAULT_CALENDAR_COLOR } as CSSProperties;
                const saved = savedIds.includes(calendar.id);
                // Just added: its result is still on its way.
                const pending = !fetchedIds.includes(calendar.id);
                return (
                    <div key={calendar.id} className="conflict-timeline__row">
                        <span className="conflict-timeline__label">
                            <span className="conflict-timeline__calendar">
                                {swatch(calendar)}
                                <span className="conflict-timeline__calendar-name">{calendar.name}</span>
                            </span>
                        </span>
                        <span className="conflict-timeline__track">
                            <span className="conflict-timeline__window" style={place(event)} />
                            {events.length === 0 && !saved && !pending && (
                                <span className="conflict-timeline__free">{t("Free")}</span>
                            )}
                            {saved && (
                                <TimelineBar
                                    saved
                                    style={{ ...place(event), ...color }}
                                    text={invitation.title}
                                    title={`${invitation.title} · ${label(event)}`}
                                />
                            )}
                            {events.map((e, index) => {
                                const span = spanOf(e);
                                const overlap = overlapOf(span, event);
                                return (
                                    <span key={`${e.summary}-${e.start ?? index}`}>
                                        <TimelineBar
                                            style={{ ...place(span), ...color }}
                                            text={e.summary}
                                            title={`${e.summary} · ${label(span)}`}
                                        />
                                        {overlap && <span className="conflict-timeline__overlap" style={place(overlap)} />}
                                    </span>
                                );
                            })}
                        </span>
                        {calendar.id === lockedId ? (
                            <span />
                        ) : (
                            <Button
                                className="conflict-timeline__remove"
                                size="small"
                                variant="tertiary"
                                color="neutral"
                                icon={<Icon name="close" type={IconType.OUTLINED} />}
                                aria-label={t("Remove {{name}}", { name: calendar.name })}
                                title={t("Remove {{name}}", { name: calendar.name })}
                                onClick={() => onRemove(calendar.id)}
                            />
                        )}
                    </div>
                );
            })}
        </div>
    );
};

/**
 * Conflicts of an invitation with the checked calendars, drawn on a small
 * timeline next to the invitation itself. Calendars are added by choosing them
 * to save the event in, and removed with the cross on their row.
 */
export const ConflictCheck = ({
    calendars,
    checkedIds,
    fetchedIds,
    onCheck,
    conflicts,
    event,
    language,
    lockedId,
    savedIds,
    footer,
}: {
    calendars: CalendarOption[];
    checkedIds: string[];
    fetchedIds: string[];
    onCheck: (ids: string[]) => void;
    conflicts: ConflictInfo[];
    event: Invitation;
    language: string;
    lockedId: string | null;
    savedIds: string[];
    footer?: ReactNode;
}) => {
    const { t } = useTranslation();
    // A previous result can still hold conflicts of a calendar just removed.
    const shown = conflicts.filter((c) => checkedIds.includes(c.calendar_id));
    const hasConflicts = shown.length > 0;

    return (
        <div
            className={`calendar-invite__conflicts${hasConflicts ? "" : " calendar-invite__conflicts--none"}`}
            role={hasConflicts ? "alert" : "status"}
        >
            <div className="calendar-invite__conflicts-header">
                <Icon name={hasConflicts ? "warning" : "event_available"} type={IconType.OUTLINED} />
                <span>
                    {hasConflicts
                        ? t("{{count}} conflicting events", { count: shown.length })
                        : t("No conflicts")}
                </span>
            </div>
            <ConflictTimeline
                conflicts={shown}
                calendars={checkedIds
                    .map((id) => calendars.find((calendar) => calendar.id === id))
                    .filter((calendar): calendar is CalendarOption => !!calendar)}
                invitation={event}
                lockedId={lockedId}
                savedIds={savedIds}
                fetchedIds={fetchedIds}
                language={language}
                onRemove={(id) => onCheck(checkedIds.filter((checked) => checked !== id))}
            />
            {footer}
        </div>
    );
};
