import { Button } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";

type ThreadViewEmptyProps = {
    showImportButton?: boolean;
    label?: string;
}

/**
 * Placeholder shown in the thread-view panel when no conversation is open:
 * either nothing is selected, or a just-closed thread is still settling its
 * navigation. Kept as a standalone component so the thread view and the
 * mailbox page render the exact same empty state.
 */
export const ThreadViewEmpty = ({ showImportButton = false, label }: ThreadViewEmptyProps) => {
    const { t } = useTranslation();

    return (
        <div className="thread-view thread-view--empty">
            <div>
                <img src="/images/svg/read-mail.svg" alt="" width={60} height={60} />
                <p>{label ?? t('Select a thread')}</p>
                {showImportButton && (
                    <Button href="#modal-message-importer">{t('Import messages')}</Button>
                )}
            </div>
        </div>
    );
};
