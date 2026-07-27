import { useTranslation } from "react-i18next";
import { Spinner } from "@gouvfr-lasuite/ui-components";
import { useEmptyCurrentFolder } from "@/features/message/use-empty-trash";
import { useConfig } from "@/features/providers/config";
import { HeaderBanner } from "@/features/ui/components/header-banner";
import { Icon } from "@/features/ui/components/icon";
import ViewHelper from "@/features/utils/view-helper";
import { Trash } from "@gouvfr-lasuite/ui-components/icons";

/**
 * Announces the retention policy of the Trash and Spam folders, whose messages
 * are permanently deleted once they have been there for TRASHBIN_CUTOFF_DAYS,
 * and offers to empty the current folder right away when it holds anything.
 * Without a retention policy there is nothing to announce: emptying moves to
 * the thread list actions rather than leaving a button alone in a banner.
 * While a large folder is being emptied in the background, it says so instead.
 * Spans the thread list and the thread view, as in Gmail.
 */
export const TrashbinBanner = () => {
    const { t } = useTranslation();
    const { TRASHBIN_CUTOFF_DAYS } = useConfig();
    const isSpamView = ViewHelper.isSpamView();
    const { isTrashbinView, isEmptying, canEmptyFolder, emptyFolder, emptyFolderLabel, isPending } = useEmptyCurrentFolder();

    if (!isTrashbinView) return null;
    if (TRASHBIN_CUTOFF_DAYS <= 0 && !isEmptying) return null;

    const label = isEmptying
        ? (
            <span className="trashbin-banner__progress">
                <Spinner size="sm" />
                {isSpamView ? t('Spam is being emptied…') : t('Trash is being emptied…')}
            </span>
        )
        : isSpamView
            ? t('Messages that have been in spam for more than {{count}} days are automatically and permanently deleted.', { count: TRASHBIN_CUTOFF_DAYS })
            : t('Messages that have been in the trash for more than {{count}} days are automatically and permanently deleted.', { count: TRASHBIN_CUTOFF_DAYS });

    return (
        <HeaderBanner
            color="warning"
            label={label}
            variant="centered"
            stackOnMobile
            ctaProps={canEmptyFolder ? {
                label: emptyFolderLabel,
                icon: isPending ? <Spinner size="sm" /> : <Icon icon={Trash} />,
                disabled: isPending,
                onClick: emptyFolder,
            } : undefined}
        />
    );
};
