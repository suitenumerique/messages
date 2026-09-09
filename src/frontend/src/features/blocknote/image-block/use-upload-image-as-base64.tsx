import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useModals, VariantType } from "@gouvfr-lasuite/ui-components";
import { ALLOWED_IMAGE_MIME_TYPES } from '@/features/blocknote/image-block';
import { AttachmentHelper } from '@/features/utils/attachment-helper';

/**
 * Hook that returns an `uploadFile` function compatible with BlockNote's
 * `useCreateBlockNote({ uploadFile })`. Images are read as base64 data URLs
 * and stored directly in the block content (no blob upload).
 *
 * Used by TemplateComposer and SignatureComposer where content is persisted
 * as self-contained HTML/JSON (no attachment system).
 *
 * Returns `null` when the file is rejected (wrong type, too large, read error)
 * so that the caller can handle block cleanup.
 */
export const useUploadImageAsBase64 = (maxImageSize: number) => {
    const { t, i18n } = useTranslation();
    const modals = useModals();

    const uploadFile = useCallback(
        (file: File): Promise<string | null> => {
            if (!ALLOWED_IMAGE_MIME_TYPES.includes(file.type)) {
                return Promise.resolve(null);
            }

            if (file.size > maxImageSize) {
                modals.messageModal({
                    title: (
                        <span className="c__modal__text--centered">
                            {t('Image size limit exceeded')}
                        </span>
                    ),
                    children: (
                        <span className="c__modal__text--centered">
                            {t('Cannot add image. File size exceeds the {{maxSize}} limit.', {
                                maxSize: AttachmentHelper.getFormattedSize(
                                    maxImageSize,
                                    i18n.resolvedLanguage,
                                ),
                            })}
                        </span>
                    ),
                    messageType: VariantType.INFO,
                });
                return Promise.resolve(null);
            }

            return new Promise<string | null>((resolve) => {
                const reader = new FileReader();
                reader.onload = () => resolve(reader.result as string);
                reader.onerror = () => resolve(null);
                reader.readAsDataURL(file);
            });
        },
        [maxImageSize, modals, t, i18n.resolvedLanguage],
    );

    return uploadFile;
};
