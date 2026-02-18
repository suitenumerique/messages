import { useCallback } from 'react';
import { useModals } from '@gouvfr-lasuite/cunningham-react';
import { useTranslation } from 'react-i18next';

export const useConfirmBeforeClose = (isDirty: boolean, onClose: () => void) => {
    const { t } = useTranslation();
    const modals = useModals();

    const guardedOnClose = useCallback(async () => {
        if (!isDirty) {
            onClose();
            return;
        }
        const decision = await modals.confirmationModal({
            title: t('Unsaved changes'),
            children: t('You have unsaved changes. Are you sure you want to close?'),
        });
        if (decision === 'yes') {
            onClose();
        }
    }, [isDirty, onClose, modals, t]);

    return guardedOnClose;
};
