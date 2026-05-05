import { UserAvatar } from "@gouvfr-lasuite/ui-kit";

export type AssigneesAvatarGroupUser = {
    id: string;
    name: string;
};

export type AssigneesAvatarGroupOverflowMode = "extra" | "replace-last";

export type AssigneesAvatarGroupSize = "xsmall" | "small" | "medium" | "large";

type AssigneesAvatarGroupProps = {
    users: ReadonlyArray<AssigneesAvatarGroupUser>;
    maxAvatars: number;
    overflowMode?: AssigneesAvatarGroupOverflowMode;
    size?: AssigneesAvatarGroupSize;
};

/**
 * Stack of overlapping avatars for a list of assignees, with an overflow
 * counter when the list exceeds ``maxAvatars``.
 *
 * Overflow modes:
 * - ``"extra"`` (default): counter appears *after* ``maxAvatars`` avatars
 *   — used where space is generous (e.g. thread header widget).
 * - ``"replace-last"``: counter replaces the last avatar slot so the total
 *   visible slot count never exceeds ``maxAvatars`` — used in compact
 *   contexts like the thread list row.
 *
 * The parent owns any surrounding interactive wrapper (tooltip, button...).
 */
export const AssigneesAvatarGroup = ({
    users,
    maxAvatars,
    overflowMode = "extra",
    size = "xsmall",
}: AssigneesAvatarGroupProps) => {
    if (users.length === 0) return null;

    const hasOverflow = users.length > maxAvatars;
    const avatarCount = hasOverflow && overflowMode === "replace-last"
        ? Math.max(maxAvatars - 1, 0)
        : Math.min(users.length, maxAvatars);
    const visible = users.slice(0, avatarCount);
    const overflow = users.length - avatarCount;

    return (
        <span className="assignees-avatar-group" data-size={size}>
            {visible.map((user) => (
                <UserAvatar key={user.id} fullName={user.name} size={size} />
            ))}
            {overflow > 0 && (
                <span className="assignees-avatar-group__overflow" aria-hidden="true">
                    +{overflow}
                </span>
            )}
        </span>
    );
};
