import { useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button, IconType } from "@gouvfr-lasuite/ui-components";
import { Icon } from "@/features/ui/components/icon";
import { Badge } from "@/features/ui/components/badge";
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

// All-day events count as starting last, so they are drawn under timed ones.
const startOf = (conflict: ConflictInfo) => spanOf(conflict)?.start ?? Number.MAX_SAFE_INTEGER;

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

type Invitation = { start: Date; end: Date; allDay: boolean; title: string };

const ConflictTimeline = ({
    conflicts,
    calendars,
    invitation,
    lockedId,
    savedIds,
    fetchedIds,
    loading,
    language,
    onRemove,
}: {
    conflicts: ConflictInfo[];
    calendars: CalendarOption[];
    invitation: Invitation;
    lockedId: string | null;
    savedIds: string[];
    fetchedIds: string[];
    loading: boolean;
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

    // Laid out like the free/busy timeline of the Calendars event dialog: hour
    // columns, busy time per calendar, and the invitation striped across all rows.
    return (
        <div className="conflict-timeline">
            <div className="conflict-timeline__lanes" aria-hidden="true">
                {ticks
                    .filter((tick) => tick > from && tick < to)
                    .map((tick) => (
                        <span key={tick} className="conflict-timeline__hour" style={{ left: place({ start: tick, end: tick }).left }} />
                    ))}
            </div>
            <div className="conflict-timeline__axis" aria-hidden="true">
                {ticks
                    .filter((tick) => tick < to)
                    .map((tick) => (
                        <span key={tick} style={{ left: place({ start: tick, end: tick }).left }}>
                            {time.format(tick)}
                        </span>
                    ))}
            </div>
            {calendars.map((calendar) => {
                // Events that overlap each other: the one that starts first is drawn on top.
                const events = conflicts
                    .filter((c) => c.calendar_id === calendar.id)
                    .sort((a, b) => startOf(b) - startOf(a));
                const color = { "--bar-color": calendar.color || DEFAULT_CALENDAR_COLOR } as CSSProperties;
                const saved = savedIds.includes(calendar.id);
                const checked = fetchedIds.includes(calendar.id);
                return (
                    <div key={calendar.id} className="conflict-timeline__row" style={color}>
                        <span className="conflict-timeline__label">
                            {swatch(calendar)}
                            <span className="conflict-timeline__calendar-name">{calendar.name}</span>
                            {calendar.id !== lockedId && (
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
                        </span>
                        <span className="conflict-timeline__track">
                            {!checked && !saved && !loading && (
                                <span className="conflict-timeline__unchecked">{t("Not checked")}</span>
                            )}
                            {events.map((e, index) => {
                                const span = spanOf(e);
                                return (
                                    <span
                                        key={`${e.summary}-${e.start}-${index}`}
                                        className="conflict-timeline__bar"
                                        style={place(span)}
                                        title={`${e.summary} · ${label(span)}`}
                                    >
                                        <span className="conflict-timeline__bar-text">{e.summary}</span>
                                        <span className="c__offscreen">{` ${label(span)}`}</span>
                                    </span>
                                );
                            })}
                            {saved && (
                                <span
                                    className="conflict-timeline__bar conflict-timeline__bar--saved"
                                    style={place(event)}
                                    title={`${invitation.title} · ${label(event)}`}
                                >
                                    <span className="conflict-timeline__bar-text">{invitation.title}</span>
                                </span>
                            )}
                        </span>
                    </div>
                );
            })}
            <div className="conflict-timeline__lanes conflict-timeline__lanes--rows" aria-hidden="true">
                <span className="conflict-timeline__invitation" style={place(event)} />
            </div>
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
    loading,
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
    loading: boolean;
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
    const pending = loading && checkedIds.some((id) => !fetchedIds.includes(id));

    return (
        <div className="calendar-invite__conflicts" role={hasConflicts ? "alert" : "status"}>
            <div className="calendar-invite__conflicts-header">
                <span>
                    {event.start.toLocaleDateString(language, { weekday: "short", month: "short", day: "numeric" })}
                </span>
                <Badge
                    color={hasConflicts ? "error" : pending ? "neutral" : "success"}
                    variant="secondary"
                    compact
                >
                    {hasConflicts
                        ? t("{{count}} conflicting events", { count: shown.length })
                        : pending
                          ? t("Checking calendars…")
                          : t("No conflicts")}
                </Badge>
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
                loading={loading}
                language={language}
                onRemove={(id) => onCheck(checkedIds.filter((checked) => checked !== id))}
            />
            {footer}
        </div>
    );
};
