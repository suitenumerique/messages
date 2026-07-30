import { Fragment, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { IconType } from "@gouvfr-lasuite/ui-kit";
import { Thread } from "@/features/api/gen/models";
import { useMailboxContext } from "@/features/providers/mailbox";
import { useThreadViewContext } from "@/features/layouts/components/thread-view/provider";
import { LabelsWidget, LabelsWidgetHandle } from "@/features/layouts/components/labels-widget";
import { Drawer } from "@/features/ui/components/drawer";
import useArchive from "@/features/message/use-archive";
import useTrash from "@/features/message/use-trash";
import useSpam from "@/features/message/use-spam";
import useDeleteDrafts from "@/features/message/use-delete-drafts";
import useStarred from "@/features/message/use-starred";
import useRead from "@/features/message/use-read";
import useCopyDeepLink from "@/features/message/use-copy-deep-link";
import useLeaveThread from "@/features/message/use-leave-thread";
import useAbility, { Abilities } from "@/hooks/use-ability";
import ViewHelper from "@/features/utils/view-helper";
import { isNativePlatform } from "@/features/native/platform";
import { MobileBottomBar } from "../bottom-bar";
import { Archive, Link, Restore, Star, StarFilled, TagAdd, Trash } from "@gouvfr-lasuite/ui-kit/icons";
import { Icon, IconProps } from "@/features/ui/components/icon";

/** Modes offered by the quick-reply CTA (bottom-right of the toolbar). */
export type QuickReplyMode = "reply" | "reply_all";

type MobileThreadToolbarProps = {
  thread: Thread;
  isArchived: boolean;
  isTrashed: boolean;
  /**
   * Mode of the quick-reply CTA, derived from the latest message: reply-all
   * when it has several recipients. Null when that message cannot be replied
   * to (draft, trashed, or a reply draft is already open).
   */
  quickReplyMode: QuickReplyMode | null;
  /** Opens the thread accesses modal owned by the thread view. */
  onOpenAccesses: () => void;
};

type DrawerAction = {
  key: string;
  label: string;
  icon: IconProps;
  onSelect: () => void;
  /** Draw a separator above this item — marks the start of an action group. */
  separatorBefore?: boolean;
};

/**
 * Native-only bottom toolbar shown while a conversation is open. Thread
 * management actions (trash/draft deletion, archive, more-options drawer)
 * sit on the left, the quick-reply CTA on the right. The drawer gathers
 * every available action, including those already surfaced as buttons.
 */
export const MobileThreadToolbar = ({ thread, isArchived, isTrashed, quickReplyMode, onOpenAccesses }: MobileThreadToolbarProps) => {
  const { t } = useTranslation();
  const { selectedMailbox, unselectThread } = useMailboxContext();
  const { requestReply, isMessageFormFocused } = useThreadViewContext();
  const { markAsArchived, markAsUnarchived } = useArchive();
  const { markAsTrashed, markAsUntrashed } = useTrash();
  const { markAsSpam, markAsNotSpam } = useSpam();
  const { deleteDrafts } = useDeleteDrafts();
  const { markAsStarred, markAsUnstarred } = useStarred();
  const { markAsReadAt } = useRead();
  const copyDeepLink = useCopyDeepLink();
  const { canLeaveThread, leaveThread } = useLeaveThread();
  const canSendMessages = useAbility(Abilities.CAN_SEND_MESSAGES, selectedMailbox);
  const canEditThread = useAbility(Abilities.CAN_EDIT_THREAD, thread);
  const canManageLabels = useAbility(Abilities.CAN_MANAGE_MAILBOX_LABELS, selectedMailbox);
  const labelsWidgetRef = useRef<LabelsWidgetHandle>(null);
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const isDraftsView = ViewHelper.isDraftsView();

  // While the composer is focused the BlockNote formatting toolbar takes the
  // spot above the keyboard, so step aside to avoid stacking two bars there.
  if (!isNativePlatform() || isMessageFormFocused) return null;

  const canReply = canSendMessages && canEditThread && !isTrashed && quickReplyMode !== null;
  // Same rules as the desktop action bar: archiving or reporting spam
  // requires edit rights and is irrelevant from the trash or drafts views;
  // archiving a spam thread makes no sense either.
  const canArchive = canEditThread && !isTrashed && !thread.is_spam && !isDraftsView;
  const canReportSpam = canEditThread && !isTrashed && !isDraftsView;
  // Starring a trashed or spam thread makes no sense (same rule as the
  // subject's star toggle).
  const canStar = !isTrashed && !thread.is_spam;
  const isStarred = thread.has_starred;

  const handleArchive = () => {
    const mutation = isArchived ? markAsUnarchived : markAsArchived;
    mutation({ threadIds: [thread.id], onSuccess: unselectThread });
  };

  // The left "remove" slot follows the context: deleting the draft from the
  // drafts view, restoring from the trash, trashing everywhere else.
  const removeAction = !canEditThread ? null : isDraftsView
    ? {
        label: t("Delete draft"),
        icon: { name: "edit_off", type: IconType.OUTLINED},
        onSelect: () => deleteDrafts({ threadIds: [thread.id], onSuccess: unselectThread }),
      }
    : isTrashed
      ? {
          label: t("Undelete"),
          icon: { icon:Restore },
          onSelect: () => markAsUntrashed({ threadIds: [thread.id], onSuccess: unselectThread }),
        }
      : {
          label: t("Move to trash"),
          icon: { icon: Trash },
          onSelect: () => markAsTrashed({ threadIds: [thread.id], onSuccess: unselectThread }),
        };

  const hasUnread = thread.has_unread;
  const drawerActionGroups: DrawerAction[][] = [
    canReply ? [
      {
        key: "reply",
        label: t("Reply"),
        icon: { name: "reply" },
        onSelect: () => requestReply("reply")
      },
      ...(quickReplyMode === "reply_all" ? [
          {
            key: "reply_all",
            label: t("Reply all"),
            icon: { name: "reply-all" },
            onSelect: () => requestReply("reply_all")
          },
        ] : []),
        { key: "forward",
          label: t("Forward"),
          icon: { name: "forward" },
          onSelect: () => requestReply("forward")
        },
    ] : [],
    [
      ...(canManageLabels && !isTrashed ? [{
        key: "labels",
        label: t("Manage labels"),
        icon: { icon: TagAdd },
        onSelect: () => labelsWidgetRef.current?.open(),
      }] : []),
      ...(canStar ? [{
        key: "star",
        label: isStarred ? t("Unstar") : t("Star"),
        icon: { icon: isStarred ? Star : StarFilled },
        onSelect: () => {
          const mutation = isStarred ? markAsUnstarred : markAsStarred;
          mutation({ threadIds: [thread.id] });
        },
      }] : []),
      {
        key: "read",
        label: hasUnread ? t("Mark as read") : t("Mark as unread"),
        icon: { name: hasUnread ? "mail-open" : "mail-unread" },
        onSelect: () => {
          if (hasUnread) {
            markAsReadAt({ threadIds: [thread.id], readAt: new Date().toISOString() });
          } else {
            // Leave the thread first so the auto-read observer of the open
            // view doesn't immediately mark it as read again (same order as
            // the desktop action bar).
            unselectThread();
            markAsReadAt({ threadIds: [thread.id], readAt: null });
          }
        },
      },
      {
        key: "copy-link",
        label: t("Copy link to thread"),
        icon: { icon: Link },
        onSelect: () => copyDeepLink()
      },
    ],
    [
      ...(removeAction ? [{ key: "remove", ...removeAction }] : []),
      ...(canArchive ? [{
        key: "archive",
        label: isArchived ? t("Unarchive") : t("Archive"),
        icon: isArchived
          ? { name: "unarchive", type: IconType.OUTLINED }
          : { icon: Archive },
        onSelect: handleArchive,
      }] : []),
      ...(canReportSpam ? [{
        key: "spam",
        label: thread.is_spam ? t("Remove spam report") : t("Report as spam"),
        icon: { name: thread.is_spam ? "report_off" : "report", type: IconType.OUTLINED },
        onSelect: () => {
          const mutation = thread.is_spam ? markAsNotSpam : markAsSpam;
          mutation({ threadIds: [thread.id], onSuccess: unselectThread });
        },
      }] : []),
    ],
    [
      { key: "accesses",
        label: t("Manage accesses"),
        icon: { name: "group", type: IconType.OUTLINED },
        onSelect: onOpenAccesses
      },
      ...(canLeaveThread ? [{
        key: "leave",
        label: t("Leave this thread"),
        icon: { name: "exit_to_app", type: IconType.OUTLINED },
        onSelect: leaveThread,
      }] : []),
    ],
  ];
  const drawerActions: DrawerAction[] = drawerActionGroups
    .filter((group) => group.length > 0)
    .flatMap((group, groupIndex) => group.map((action, actionIndex) => ({
      ...action,
      separatorBefore: groupIndex > 0 && actionIndex === 0,
    })));

  return (
    <>
      <MobileBottomBar className="mobile-thread-toolbar">
        <div className="mobile-thread-toolbar__group">
          {removeAction && (
            <Button
              variant="tertiary"
              onClick={removeAction.onSelect}
              icon={<Icon {...removeAction.icon} />}
              aria-label={removeAction.label}
            />
          )}
          {canArchive && (
            <Button
              variant="tertiary"
              onClick={handleArchive}
              icon={isArchived ? <Icon name="unarchive" type={IconType.OUTLINED} /> : <Icon icon={Archive} />}
              aria-label={isArchived ? t("Unarchive") : t("Archive")}
            />
          )}
          <Button
            variant="tertiary"
            onClick={() => setIsDrawerOpen(true)}
            icon={<Icon name="more_horiz" type={IconType.OUTLINED} />}
            aria-label={t("More options")}
            aria-haspopup="dialog"
            aria-expanded={isDrawerOpen}
          />
        </div>
        {canReply && (
          <Button
            className="mobile-thread-toolbar__reply"
            color="brand"
            variant="primary"
            onClick={() => requestReply(quickReplyMode!)}
            icon={<Icon name={quickReplyMode === "reply_all" ? "reply-all" : "reply"} type={IconType.OUTLINED} />}
            aria-label={quickReplyMode === "reply_all" ? t("Reply all") : t("Reply")}
          />
        )}
      </MobileBottomBar>
      <LabelsWidget
        ref={labelsWidgetRef}
        threadIds={[thread.id]}
        initialLabels={thread.labels}
        hideTrigger
      />
      {isDrawerOpen && createPortal(
        <>
          <div
            className="mobile-thread-toolbar__drawer-overlay"
            onClick={() => setIsDrawerOpen(false)}
          />
          <div className="mobile-thread-toolbar__drawer">
            <Drawer title={t("More options")} onClose={() => setIsDrawerOpen(false)}>
              <div className="drawer-list">
                {drawerActions.map((action) => (
                  <Fragment key={action.key}>
                    {action.separatorBefore && (
                      <hr className="mobile-thread-toolbar__drawer-separator" />
                    )}
                    <button
                      type="button"
                      className="drawer-list__item"
                      onClick={() => {
                        setIsDrawerOpen(false);
                        action.onSelect();
                      }}
                    >
                      <Icon {...action.icon} />
                      <span className="drawer-list__item-label">{action.label}</span>
                    </button>
                  </Fragment>
                ))}
              </div>
            </Drawer>
          </div>
        </>,
        document.body,
      )}
    </>
  );
};

export default MobileThreadToolbar;
