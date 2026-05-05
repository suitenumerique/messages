import { Icon, IconSize, IconType, Spinner, UserAvatar } from "@gouvfr-lasuite/ui-kit";
import {
    Autocomplete,
    Dialog,
    Input,
    Menu,
    MenuItem,
    Popover,
    SearchField,
    Selection,
    useFilter,
} from "react-aria-components";
import { RefObject, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
    ThreadAccessRoleChoices,
    UserWithoutAbilities,
    useThreadsAccessesList,
} from "@/features/api/gen";
import { useAuth } from "@/features/auth";
import { useThreadAssignment } from "@/features/message/use-thread-assignment";
import { StringHelper } from "@/features/utils/string-helper";

type QuickAssignPopoverProps = {
    isOpen: boolean;
    triggerRef: RefObject<HTMLElement | null>;
    onOpenChange: (open: boolean) => void;
    threadId: string;
};

/**
 * Compact popover that lets the current user toggle thread assignment for
 * any teammate without opening the full share modal.
 *
 * A11y:
 *   - `Dialog` from react-aria-components provides role=dialog, focus trap,
 *     ESC-to-close and focus restoration on the trigger.
 *   - `Autocomplete` wires the search field to the menu so arrow keys move
 *     virtual focus across items while the user keeps typing.
 *   - `Menu` with `selectionMode="multiple"` exposes each row as
 *     role=menuitemcheckbox with proper aria-checked semantics.
 *   - `disabledKeys` blocks every user with an in-flight mutation; the
 *     others stay navigable, so keyboard users never lose their place
 *     and concurrent toggles can settle independently.
 *   - The check icon and the avatar are decorative — meaning is conveyed
 *     by aria-checked and the visible name.
 *
 * Filtering: only users coming from editor mailboxes are listed. Viewer
 * mailboxes are excluded because assigning them would require a
 * privilege escalation we don't surface here (the share modal handles it).
 */
export const QuickAssignPopover = ({
    isOpen,
    triggerRef,
    onOpenChange,
    threadId,
}: QuickAssignPopoverProps) => {
    const { t } = useTranslation();
    const { user: currentUser } = useAuth();
    const [announcement, setAnnouncement] = useState("");
    const {
        assignedUserIds,
        mutatingUserIds,
        assignUser,
        unassignUser,
    } = useThreadAssignment();

    const accessesQuery = useThreadsAccessesList(
        threadId,
        undefined,
        { query: { enabled: isOpen && !!threadId } },
    );

    // Distinct users across editor mailboxes only. Sorted with the current
    // user first (one-click self-assign), then alphabetically by display
    // name with email as a fallback for users whose `full_name` is null.
    const users = useMemo(() => {
        const editorAccesses = (accessesQuery.data?.data ?? []).filter(
            (a) => a.role === ThreadAccessRoleChoices.editor,
        );
        const seen = new Set<string>();
        const list: UserWithoutAbilities[] = [];
        for (const access of editorAccesses) {
            for (const user of access.users) {
                if (seen.has(user.id)) continue;
                seen.add(user.id);
                list.push(user);
            }
        }
        list.sort((a, b) => {
            if (currentUser && a.id === currentUser.id) return -1;
            if (currentUser && b.id === currentUser.id) return 1;
            const labelA = a.full_name ?? a.email ?? "";
            const labelB = b.full_name ?? b.email ?? "";
            return labelA.localeCompare(labelB);
        });
        return list;
    }, [accessesQuery.data?.data, currentUser]);

    // react-aria-components ships its own `useFilter` hook backed by the
    // user's locale (handles diacritics correctly via Intl.Collator). We
    // wrap it in an extra normalization pass for ligatures (œ → oe) which
    // Intl.Collator's "base" sensitivity does not always cover.
    const { contains } = useFilter({ sensitivity: "base" });
    const matchUser = (textValue: string, inputValue: string) => {
        if (!inputValue) return true;
        const haystack = StringHelper.normalizeForSearch(textValue);
        const needle = StringHelper.normalizeForSearch(inputValue);
        return contains(haystack, needle);
    };

    const handleSelectionChange = (newKeys: Selection) => {
        if (newKeys === "all") return;
        // Diff the new selection against the current server-known state.
        // Exactly one delta is expected per user click (Menu fires once
        // per toggle), but we walk both sides to be safe. The live-region
        // announcement is not fired here: it is derived from the
        // server-confirmed `assignedUserIds` change in the effect below,
        // so a failed mutation never produces a misleading success message.
        for (const key of newKeys) {
            const id = String(key);
            if (!assignedUserIds.has(id)) {
                const user = users.find((u) => u.id === id);
                if (user) {
                    assignUser(user);
                }
                return;
            }
        }
        for (const id of assignedUserIds) {
            if (!newKeys.has(id)) {
                unassignUser(id);
                return;
            }
        }
    };

    // Announce assignment changes only after the server confirms them.
    // We diff the previous `assignedUserIds` Set against the current one
    // (stabilized via useMemo in `useThreadAssignment`) and announce the
    // arriving or leaving user. The first render is skipped so existing
    // assignees aren't read out when the popover mounts.
    const previousAssignedRef = useRef<ReadonlySet<string> | null>(null);
    useEffect(() => {
        const previous = previousAssignedRef.current;
        previousAssignedRef.current = assignedUserIds;
        if (previous === null) return;

        for (const id of assignedUserIds) {
            if (!previous.has(id)) {
                const user = users.find((u) => u.id === id);
                setAnnouncement(
                    t('{{name}} assigned to this thread', {
                        name: user?.full_name || user?.email || "",
                    }),
                );
                return;
            }
        }
        for (const id of previous) {
            if (!assignedUserIds.has(id)) {
                const user = users.find((u) => u.id === id);
                setAnnouncement(
                    t('{{name}} unassigned from this thread', {
                        name: user?.full_name || user?.email || "",
                    }),
                );
                return;
            }
        }
    }, [assignedUserIds, users, t]);

    const disabledKeys = mutatingUserIds.size > 0 ? mutatingUserIds : undefined;

    return (
        <Popover
            isOpen={isOpen}
            triggerRef={triggerRef}
            onOpenChange={onOpenChange}
            placement="bottom start"
        >
            <Dialog
                aria-label={t('Assign users to this thread')}
                className="quick-assign-popover"
            >
                <div
                    className="quick-assign-popover__body"
                    onKeyDownCapture={(e: React.KeyboardEvent) => {
                        // Close on Esc no matter what currently has focus.
                        // Without this, react-aria's SearchField/Autocomplete/Menu
                        // stack can interpret Esc as a deselect on the virtually
                        // focused row, which fires onSelectionChange and unassigns
                        // a user instead of dismissing the popover.
                        if (e.key === "Escape") {
                            e.preventDefault();
                            e.stopPropagation();
                            onOpenChange(false);
                        }
                    }}
                >
                <Autocomplete filter={matchUser}>
                    <SearchField
                        aria-label={t('Search users')}
                        className="quick-assign-popover__search"
                    >
                        <Icon
                            name="search"
                            type={IconType.OUTLINED}
                            size={IconSize.SMALL}
                            className="quick-assign-popover__search-icon"
                            aria-hidden="true"
                        />
                        {/* autoFocus is essential: Dialog's FocusScope can
                        land on the wrapper instead of the Input depending on
                        mount order, which leaves Autocomplete's virtual-focus
                        pipeline disconnected (no typing, no arrow nav). */}
                        <Input
                            autoFocus
                            placeholder={t('Assign to...')}
                            className="quick-assign-popover__search-input"
                        />
                    </SearchField>
                    <Menu
                        items={accessesQuery.isLoading ? [] : users}
                        selectionMode="multiple"
                        selectedKeys={assignedUserIds as Set<string>}
                        disabledKeys={disabledKeys}
                        onSelectionChange={handleSelectionChange}
                        renderEmptyState={() => (
                            accessesQuery.isLoading ? (
                                <div
                                    role="status"
                                    aria-live="polite"
                                    className="quick-assign-popover__status"
                                >
                                    <Spinner size="sm" />
                                    <span className="c__offscreen">
                                        {t('Loading users')}
                                    </span>
                                </div>
                            ) : (
                                <div className="quick-assign-popover__status">
                                    {t('No matching users')}
                                </div>
                            )
                        )}
                        className="quick-assign-popover__list"
                    >
                            {(user) => {
                                const isMe = currentUser?.id === user.id;
                                const displayName = isMe
                                    ? t('You')
                                    : user.full_name || user.email || "";
                                // textValue powers Autocomplete filtering and
                                // the screen-reader label for the row. Joining
                                // name + email lets users find each other by
                                // either; "Me" is dropped on purpose so users
                                // type their actual name to find themselves.
                                const textValue = [user.full_name, user.email]
                                    .filter(Boolean)
                                    .join(" ");
                                const isMutating = mutatingUserIds.has(user.id);
                                return (
                                    <MenuItem
                                        id={user.id}
                                        textValue={textValue}
                                        className="quick-assign-popover__row"
                                    >
                                        <span aria-hidden="true">
                                            <UserAvatar
                                                fullName={user.full_name || user.email || ""}
                                                size="xsmall"
                                            />
                                        </span>
                                        <span className="quick-assign-popover__row-name">
                                            {displayName}
                                        </span>
                                        {isMutating ? (
                                            <Spinner size="sm" />
                                        ) : (
                                            <Icon
                                                name="check"
                                                type={IconType.OUTLINED}
                                                size={IconSize.SMALL}
                                                className="quick-assign-popover__row-check"
                                                aria-hidden="true"
                                            />
                                        )}
                                    </MenuItem>
                                );
                            }}
                        </Menu>
                </Autocomplete>
                <div
                    role="status"
                    aria-live="polite"
                    className="c__offscreen"
                >
                    {announcement}
                </div>
                </div>
            </Dialog>
        </Popover>
    );
};
