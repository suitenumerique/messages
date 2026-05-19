import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TextHelper } from "@/features/utils/text-helper";
import { DateHelper } from "@/features/utils/date-helper";
import { Message, ThreadEvent as ThreadEventType, ThreadEventTypeEnum, ThreadEventAssigneesData, ThreadEventIMData } from "@/features/api/gen/models";
import { useThreadsEventsDestroy } from "@/features/api/gen/thread-events/thread-events";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth";
import { useMailboxContext, TimelineItem } from "@/features/providers/mailbox";
import { Badge } from "@/features/ui/components/badge";
import { AVATAR_COLORS, Icon, IconSize, IconType, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { Button, useModals } from "@gouvfr-lasuite/cunningham-react";
import useCopyDeepLink from "@/features/message/use-copy-deep-link";
import clsx from "clsx";
import { buildAssignmentMessage } from "./assignment-message";

const TWO_MINUTES_MS = 2 * 60 * 1000;

type TypedThreadEvent<T extends ThreadEventTypeEnum> = ThreadEventType & {
  type: T,
  data: T extends 'im' ? ThreadEventIMData
  : T extends 'assign' ? ThreadEventAssigneesData
  : T extends 'unassign' ? ThreadEventAssigneesData
  : unknown
};

const isAssignmentEvent = (event: ThreadEventType): event is TypedThreadEvent<'assign' | 'unassign'> =>
    event.type === ThreadEventTypeEnum.assign || event.type === ThreadEventTypeEnum.unassign;

const isIMEvent = (event: ThreadEventType): event is TypedThreadEvent<'im'> =>
    event.type === ThreadEventTypeEnum.im;

/**
 * Rendered timeline item used by the thread view.
 *
 * Adds a ``collapsed_events`` variant on top of ``TimelineItem`` that hides a
 * run of 3+ consecutive assignment events behind a single summary line with
 * an expand toggle. Progressive disclosure keeps assignment churn out of the
 * way without losing audit information.
 *
 * The ``collapsed_events`` payload is intentionally narrowed to assignment
 * events: ``CollapsedEventsGroup`` and its summary string ("assignment
 * changes") only know how to render that subset, so the type contract acts as
 * a compile-time guard against silently absorbing future system event kinds.
 */
export type RenderItem =
    | { kind: 'message'; data: Message; created_at: string }
    | { kind: 'event'; data: ThreadEventType; created_at: string }
    | { kind: 'collapsed_events'; events: TypedThreadEvent<'assign' | 'unassign'>[]; created_at: string };

const COLLAPSE_THRESHOLD = 3;

/**
 * Groups consecutive assignment ThreadEvents (``assign``/``unassign``) into
 * ``collapsed_events`` runs when 3 or more pile up between other items.
 * Shorter runs stay as individual inline events so the timeline keeps a
 * natural chronological flow.
 *
 * Any other timeline item — message, IM, or non-assignment system event —
 * flushes the buffer, so collapsed runs only ever contain assignment history.
 * That is exactly the subset ``CollapsedEventsGroup`` can render; future
 * system event kinds fall through to the generic inline renderer until they
 * get explicit support.
 */
export const groupSystemEvents = (items: readonly TimelineItem[]): RenderItem[] => {
    const result: RenderItem[] = [];
    let buffer: TypedThreadEvent<'assign' | 'unassign'>[] = [];

    const flushBuffer = () => {
        if (buffer.length === 0) return;
        if (buffer.length >= COLLAPSE_THRESHOLD) {
            const last = buffer[buffer.length - 1];
            result.push({ kind: 'collapsed_events', events: [...buffer], created_at: last.created_at });
        } else {
            for (const event of buffer) {
                result.push({ kind: 'event', data: event, created_at: event.created_at });
            }
        }
        buffer = [];
    };

    for (const item of items) {
        if (item.type === 'event' && isAssignmentEvent(item.data)) {
            buffer.push(item.data);
            continue;
        }
        flushBuffer();
        if (item.type === 'event') {
            result.push({ kind: 'event', data: item.data, created_at: item.created_at });
        } else {
            result.push({ kind: 'message', data: item.data, created_at: item.created_at });
        }
    }
    flushBuffer();
    return result;
};

/**
 * Computes the avatar palette color for a given name.
 * Mirrors the hash logic used by UserAvatar from @gouvfr-lasuite/ui-kit.
 */
const getAvatarColor = (name: string): string => {
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
        hash += name.charCodeAt(i);
    }
    return AVATAR_COLORS[hash % AVATAR_COLORS.length];
};

type ThreadEventProps = {
    event: ThreadEventType;
    isCondensed?: boolean;
    onEdit?: (event: ThreadEventType) => void;
    onDelete?: (eventId: string) => void;
    /**
     * Ref setter wired by the parent when the event carries an unread mention,
     * used by the IntersectionObserver that marks mentions as read on scroll.
     * Receives the bubble element — the observer needs a target with a real
     * bounding box to reliably report intersections.
     */
    mentionRef?: (el: HTMLDivElement | null) => void;
    /**
     * True when this event OR any subsequent event condensed with it carries
     * an unread mention for the current user. Drives the badge shown in the
     * header. Computed by the parent so the first event of a condensed group
     * surfaces mentions that would otherwise be hidden on condensed siblings.
     */
    hasUnreadMention?: boolean;
};

/**
 * Returns true if this IM event should show a condensed view (no header),
 * because the previous event is also an IM from the same author within 2 minutes.
 */
export const isCondensed = (event: ThreadEventType, previousEvent?: ThreadEventType | null): boolean => {
    if (!previousEvent) return false;
    if (event.type !== ThreadEventTypeEnum.im || previousEvent.type !== ThreadEventTypeEnum.im) return false;
    if (event.author?.id !== previousEvent.author?.id) return false;
    const diff = new Date(event.created_at).getTime() - new Date(previousEvent.created_at).getTime();
    return Math.abs(diff) < TWO_MINUTES_MS;
};

/**
 * Renders a thread event in the timeline.
 * For type=im: renders as a chat bubble with avatar, author name, and content.
 * Consecutive IMs from the same author within 2 minutes are condensed (no header).
 * For other types: renders a minimal card with type badge and data.
 */
export const ThreadEvent = ({ event, isCondensed = false, onEdit, onDelete, mentionRef, hasUnreadMention = false }: ThreadEventProps) => {
    const { t, i18n } = useTranslation();
    const { user } = useAuth();
    const modals = useModals();
    const { invalidateThreadEvents } = useMailboxContext();
    const copyDeepLink = useCopyDeepLink();
    const isAuthor = event.author?.id === user?.id;
    const isEdited = Math.abs(new Date(event.updated_at).getTime() - new Date(event.created_at).getTime()) > 1000;
    // Edit/delete actions are only available to the author while the
    // server-side edit delay has not elapsed (MAX_THREAD_EVENT_EDIT_DELAY).
    const canModify = isAuthor && event.is_editable;

    const deleteEvent = useThreadsEventsDestroy();
    const [showActions, setShowActions] = useState(false);
    const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const bubbleRef = useRef<HTMLDivElement>(null);

    const [pressing, setPressing] = useState(false);

    const handleTouchStart = useCallback(() => {
        setPressing(true);
        longPressTimer.current = setTimeout(() => {
            setPressing(false);
            setShowActions(true);
            navigator.vibrate?.(50);
        }, 500);
    }, []);

    const cancelLongPress = useCallback(() => {
        setPressing(false);
        if (longPressTimer.current) {
            clearTimeout(longPressTimer.current);
            longPressTimer.current = null;
        }
    }, []);

    useEffect(() => {
        if (!showActions) return;
        const handleClickOutside = (e: MouseEvent | TouchEvent) => {
            if (bubbleRef.current && !bubbleRef.current.contains(e.target as Node)) {
                setShowActions(false);
            }
        };
        document.addEventListener("mousedown", handleClickOutside);
        document.addEventListener("touchstart", handleClickOutside);
        return () => {
            document.removeEventListener("mousedown", handleClickOutside);
            document.removeEventListener("touchstart", handleClickOutside);
        };
    }, [showActions]);

    const handleCopyLink = async () => {
        const copied = await copyDeepLink({ eventId: event.id });
        if (copied) setShowActions(false);
    };

    const handleDelete = async () => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete internal comment')}</span>,
            children: t('Are you sure you want to delete this internal comment? It will be deleted for all users. This action cannot be undone.'),
        });
        if (decision !== 'delete') return;
        deleteEvent.mutate(
            { threadId: event.thread, id: event.id },
            { onSuccess: () => {
                onDelete?.(event.id);
                invalidateThreadEvents();
            } },
        );
    };

    if (isIMEvent(event)) {
        const authorName = event.author?.full_name || event.author?.email || "";
        const avatarColor = getAvatarColor(authorName);
        const isMentioned = user
            ? event.data?.mentions?.map((m) => m.id)?.includes(user.id)
            : false;
        const content = event.data?.content ?? "";

        const imClasses = clsx(
            "thread-event",
            "thread-event--im",
            {
                "thread-event--condensed": isCondensed,
            },
        );

        const bubbleStyle = {
            "--thread-event-color": `var(--c--contextuals--background--palette--${avatarColor}--primary)`,
        } as React.CSSProperties;

        return (
            <div className={imClasses} id={`thread-event-${event.id}`}>
                <div
                    ref={(el) => {
                        // Combined ref: keeps the local bubbleRef (used for
                        // click-outside detection) and the parent-provided
                        // mentionRef (used by the IntersectionObserver that
                        // acknowledges unread mentions on scroll) in sync.
                        bubbleRef.current = el;
                        mentionRef?.(el);
                    }}
                    className={`thread-event__bubble${showActions ? " thread-event__bubble--actions-visible" : ""}`}
                    style={bubbleStyle}
                    data-event-id={event.id}
                >
                    {!isCondensed && (
                        <div className="thread-event__header">
                            <span className="thread-event__author">
                                <UserAvatar fullName={event.author?.full_name || event.author?.email || t("Unknown")} size="xsmall" />
                                {event.author?.full_name || event.author?.email || t("Unknown")}
                            </span>
                            <span className="thread-event__time">
                                {t('{{date}} at {{time}}', {
                                    date: DateHelper.formatDate(event.created_at, i18n.resolvedLanguage, false),
                                    time: new Date(event.created_at).toLocaleString(i18n.resolvedLanguage, {
                                        minute: '2-digit',
                                        hour: '2-digit',
                                    }),
                                })}
                            </span>
                            {hasUnreadMention && (
                                <Badge
                                    color="warning"
                                    variant="secondary"
                                    compact
                                    role="status"
                                    className="thread-event__mention-badge"
                                >
                                    <Icon
                                        type={IconType.OUTLINED}
                                        size={IconSize.X_SMALL}
                                        name="alternate_email"
                                        aria-hidden="true"
                                    />
                                    {t('Unread mention')}
                                </Badge>
                            )}
                        </div>
                    )}
                    <div
                        className={`thread-event__content${pressing ? " thread-event__content--pressing" : ""}`}
                        onTouchStart={canModify ? handleTouchStart : undefined}
                        onTouchEnd={canModify ? cancelLongPress : undefined}
                        onTouchMove={canModify ? cancelLongPress : undefined}
                        onTouchCancel={canModify ? cancelLongPress : undefined}
                    >
                        {TextHelper.renderLinks(
                          TextHelper.renderMentions(
                              content,
                              isMentioned ? user?.full_name ?? undefined : undefined,
                              { baseClassName: "thread-event" }
                          )
                        )}
                        {isEdited && (
                            <span className="thread-event__edited-badge">({t("edited")})</span>
                        )}
                    </div>
                    <div className="thread-event__actions">
                        <Button
                            size="nano"
                            variant="tertiary"
                            color="brand"
                            icon={<Icon type={IconType.OUTLINED} name="link" aria-hidden="true" />}
                            aria-label={t("Copy link to comment")}
                            title={t("Copy link to comment")}
                            onClick={handleCopyLink}
                        />
                        {canModify && (
                            <>
                                <Button
                                    size="nano"
                                    variant="tertiary"
                                    color="brand"
                                    icon={<Icon type={IconType.OUTLINED} name="edit" aria-hidden="true" />}
                                    aria-label={t("Edit")}
                                    title={t("Edit")}
                                    onClick={() => {
                                        setShowActions(false);
                                        onEdit?.(event);
                                    }}
                                />
                                <Button
                                    size="nano"
                                    variant="tertiary"
                                    color="brand"
                                    icon={<Icon type={IconType.OUTLINED} name="delete" aria-hidden="true" />}
                                    aria-label={t("Delete")}
                                    title={t("Delete")}
                                    onClick={() => {
                                        setShowActions(false);
                                        handleDelete();
                                    }}
                                />
                            </>
                        )}
                    </div>
                </div>
            </div>
        );
    }

    // Assignment events: single discrete line, no icon, no background.
    // Aligned with the Front/Linear convention of keeping system metadata out
    // of the way of actual conversation.
    if (isAssignmentEvent(event)) {
        return <SystemEventLine event={event} currentUserId={user?.id} />;
    }

    // Fallback for other event types
    return (
        <div className="thread-event thread-event--generic">
            <div className="thread-event__badge">{event.type}</div>
            <div className="thread-event__body">
                <div className="thread-event__header">
                    <span className="thread-event__time">
                        {t('{{date}} at {{time}}', {
                            date: DateHelper.formatDate(event.created_at, i18n.resolvedLanguage, false),
                            time: new Date(event.created_at).toLocaleString(i18n.resolvedLanguage, {
                                minute: '2-digit',
                                hour: '2-digit',
                            }),
                        })}
                        {isEdited && ` · ${t('Modified')}`}
                    </span>
                </div>
                {(event?.data as { content?: string })?.content && (
            <div className="thread-event__content">{(event?.data as { content?: string })?.content}</div>
                )}
            </div>
        </div>
    );
};

type SystemEventLineProps = {
    event: TypedThreadEvent<'assign' | 'unassign'>;
    currentUserId: string | undefined;
};

/**
 * Renders an ASSIGN/UNASSIGN event as a single discrete text line.
 *
 * Phrasing is delegated to ``buildAssignmentMessage`` which picks the right
 * i18n key for each viewer-relative case (self assigning self, someone
 * assigning the viewer, …) so the sentence stays grammatically correct.
 * The timestamp always includes the time, prefixed with day/date outside of
 * today — users shouldn’t have to hover to know when something happened.
 */
const SystemEventLine = ({ event, currentUserId }: SystemEventLineProps) => {
    const { t, i18n } = useTranslation();
    const message = buildAssignmentMessage(event, currentUserId, t);
    const timeLabel = DateHelper.formatEventTimestamp(event.created_at, i18n.resolvedLanguage);

    return (
        <div
            className="thread-event thread-event--system-line"
            data-event-id={event.id}
        >
            <span className="thread-event__system-text">{message}</span>
            <span className="thread-event__system-time">· {timeLabel}</span>
        </div>
    );
};

type CollapsedEventsGroupProps = {
    events: TypedThreadEvent<'assign' | 'unassign'>[];
};

/**
 * Renders a run of 3+ consecutive assignment events behind a disclosure toggle.
 * Collapsed by default to keep assignment churn out of the reading flow;
 * expands to a chronological list of the same inline lines used outside of
 * groups.
 */
export const CollapsedEventsGroup = ({ events }: CollapsedEventsGroupProps) => {
    const { t } = useTranslation();
    const { user } = useAuth();
    const [expanded, setExpanded] = useState(false);

    const bucket = useMemo(() => {
        const latest = events[events.length - 1];
        return DateHelper.bucketDate(latest.created_at);
    }, [events]);

    const bucketLabel =
        bucket === 'today' ? t('Today')
        : bucket === 'this_week' ? t('This week')
        : t('Older');

    const summary = t('{{count}} assignment changes', {
        count: events.length,
        defaultValue_one: '{{count}} assignment change',
        defaultValue_other: '{{count}} assignment changes',
    });

    return (
        <div className="thread-event thread-event--collapsed">
            <button
                type="button"
                className="thread-event__collapsed-toggle"
                aria-expanded={expanded}
                onClick={() => setExpanded((v) => !v)}
            >
                <Icon
                    type={IconType.OUTLINED}
                    size={IconSize.X_SMALL}
                    name={expanded ? 'expand_more' : 'chevron_right'}
                    aria-hidden="true"
                />
                <span className="thread-event__collapsed-summary">
                    {summary}
                </span>
                <span className="thread-event__collapsed-bucket">· {bucketLabel}</span>
            </button>
            {expanded && (
                <div className="thread-event__collapsed-list">
                    {events.map((evt) => (
                        <SystemEventLine
                            key={evt.id}
                            event={evt}
                            currentUserId={user?.id}
                        />
                    ))}
                </div>
            )}
        </div>
    );
};
