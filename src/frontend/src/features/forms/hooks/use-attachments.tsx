import { useState, useEffect, useCallback } from 'react';
import { UseFormReturn } from 'react-hook-form';
import { Attachment, blobUploadCreateResponse201, useBlobUploadCreate } from '@/features/api/gen';
import { DriveFile } from '../components/message-form/drive-attachment-picker';
import { MessageFormValues } from '../components/message-form';
import { getBlobDownloadRetrieveUrl } from '@/features/api/gen';
import { getRequestUrl } from '@/features/api/utils';
import { useDebounceCallback } from '@/hooks/use-debounce-callback';
import { isAttachment } from '@/features/layouts/components/thread-view/components/thread-attachment-list/attachment-item';
import { useTranslation } from 'react-i18next';
import { useModals, VariantType } from "@gouvfr-lasuite/ui-components";
import { AttachmentHelper } from '@/features/utils/attachment-helper';

interface UseAttachmentsOptions {
    mailboxId: string;
    initialAttachments: (Attachment | DriveFile)[];
    form: UseFormReturn<MessageFormValues>;
    onChange: () => void;
    maxAttachmentSize: number;
}

export interface UseAttachmentsReturn {
    // State
    attachments: (Attachment | DriveFile)[];
    uploadingQueue: File[];
    failedQueue: File[];

    // For MessageComposer
    uploadInlineImage: (file: File) => Promise<{ url: string; blobId: string } | null>;
    uploadAsAttachment: (file: File) => Promise<void>;
    removeInlineImage: (blobId: string) => void;

    // For AttachmentUploader
    uploadFiles: (files: File[]) => Promise<void>;
    removeAttachment: (entry: Attachment | DriveFile) => void;
    removeFailedUpload: (file: File) => void;
    retryUpload: (file: File) => void;
    addDriveFiles: (files: DriveFile[]) => void;

    // Computed
    totalSize: number;
    maxAttachmentSize: number;
}

export const useAttachments = ({
    mailboxId,
    initialAttachments,
    form,
    onChange,
    maxAttachmentSize,
}: UseAttachmentsOptions): UseAttachmentsReturn => {
    const { t, i18n } = useTranslation();
    const modals = useModals();
    const [attachments, setAttachments] = useState<(DriveFile | Attachment)[]>(
        initialAttachments.map((a) => ({ ...a })),
    );
    const [uploadingQueue, setUploadingQueue] = useState<File[]>([]);
    const [failedQueue, setFailedQueue] = useState<File[]>([]);
    const { mutateAsync: uploadBlob } = useBlobUploadCreate();
    const debouncedOnChange = useDebounceCallback(onChange, 1000);

    // Computed total size (attachments + uploading queue)
    const attachmentsSize = attachments.reduce((acc, attachment) => {
        if (isAttachment(attachment)) return acc + attachment.size;
        return acc;
    }, 0);
    const uploadingQueueSize = uploadingQueue.reduce((acc, file) => acc + file.size, 0);
    const totalSize = attachmentsSize + uploadingQueueSize;

    // Queue helpers
    const addToUploadingQueue = (files: File[]) =>
        setUploadingQueue((queue) => [...queue, ...files]);
    const addToFailedQueue = (files: File[]) =>
        setFailedQueue((queue) => [...queue, ...files]);
    const removeToQueue = (queue: File[], files: File[]) =>
        queue.filter((entry) => !files.some((f) => f.name === entry.name && f.size === entry.size));
    const removeToUploadingQueue = (files: File[]) =>
        setUploadingQueue((queue) => removeToQueue(queue, files));
    const removeToFailedQueue = (files: File[]) =>
        setFailedQueue((queue) => removeToQueue(queue, files));

    const appendToAttachments = useCallback((newAttachments: (DriveFile | Attachment)[]) => {
        setAttachments((prev) =>
            [...prev, ...newAttachments].sort(
                (a, b) => Number(new Date(b.created_at)) - Number(new Date(a.created_at)),
            ),
        );
    }, []);

    const isSizeLimitExceeded = useCallback(
        (additionalSize: number): boolean => {
            if (totalSize + additionalSize <= maxAttachmentSize) return false;
            modals.messageModal({
                title: (
                    <span className="c__modal__text--centered">
                        {t('Attachment size limit exceeded')}
                    </span>
                ),
                children: (
                    <span className="c__modal__text--centered">
                        {t('Cannot add attachment(s). Total size would be more than {{maxSize}}.', {
                            maxSize: AttachmentHelper.getFormattedSize(
                                maxAttachmentSize,
                                i18n.resolvedLanguage,
                            ),
                        })}
                    </span>
                ),
                messageType: VariantType.INFO,
            });
            return true;
        },
        [totalSize, maxAttachmentSize, modals, t, i18n.resolvedLanguage],
    );

    /**
     * Upload a single file as a regular (non-inline) attachment.
     */
    const uploadAsAttachment = useCallback(
        async (file: File): Promise<void> => {
            if (isSizeLimitExceeded(file.size)) return;

            addToUploadingQueue([file]);
            removeToFailedQueue([file]);

            try {
                const response = await uploadBlob({ mailboxId, data: { file } });
                const newAttachment = {
                    ...response.data,
                    name: file.name,
                    created_at: new Date().toISOString(),
                } as Attachment;
                appendToAttachments([newAttachment]);
            } catch {
                addToFailedQueue([file]);
            } finally {
                removeToUploadingQueue([file]);
            }
        },
        [isSizeLimitExceeded, mailboxId, uploadBlob, appendToAttachments],
    );

    /**
     * Upload a file as an inline image and return its URL + blobId for BlockNote.
     */
    const uploadInlineImage = useCallback(
        async (file: File): Promise<{ url: string; blobId: string } | null> => {
            if (isSizeLimitExceeded(file.size)) return null;

            addToUploadingQueue([file]);
            removeToFailedQueue([file]);

            try {
                const response = await uploadBlob({ mailboxId, data: { file } });
                const blobId = (response as blobUploadCreateResponse201).data.blobId;

                const newAttachment = {
                    ...(response as blobUploadCreateResponse201).data,
                    name: file.name,
                    cid: blobId,
                    created_at: new Date().toISOString(),
                } as Attachment;
                appendToAttachments([newAttachment]);

                const url = getRequestUrl(getBlobDownloadRetrieveUrl(blobId));
                return { url, blobId };
            } catch {
                addToFailedQueue([file]);
                return null;
            } finally {
                removeToUploadingQueue([file]);
            }
        },
        [isSizeLimitExceeded, mailboxId, uploadBlob, appendToAttachments],
    );

    /**
     * Upload multiple files as regular attachments (used by the dropzone).
     */
    const uploadFiles = useCallback(
        async (files: File[]): Promise<void> => {
            const newFilesSize = files.reduce((acc, file) => acc + file.size, 0);
            if (isSizeLimitExceeded(newFilesSize)) return;
            await Promise.all(files.map(uploadAsAttachment));
        },
        [isSizeLimitExceeded, uploadAsAttachment],
    );

    /**
     * Remove a specific attachment (regular or drive).
     */
    const removeAttachment = useCallback((entry: Attachment | DriveFile) => {
        setAttachments((prev) =>
            prev.filter((a) => {
                if ('blobId' in a && 'blobId' in entry) return a.blobId !== entry.blobId;
                if ('id' in a && 'id' in entry) return a.id !== entry.id;
                return true;
            }),
        );
    }, []);

    /**
     * Remove an inline image from the attachments (called when the image block is deleted from the editor).
     */
    const removeInlineImage = useCallback((cid: string) => {
        setAttachments((prev) => prev.filter((a) => !('cid' in a && a.cid === cid)));
    }, []);

    /**
     * Remove a failed upload from the failed queue.
     */
    const removeFailedUpload = useCallback((file: File) => {
        removeToFailedQueue([file]);
    }, []);

    /**
     * Retry uploading a failed file.
     */
    const retryUpload = useCallback(
        (file: File) => {
            uploadAsAttachment(file);
        },
        [uploadAsAttachment],
    );

    /**
     * Add Drive files to the attachment list.
     */
    const addDriveFiles = useCallback(
        (files: DriveFile[]) => {
            appendToAttachments(files);
        },
        [appendToAttachments],
    );

    // One-way sync: attachments → form values
    useEffect(() => {
        const localAttachments = attachments.filter(
            (attachment): attachment is Attachment => 'blobId' in attachment,
        );
        const driveAttachments = attachments.filter(
            (attachment): attachment is DriveFile => 'url' in attachment,
        );

        form.setValue(
            'attachments',
            localAttachments.map((attachment) => ({
                blobId: attachment.blobId,
                name: attachment.name,
                cid: 'cid' in attachment ? attachment.cid : undefined,
                size: attachment.size,
            })),
            { shouldDirty: true },
        );
        form.setValue('driveAttachments', driveAttachments, { shouldDirty: true });

        if (form.formState.dirtyFields.attachments || form.formState.dirtyFields.driveAttachments) {
            debouncedOnChange();
        }
    }, [attachments]);

    return {
        attachments,
        uploadingQueue,
        failedQueue,
        uploadInlineImage,
        uploadAsAttachment,
        removeInlineImage,
        uploadFiles,
        removeAttachment,
        removeFailedUpload,
        retryUpload,
        addDriveFiles,
        totalSize,
        maxAttachmentSize,
    };
};
