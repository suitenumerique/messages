import { useThreadSelection } from "@/features/providers/thread-selection";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import Image from "next/image";
import { useTranslation } from "react-i18next";

export const ThreadSelectionPlaceholder = () => {
    const { t } = useTranslation();
    const { selectedThreadIds, clearSelection } = useThreadSelection();

    return (
        <div className="thread-view thread-view--empty">
            <div>
                <Image src="/images/svg/selected-threads.svg" alt="" width={130} height={62} style={{ transform: 'translateY(4px)' }} />
                <div>
                    <p>{t('{{count}} selected threads', { count: selectedThreadIds.size })}</p>
                    <Button color="neutral" variant="tertiary" size="small" onClick={clearSelection}>{t('Disable thread selection')}</Button>
                </div>
            </div>
        </div>
    );
};
