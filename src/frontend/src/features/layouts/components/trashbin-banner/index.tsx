import { useTranslation } from "react-i18next";
import { ThreadsStatsRetrieve200, ThreadsStatsRetrieveStatsFields, useThreadsStatsRetrieve } from "@/features/api/gen";
import useEmptyTrash from "@/features/message/use-empty-trash";
import { useConfig } from "@/features/providers/config";
import { getThreadsStatsQueryKey, useMailboxContext } from "@/features/providers/mailbox";
import { useThreadSelection } from "@/features/providers/thread-selection";
import { HeaderBanner } from "@/features/ui/components/header-banner";
import { Icon } from "@/features/ui/components/icon";
import ViewHelper from "@/features/utils/view-helper";
import useAbility, { Abilities } from "@/hooks/use-ability";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";
import { Trash } from "@gouvfr-lasuite/ui-components/icons";

/**
 * Announces the retention policy of the Trash and Spam folders, whose messages
 * are permanently deleted once they have been there for TRASHBIN_CUTOFF_DAYS,
 * and offers to empty the current folder right away when it holds anything.
 * Spans the thread list and the thread view, as in Gmail.
 */
export const TrashbinBanner = () => {
    const { t } = useTranslation();
    const { TRASHBIN_CUTOFF_DAYS } = useConfig();
    const { selectedMailbox, unselectThread } = useMailboxContext();
    const { clearSelection } = useThreadSelection();
    const { emptyTrashbin } = useEmptyTrash();
    const canEmptyTrash = useAbility(Abilities.CAN_EMPTY_TRASH, selectedMailbox);
    // ViewHelper reads `window.location`, subscribe to the search params to
    // re-render when the user switches folder.
    useUrlSearchParams();
    const isSpamView = ViewHelper.isSpamView();
    const isTrashbinView = isSpamView || ViewHelper.isTrashedView();
    // Counted apart from the thread list, which a search may narrow down while
    // emptying always deletes the whole folder.
    const folderFilter = isSpamView ? "is_spam" : "has_trashed";
    const { data: statsResponse } = useThreadsStatsRetrieve({
        mailbox_id: selectedMailbox?.id,
        stats_fields: ThreadsStatsRetrieveStatsFields.all,
        [folderFilter]: 1,
    }, {
        query: {
            enabled: isTrashbinView && canEmptyTrash && !!selectedMailbox,
            // `stats_fields` belongs to the key: the sidebar already caches
            // `is_spam=1` with unread counts only.
            queryKey: getThreadsStatsQueryKey(
                selectedMailbox?.id,
                `${folderFilter}=1&stats_fields=${ThreadsStatsRetrieveStatsFields.all}`,
            ),
        },
    });
    const folderStats = statsResponse?.data as ThreadsStatsRetrieve200 | undefined;
    const showEmptyCta = canEmptyTrash && (folderStats?.all ?? 0) > 0;

    if (!isTrashbinView) return null;
    if (TRASHBIN_CUTOFF_DAYS <= 0 && !showEmptyCta) return null;

    const label = TRASHBIN_CUTOFF_DAYS <= 0
        ? null
        : isSpamView
            ? t('Messages that have been in spam for more than {{count}} days are automatically and permanently deleted.', { count: TRASHBIN_CUTOFF_DAYS })
            : t('Messages that have been in the trash for more than {{count}} days are automatically and permanently deleted.', { count: TRASHBIN_CUTOFF_DAYS });

    const handleEmpty = () => {
        if (!selectedMailbox) return;
        emptyTrashbin({
            mailboxId: selectedMailbox.id,
            scope: isSpamView ? 'spam' : 'trashed',
            // Cleared only once the deletion went through: the confirmation
            // modal lets the user back out with the open thread and selection intact.
            onSuccess: () => {
                unselectThread();
                clearSelection();
            },
        });
    };

    return (
        <HeaderBanner
            color="warning"
            label={label}
            variant="centered"
            stackOnMobile
            ctaProps={showEmptyCta ? {
                label: isSpamView ? t('Empty spam') : t('Empty trash'),
                icon: <Icon icon={Trash} />,
                onClick: handleEmpty,
            } : undefined}
        />
    );
};
