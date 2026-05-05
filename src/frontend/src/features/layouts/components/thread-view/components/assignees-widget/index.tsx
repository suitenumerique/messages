import { Button, Tooltip } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAssignedUsers } from "@/features/message/use-assigned-users";
import { useMailboxContext } from "@/features/providers/mailbox";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useIsSharedContext } from "@/hooks/use-is-shared-context";
import { AssigneesAvatarGroup } from "@/features/ui/components/assignees-avatar-group";
import { QuickAssignPopover } from "./quick-assign-popover";

type AssigneesWidgetProps = {
    /**
     * Click handler used only on the read-only path (no manage rights).
     * When the user can manage thread access, the widget owns its own
     * popover and ignores this callback.
     */
    onClick?: () => void;
};

/**
 * Surfaces the current assignees on the thread and exposes the quick
 * assignment popover for users with manage rights. The trigger is shown:
 *   - never when the thread is not in a shared context (no other mailboxes
 *     to assign to);
 *   - on the read-only path, only when at least one user is assigned (just
 *     the avatars + tooltip — clicking falls back to `onClick`);
 *   - on the manage path, always (avatars when any, "person_add" icon
 *     otherwise) — click opens the QuickAssignPopover.
 */
export const AssigneesWidget = ({ onClick }: AssigneesWidgetProps) => {
    const { t } = useTranslation();
    const { selectedMailbox, selectedThread } = useMailboxContext();
    const isSharedContext = useIsSharedContext();
    const assignedUsers = useAssignedUsers();
    const canManageThreadAccess = useAbility(
        Abilities.CAN_MANAGE_THREAD_ACCESS,
        [selectedMailbox!, selectedThread!],
    );
    const triggerRef = useRef<HTMLSpanElement>(null);
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);

    if (!isSharedContext) return null;

    const assignedTooltip = t('Assigned to {{names}}', {
        names: assignedUsers.map((u) => u.name).join(', '),
    });

    if (!canManageThreadAccess) {
        if (assignedUsers.length === 0) return null;
        return (
            <Tooltip content={assignedTooltip}>
                <Button
                    type="button"
                    variant="tertiary"
                    className="assignees-widget"
                    onClick={onClick}
                    aria-label={assignedTooltip}
                    size="nano"
                    disabled
                >
                    <AssigneesAvatarGroup users={assignedUsers} maxAvatars={3} />
                </Button>
            </Tooltip>
        );
    }

    const tooltipContent = assignedUsers.length === 0
        ? t('Assign users to this thread')
        : assignedTooltip;

    return (
        <>
            <span ref={triggerRef} className="assignees-widget__trigger-wrapper">
                <Tooltip content={tooltipContent}>
                    <Button
                        type="button"
                        variant="tertiary"
                        className="assignees-widget"
                        onClick={() => setIsPopoverOpen((open) => !open)}
                        aria-label={tooltipContent}
                        aria-haspopup="dialog"
                        aria-expanded={isPopoverOpen}
                        size="nano"
                        icon={
                            assignedUsers.length === 0
                                ? <Icon name="person_add" type={IconType.OUTLINED} />
                                : undefined
                        }
                    >
                        {assignedUsers.length > 0 && (
                            <AssigneesAvatarGroup users={assignedUsers} maxAvatars={3} />
                        )}
                    </Button>
                </Tooltip>
            </span>
            {selectedThread?.id && (
                <QuickAssignPopover
                    isOpen={isPopoverOpen}
                    triggerRef={triggerRef}
                    onOpenChange={setIsPopoverOpen}
                    threadId={selectedThread.id}
                />
            )}
        </>
    );
};
