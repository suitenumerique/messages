import { useCallback } from 'react';
import { useModals } from '@gouvfr-lasuite/cunningham-react';
import { useTranslation } from 'react-i18next';

/**
 * Returns a guard that, when `isDirty` is true, asks the user to confirm losing
 * their unsaved changes. Resolves to `true` when it is safe to proceed (nothing
 * dirty, or the user accepted) and `false` otherwise. Use it to gate any action
 * that would discard in-progress edits (closing a modal, switching a tab, …).
 */
export const useConfirmUnsavedChanges = () => {
    const { t } = useTranslation();
    const modals = useModals();

    return useCallback(async (isDirty: boolean): Promise<boolean> => {
        if (!isDirty) {
            return true;
        }
        const decision = await modals.confirmationModal({
            title: t('Unsaved changes'),
            children: t('You have unsaved changes. Are you sure you want to close?'),
        });
        return decision === 'yes';
    }, [modals, t]);
};

export const useConfirmBeforeClose = (isDirty: boolean, onClose: () => void) => {
    const confirmUnsavedChanges = useConfirmUnsavedChanges();

    return useCallback(async () => {
        if (await confirmUnsavedChanges(isDirty)) {
            onClose();
        }
    }, [confirmUnsavedChanges, isDirty, onClose]);
};
