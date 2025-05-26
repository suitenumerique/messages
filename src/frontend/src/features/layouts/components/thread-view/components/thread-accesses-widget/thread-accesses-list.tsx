import { ThreadAccessDetail } from "@/features/api/gen/models"
import { getUserColor, getUserInitials } from "@gouvfr-lasuite/ui-kit";

const MAX_ACCESS_AVATARS_SHOWN = 4;

type ThreadAccessesListProps = {
    accesses: readonly ThreadAccessDetail[];
}
/**
 * ThreadAccessesList renders all thread accesses as a list of avatars.
 */
export const ThreadAccessesList = ({ accesses }: ThreadAccessesListProps) => {
    return (
        <>
            {accesses.slice(0, MAX_ACCESS_AVATARS_SHOWN).map((threadAccess, index) => {
                return (
                    <div
                        key={threadAccess.id}
                        className="thread-accesses-widget__item">
                            <AccessAvatar
                                name={threadAccess.mailbox.name ?? threadAccess.mailbox.email}
                                index={accesses.length - index}
                            />
                    </div>
                )
            })}
        </>
    )
};

type AccessAvatarProps = {
    name: string;
    index: number;
}
/**
 * AccessAvatar renders an avatar through user initials with a background color
 * processed according to the user name.
 */
const AccessAvatar = ({ name, index }: AccessAvatarProps) => {
    const abbr = getUserInitials(name);
    const color = getUserColor(name);

    return (
        <div
            className={`widget-access-tile c__avatar ${color}`}
            style={{ "zIndex": index }}
        >
            {abbr}
        </div>
    )
}
