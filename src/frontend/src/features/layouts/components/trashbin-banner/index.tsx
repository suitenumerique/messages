import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-components";
import useEmptyTrash, { useEmptyTrashTask, useTrashbinFolderCount } from "@/features/message/use-empty-trash";
import { useConfig } from "@/features/providers/config";
import { useMailboxContext } from "@/features/providers/mailbox";
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
 * While a large folder is being emptied in the background, it says so instead.
 * Hidden during a search, even one restricted to these folders: the list then
 * shows matches, not the folder the banner is about.
 * Spans the thread list and the thread view, as in Gmail.
 */
export const TrashbinBanner = () => {
    const { t } = useTranslation();
    const { TRASHBIN_CUTOFF_DAYS } = useConfig();
    const { selectedMailbox, unselectThread } = useMailboxContext();
    const { clearSelection } = useThreadSelection();
    const { emptyTrashbin, isPending } = useEmptyTrash();
    const canEmptyTrash = useAbility(Abilities.CAN_EMPTY_TRASH, selectedMailbox);
    const isSearch = useUrlSearchParams().has('search');
    const isSpamView = ViewHelper.isSpamView();
    const isTrashbinView = !isSearch && (isSpamView || ViewHelper.isTrashedView());
    const scope = isSpamView ? 'spam' : 'trashed';
    const { isRunning: isEmptying } = useEmptyTrashTask(
        isTrashbinView ? selectedMailbox?.id : undefined,
        scope,
    );
    // Emptying always deletes the whole folder, whatever filter narrows the
    // list. Kept fetched by the main layout, so known as soon as the folder opens.
    const folderCount = useTrashbinFolderCount(selectedMailbox, scope);
    const hasFolderThreads = (folderCount ?? 0) > 0;
    const showEmptyCta = canEmptyTrash && !isEmptying && hasFolderThreads;

    if (!isTrashbinView) return null;
    if (TRASHBIN_CUTOFF_DAYS <= 0 && !showEmptyCta && !isEmptying) return null;

    const label = isEmptying
        ? (
            <span className="trashbin-banner__progress">
                <Spinner size="sm" />
                {isSpamView ? t('Spam is being emptied…') : t('Trash is being emptied…')}
            </span>
        )
        : TRASHBIN_CUTOFF_DAYS <= 0
        ? null
        : isSpamView
            ? t('Messages that have been in spam for more than {{count}} days are automatically and permanently deleted.', { count: TRASHBIN_CUTOFF_DAYS })
            : t('Messages that have been in the trash for more than {{count}} days are automatically and permanently deleted.', { count: TRASHBIN_CUTOFF_DAYS });

    const handleEmpty = () => {
        if (!selectedMailbox) return;
        emptyTrashbin({
            mailboxId: selectedMailbox.id,
            scope,
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
                icon: isPending ? <Spinner size="sm" /> : <Icon icon={Trash} />,
                disabled: isPending,
                onClick: handleEmpty,
            } : undefined}
        />
    );
};
