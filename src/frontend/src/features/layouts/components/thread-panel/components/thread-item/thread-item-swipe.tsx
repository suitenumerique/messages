import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "@tanstack/react-router";
import clsx from "clsx";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";

import { Thread } from "@/features/api/gen/models";
import { ThreadRowActions } from "@/features/message/use-thread-row-actions";
import useThreadUnread from "@/features/message/use-thread-unread";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useSwipeActions } from "@/hooks/use-swipe-actions";
import ViewHelper from "@/features/utils/view-helper";

/** Width (px) of a single revealed action button. Mirrors the SCSS. */
const ACTION_WIDTH = 76;

type ThreadItemSwipeProps = {
    thread: Thread;
    enabled: boolean;
    /** Owned by the list, not the row — see `useThreadRowActions`. */
    actions: ThreadRowActions;
    children: React.ReactNode;
};

/**
 * Touch swipe affordances around a thread row.
 *
 * Swiping right reveals the read/unread toggle, which also fires on its own
 * once the row is pulled far enough — a single leading action makes that
 * shortcut unambiguous. Swiping left reveals archive and trash as buttons to
 * tap: pulling cannot pick between two actions.
 *
 * The panels only exist while the row is being swiped or held open. At rest a
 * list of fifty rows would otherwise carry several hundred nodes it never
 * shows, which costs layout and memory on exactly the devices where the
 * gesture needs to stay smooth.
 *
 * The revealed controls are `aria-hidden` and out of the tab order on purpose.
 * The gesture is touch-only, and every action it exposes is already reachable
 * from the selection toolbar and the thread action bar — exposing them twice
 * would only add tab stops to every row of the list.
 */
export const ThreadItemSwipe = ({
    thread,
    enabled,
    actions,
    children,
}: ThreadItemSwipeProps) => {
    const { t } = useTranslation();
    const canEditThread = useAbility(Abilities.CAN_EDIT_THREAD, thread);
    const hasUnread = useThreadUnread(thread);

    // Each of these rebuilds the whole folder tree (translated labels
    // included) and reparses the query string, so left uncached they ran a few
    // hundred times per render of the list. They only depend on the URL.
    const searchStr = useLocation({ select: (location) => location.searchStr });
    const view = useMemo(
        () => ({
            isTrashed: ViewHelper.isTrashedView(),
            isArchived: ViewHelper.isArchivedView(),
            isDrafts: ViewHelper.isDraftsView(),
        }),
        [searchStr]
    );

    // Same rules as the thread action bar: from the trash the only meaningful
    // transition is restoring, and archiving a spam or a draft thread means
    // nothing.
    const isTrashContext = view.isTrashed || thread.is_trashed;
    const isArchivedView = view.isArchived;
    const canArchive =
        canEditThread && !thread.is_spam && !isTrashContext && !view.isDrafts;
    const canTrash = canEditThread;
    const endWidth = (canArchive ? ACTION_WIDTH : 0) + (canTrash ? ACTION_WIDTH : 0);

    const toggleRead = () => actions.toggleRead(thread, hasUnread);

    const { containerRef, contentRef, openSide, isSwiping, close } = useSwipeActions({
        startWidth: ACTION_WIDTH,
        endWidth,
        onCommitStart: toggleRead,
        enabled,
    });

    // Closing first keeps the row from sitting open behind a list that the
    // mutation is about to reshuffle.
    const runAction = (action: () => void) => {
        close();
        action();
    };

    // While a panel is open the row is a dismiss surface: the tap that closes
    // it must not also open the thread. Swallowing the click in the capture
    // phase is what keeps it from reaching the stretched link below.
    const handleClickCapture = (event: React.MouseEvent) => {
        if (!openSide) return;
        if (event.target instanceof Element && event.target.closest(".thread-item-swipe__action")) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        close();
    };

    // Drafts have no trash stage: in the drafts view the button hard-deletes
    // the thread's drafts, mirroring the thread action bar and the selection
    // toolbar.
    const isDraftDelete = view.isDrafts && !isTrashContext;

    // The button is 76px wide: it gets the resulting state ("Read" / "Unread"),
    // not the full sentence, which wrapped onto three lines in French. The
    // long form stays as the title.
    const readLabel = hasUnread ? t("Read") : t("Unread");
    const readTitle = hasUnread ? t("Mark as read") : t("Mark as unread");
    const archiveLabel = isArchivedView ? t("Unarchive") : t("Archive");
    const trashLabel = isTrashContext ? t("Restore") : t("Delete");
    const trashTitle = isDraftDelete ? t("Delete draft") : trashLabel;
    const trashIcon = isDraftDelete
        ? "edit_off"
        : isTrashContext
          ? "restore_from_trash"
          : "delete";
    const trashAction = isDraftDelete
        ? () => actions.deleteDrafts(thread)
        : () => actions.setTrashed(thread, !isTrashContext);
    const showPanels = enabled && (isSwiping || openSide !== null);

    // The wrapper is rendered even when the gesture is off (desktop, selection
    // mode) and neutralised with `display: contents` instead: dropping it from
    // the tree would remount every row of the list each time selection mode is
    // entered or left.
    return (
        <div
            className={clsx("thread-item-swipe", {
                "thread-item-swipe--disabled": !enabled,
                "thread-item-swipe--swiping": isSwiping,
                "thread-item-swipe--open": openSide !== null,
            })}
            ref={containerRef}
            onClickCapture={handleClickCapture}
        >
            {showPanels && (
                <>
                    <div
                        className="thread-item-swipe__panel thread-item-swipe__panel--start"
                        aria-hidden="true"
                    >
                        <button
                            type="button"
                            tabIndex={-1}
                            className="thread-item-swipe__action thread-item-swipe__action--read"
                            onClick={() => runAction(toggleRead)}
                            title={readTitle}
                        >
                            <Icon
                                name={hasUnread ? "drafts" : "mark_email_unread"}
                                type={IconType.OUTLINED}
                            />
                            <span className="thread-item-swipe__action-label">{readLabel}</span>
                        </button>
                    </div>
                    {endWidth > 0 && (
                        <div
                            className="thread-item-swipe__panel thread-item-swipe__panel--end"
                            aria-hidden="true"
                        >
                            {canArchive && (
                                <button
                                    type="button"
                                    tabIndex={-1}
                                    className="thread-item-swipe__action thread-item-swipe__action--archive"
                                    onClick={() =>
                                        runAction(() => actions.setArchived(thread, !isArchivedView))
                                    }
                                    title={archiveLabel}
                                >
                                    <Icon
                                        name={isArchivedView ? "unarchive" : "archive"}
                                        type={IconType.OUTLINED}
                                    />
                                    <span className="thread-item-swipe__action-label">
                                        {archiveLabel}
                                    </span>
                                </button>
                            )}
                            {canTrash && (
                                <button
                                    type="button"
                                    tabIndex={-1}
                                    className="thread-item-swipe__action thread-item-swipe__action--trash"
                                    onClick={() => runAction(trashAction)}
                                    title={trashTitle}
                                >
                                    <Icon name={trashIcon} type={IconType.OUTLINED} />
                                    <span className="thread-item-swipe__action-label">
                                        {trashLabel}
                                    </span>
                                </button>
                            )}
                        </div>
                    )}
                </>
            )}
            <div className="thread-item-swipe__content" ref={contentRef}>
                {children}
            </div>
        </div>
    );
};

export default ThreadItemSwipe;
