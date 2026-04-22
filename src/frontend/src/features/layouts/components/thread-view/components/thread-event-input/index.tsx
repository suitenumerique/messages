import { RefObject, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon, IconSize, IconType, UserRow } from "@gouvfr-lasuite/ui-kit";
import { useThreadsEventsCreate, useThreadsEventsPartialUpdate, useThreadsUsersList, ThreadMentionableUser, ThreadEventTypeEnum, ThreadEvent, ThreadEventIMData } from "@/features/api/gen";
import { StringHelper } from "@/features/utils/string-helper";
import { TextHelper } from "@/features/utils/text-helper";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Badge } from "@/features/ui/components/badge";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useAuth } from "@/features/auth";
import { SuggestionInput } from "@/features/ui/components/suggestion-input";
import { useThreadViewContext } from "../../provider";
import { useIMInputVisibility } from "./use-im-input-visibility";
import clsx from "clsx";

type ThreadEventInputProps = {
    threadId: string;
    editingEvent?: ThreadEvent | null;
    onCancelEdit?: () => void;
    onEventCreated?: () => void;
    containerRef: RefObject<HTMLDivElement | null>;
};

/**
 * Small fixed input bar at the bottom of the thread view for adding internal comments.
 * Also handles editing existing events when `editingEvent` is provided.
 */
export const ThreadEventInput = ({ threadId, editingEvent, onCancelEdit, onEventCreated, containerRef }: ThreadEventInputProps) => {
    const { t } = useTranslation();
    const { invalidateThreadEvents } = useMailboxContext();
    const { user: currentUser } = useAuth();
    const { isMessageFormFocused } = useThreadViewContext();

    const isEditing = !!editingEvent;

    const { isVisible, onFocusChange } = useIMInputVisibility({
        containerRef,
        threadId,
        isEditing,
        isMessageFormFocused,
    });
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const [content, setContent] = useState("");
    const [mentions, setMentions] = useState<Array<{ id: string; name: string }>>([]);
    const [showMentionPopover, setShowMentionPopover] = useState(false);
    const [mentionFilter, setMentionFilter] = useState("");

    const createEvent = useThreadsEventsCreate();
    const updateEvent = useThreadsEventsPartialUpdate();
    const isPending = isEditing ? updateEvent.isPending : createEvent.isPending;

    const { data: usersData } = useThreadsUsersList(threadId, {
        query: {
            enabled: showMentionPopover,
            staleTime: 5 * 60 * 1000, // Keep data for 5 minutes
        },
    });

    const users = usersData?.data ?? [];
    const mentionedIds = new Set(mentions.map((m) => m.id));
    const filteredUsers = users.filter((user: ThreadMentionableUser) => {
        if (user.id === currentUser?.id) return false;
        if (mentionedIds.has(user.id)) return false;
        if (!mentionFilter) return true;
        const filter = StringHelper.normalizeForSearch(mentionFilter);
        const name = StringHelper.normalizeForSearch(user.full_name ?? "");
        const email = user.email?.toLowerCase() ?? "";
        return name.includes(filter) || email.includes(filter);
    });

    const isPopoverOpen = showMentionPopover && filteredUsers.length > 0;

    const resetInput = useCallback(() => {
        setContent("");
        setMentions([]);
        setShowMentionPopover(false);
        setMentionFilter("");
    }, []);

    const processContent = useCallback((rawContent: string) => {
        // Convert @Name back to @[Name] for storage/rendering
        // Sort by name length descending so longer names are replaced first
        let processedContent = rawContent;
        const sortedMentions = [...mentions].sort((a, b) => b.name.length - a.name.length);
        for (const m of sortedMentions) {
            processedContent = processedContent.replace(TextHelper.buildMentionPattern(m.name, "gu"), `@[${m.name}]`);
        }
        return processedContent;
    }, [mentions]);

    const buildEventData = useCallback((processedContent: string) => {
        // Only include mentions that are still present in the content
        const activeMentions = mentions.filter((m) => processedContent.includes(`@[${m.name}]`));
        return {
            content: processedContent,
            ...(activeMentions.length > 0 && { mentions: activeMentions }),
        };
    }, [mentions]);

    const handleSubmit = useCallback(() => {
        const trimmed = content.trim();
        if (!trimmed) return;

        const processedContent = processContent(trimmed);
        const eventData = buildEventData(processedContent);

        if (isEditing) {
            if (updateEvent.isPending) return;
            updateEvent.mutate(
                {
                    threadId,
                    id: editingEvent.id,
                    data: { data: eventData },
                },
                {
                    onSuccess: async () => {
                        resetInput();
                        onCancelEdit?.();
                        await invalidateThreadEvents();
                    },
                },
            );
        } else {
            if (createEvent.isPending) return;
            createEvent.mutate(
                {
                    threadId,
                    data: {
                        type: ThreadEventTypeEnum.im,
                        data: eventData,
                    },
                },
                {
                    onSuccess: async () => {
                        resetInput();
                        await invalidateThreadEvents();
                        onEventCreated?.();
                    },
                },
            );
        }
    }, [content, mentions, threadId, editingEvent, isEditing, createEvent, updateEvent, invalidateThreadEvents, onEventCreated, onCancelEdit, processContent, buildEventData, resetInput]);

    const handleCancelEdit = useCallback(() => {
        resetInput();
        onCancelEdit?.();
    }, [resetInput, onCancelEdit]);

    const insertMention = (user: ThreadMentionableUser) => {
        const name = user.full_name || user.email || "";

        if (!mentions.some((m) => m.id === user.id)) {
            setMentions((prev) => [...prev, { id: user.id, name }]);
        }

        // Replace the @partial text with the full mention (displayed without brackets)
        const textarea = textareaRef.current;
        if (textarea) {
            const cursorPos = textarea.selectionStart ?? 0;
            const textBeforeCursor = content.slice(0, cursorPos);
            const textAfterCursor = content.slice(cursorPos);
            const mentionStart = textBeforeCursor.lastIndexOf("@");
            const newText = `${textBeforeCursor.slice(0, mentionStart)}@${name} ${textAfterCursor}`;
            setContent(newText);

            // Move cursor after the mention
            const newCursorPos = mentionStart + name.length + 2; // +2 for @ and space
            requestAnimationFrame(() => {
                textarea.setSelectionRange(newCursorPos, newCursorPos);
                textarea.focus();
            });
        }

        setShowMentionPopover(false);
        setMentionFilter("");
    };

    const handleInput = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        const value = e.target.value;
        setContent(value);

        // Remove mentions whose @Name pattern was deleted from the content
        // Use word-boundary check to avoid partial matches (e.g. @John inside @Johnny)
        setMentions((prev) => prev.filter((m) => TextHelper.buildMentionPattern(m.name).test(value)));

        // Detect @mention trigger — uses Unicode property escapes (\p{L}, \p{N})
        // to support accented and non-Latin characters (e.g. @René, @Étienne)
        const cursorPos = e.target.selectionStart ?? 0;
        const textBeforeCursor = value.slice(0, cursorPos);
        const mentionMatch = textBeforeCursor.match(/(?:^|\s)@([\p{L}\p{N}_]*)$/u);

        if (mentionMatch) {
            setShowMentionPopover(true);
            setMentionFilter(mentionMatch[1]);
        } else {
            setShowMentionPopover(false);
            setMentionFilter("");
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit();
        }
        if (e.key === "Escape" && isEditing) {
            handleCancelEdit();
        }
    };

    // Reset on thread change
    useEffect(() => {
        resetInput();
        onCancelEdit?.();
    }, [threadId]);

    // Populate input when entering edit mode, reset when leaving
    useEffect(() => {
        if (editingEvent) {
            const eventData = editingEvent?.data as ThreadEventIMData | null;
            const eventContent = eventData?.content ?? "";
            // Restore mentions from persisted data.mentions (contains real user IDs)
            const persistedMentions = eventData?.mentions ?? [];
            // Build a lookup to map name → id from persisted mentions
            const mentionsByName = new Map(persistedMentions.map((m) => [m.name, m.id]));
            // Convert @[Name] to @Name for display, rebuild mentions list with real IDs
            const extractedMentions: Array<{ id: string; name: string }> = [];
            const displayContent = eventContent.replace(/@\[([^\]]+)\]/g, (_, name: string) => {
                const id = mentionsByName.get(name) ?? name;
                if (!extractedMentions.some((m) => m.id === id)) {
                    extractedMentions.push({ id, name });
                }
                return `@${name}`;
            });
            setContent(displayContent);
            setMentions(extractedMentions);
            requestAnimationFrame(() => textareaRef.current?.focus());
        } else {
            resetInput();
        }
    }, [editingEvent, resetInput]);

    const handleFocus = useCallback(() => {
        onFocusChange(true);
    }, [onFocusChange]);

    const handleBlur = useCallback((e: React.FocusEvent<HTMLDivElement>) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) {
            onFocusChange(false);
        }
    }, [onFocusChange]);

    return (
        <div
            className={clsx("thread-event-input", { "thread-event-input--hidden": !isVisible })}
            onFocus={handleFocus}
            onBlur={handleBlur}
            inert={!isVisible}
        >
            {isEditing && (
                <div className="thread-event-input__edit-banner">
                    <Icon name="edit" type={IconType.OUTLINED} size={IconSize.SMALL} aria-hidden="true" />
                    <span className="thread-event-input__edit-banner__label">{t("Editing message")}</span>
                    <Button
                        size="nano"
                        color="warning"
                        variant="tertiary"
                        icon={<Icon name="close" type={IconType.OUTLINED} size={IconSize.SMALL} />}
                        onClick={handleCancelEdit}
                        aria-label={t("Cancel")}
                    />
                </div>
            )}
            <div className="thread-event-input__container">
                <SuggestionInput
                    className="thread-event-input__field"
                    inputClassName="thread-event-input__textarea"
                    inputRef={textareaRef}
                    value={content}
                    onChange={handleInput}
                    onKeyDown={handleKeyDown}
                    placeholder={t("Add internal comment...")}
                    rows={1}
                    items={filteredUsers}
                    isOpen={isPopoverOpen}
                    onOpenChange={(open) => {
                        if (!open) {
                            setShowMentionPopover(false);
                            setMentionFilter("");
                        }
                    }}
                    inputValue={mentionFilter}
                    onSelect={insertMention}
                    itemToString={(item) => item?.full_name || item?.email || ""}
                    keyExtractor={(user) => user.id}
                    renderItem={(user) => (
                        <div className="thread-event-input__suggestion">
                            <UserRow
                                fullName={user.full_name || undefined}
                                email={user.email || undefined}
                            />
                            {!user.can_post_comments && (
                                <Badge color="warning" variant="secondary">
                                    {t("Read-only")}
                                </Badge>
                            )}
                        </div>
                    )}
                />
                <Button
                    className="thread-event-input__submit-button"
                    size="small"
                    variant="tertiary"
                    icon={<Icon name={isEditing ? "check" : "arrow_upward"} type={IconType.OUTLINED} size={IconSize.MEDIUM} />}
                    onClick={handleSubmit}
                    disabled={!content.trim() || isPending}
                    title={isEditing ? t("Save") : t("Send")}
                    aria-label={isEditing ? t("Save") : t("Send")}
                />
            </div>
        </div>
    );
};
