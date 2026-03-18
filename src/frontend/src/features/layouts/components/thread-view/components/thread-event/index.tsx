import React, { useCallback, useEffect, useRef, useState } from "react";
import { TextHelper } from "@/features/utils/text-helper";
import { ThreadEvent as ThreadEventType, ThreadEventTypeEnum } from "@/features/api/gen/models";
import { useThreadsEventsDestroy } from "@/features/api/gen/thread-events/thread-events";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/features/auth";
import { useMailboxContext } from "@/features/providers/mailbox";
import { AVATAR_COLORS, Icon, IconType, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import { Button, useModals } from "@gouvfr-lasuite/cunningham-react";

const TWO_MINUTES_MS = 2 * 60 * 1000;

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
    previousEvent?: ThreadEventType | null;
    onEdit?: (event: ThreadEventType) => void;
    onDelete?: (eventId: string) => void;
};

const formatTime = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
};

/**
 * Returns true if this IM event should show a condensed view (no header),
 * because the previous event is also an IM from the same author within 2 minutes.
 */
const isCondensed = (event: ThreadEventType, previousEvent?: ThreadEventType | null): boolean => {
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
export const ThreadEvent = ({ event, previousEvent, onEdit, onDelete }: ThreadEventProps) => {
    const { t } = useTranslation();
    const { user } = useAuth();
    const modals = useModals();
    const { invalidateThreadEvents } = useMailboxContext();
    const content = event.data?.content ?? "";
    const condensed = isCondensed(event, previousEvent);
    const isSender = event.author?.id === user?.id;
    const isEdited = Math.abs(new Date(event.updated_at).getTime() - new Date(event.created_at).getTime()) > 1000;

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

    const handleDelete = async () => {
        const decision = await modals.deleteConfirmationModal({
            title: <span className="c__modal__text--centered">{t('Delete message')}</span>,
            children: t('Are you sure you want to delete this message? It will be deleted for all users. This action cannot be undone.'),
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

    if (event.type === ThreadEventTypeEnum.im) {
        const authorName = event.author?.full_name || event.author?.email || "";
        const avatarColor = getAvatarColor(authorName);
        const isMentioned = user
            ? event.data?.mentions?.map((m) => m.id)?.includes(user.id)
            : false;

        const imClasses = [
            "thread-event",
            "thread-event--im",
            condensed && "thread-event--condensed",
            isSender && "thread-event--sender",
        ].filter(Boolean).join(" ");

        const bubbleStyle = {
            "--thread-event-color": `var(--c--contextuals--background--palette--${avatarColor}--primary)`,
        } as React.CSSProperties;

        return (
            <div className={imClasses}>
                <div
                    ref={bubbleRef}
                    className={`thread-event__bubble${showActions ? " thread-event__bubble--actions-visible" : ""}`}
                    style={bubbleStyle}
                >
                    {!condensed && (
                        <div className="thread-event__header">
                            <span className="thread-event__author">
                                <UserAvatar fullName={event.author?.full_name || event.author?.email || t("Unknown")} size="xsmall" />
                                {event.author?.full_name || event.author?.email || t("Unknown")}
                            </span>
                            <span className="thread-event__time">
                                {formatTime(event.created_at)}
                            </span>
                        </div>
                    )}
                    <div
                        className={`thread-event__content${pressing ? " thread-event__content--pressing" : ""}`}
                        onTouchStart={isSender ? handleTouchStart : undefined}
                        onTouchEnd={isSender ? cancelLongPress : undefined}
                        onTouchMove={isSender ? cancelLongPress : undefined}
                        onTouchCancel={isSender ? cancelLongPress : undefined}
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
                    {isSender && (
                        <div className="thread-event__actions">
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
                        </div>
                    )}
                </div>
            </div>
        );
    }

    // Fallback for other event types
    return (
        <div className="thread-event thread-event--generic">
            <div className="thread-event__badge">{event.type}</div>
            <div className="thread-event__body">
                <div className="thread-event__header">
                    <span className="thread-event__time">
                        {formatTime(event.created_at)}
                        {isEdited && ` · ${t('Modified')}`}
                    </span>
                </div>
                {content && (
                    <div className="thread-event__content">{content}</div>
                )}
            </div>
        </div>
    );
};
