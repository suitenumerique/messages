import { useState, useEffect, MouseEventHandler } from 'react';
import { Attachment } from "@/features/api/gen/models";
import { useBlobUploadCreate } from "@/features/api/gen/blob/blob";
import { useMailboxContext } from '@/features/providers/mailbox';
import { useConfig } from '@/features/providers/config';
import { useFormContext } from 'react-hook-form';
import { Button, Field, useModals, VariantType } from '@gouvfr-lasuite/cunningham-react';
import { AttachmentItem, isAttachment } from '@/features/layouts/components/thread-view/components/thread-attachment-list/attachment-item';
import { useTranslation } from 'react-i18next';
import { useDropzone } from 'react-dropzone';
import { AttachmentHelper } from '@/features/utils/attachment-helper';
import { useDebounceCallback } from '@/hooks/use-debounce-callback';
import { DropZone } from './dropzone';
import { DriveAttachmentPicker, DriveFile } from './drive-attachment-picker';
import { Icon } from '@gouvfr-lasuite/ui-kit';
import clsx from 'clsx';

interface AttachmentUploaderProps {
    initialAttachments?: (DriveFile | Attachment)[];
    onChange: () => void;
    disabled?: boolean;
}

export const AttachmentUploader = ({
    initialAttachments = [],
    disabled = false,
    onChange
}: AttachmentUploaderProps) => {
    const form = useFormContext();
    const { t, i18n } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const config = useConfig();
    const modals = useModals();
    const MAX_ATTACHMENT_SIZE = config.MAX_OUTGOING_ATTACHMENT_SIZE;
    const [attachments, setAttachments] = useState<(DriveFile | Attachment)[]>(initialAttachments.map((a) => ({ ...a, state: 'idle' })));
    const [uploadingQueue, setUploadingQueue] = useState<File[]>([]);
    const [failedQueue, setFailedQueue] = useState<File[]>([]);
    const { mutateAsync: uploadBlob } = useBlobUploadCreate();
    const debouncedOnChange = useDebounceCallback(onChange, 1000);

    // Calculate current total size of attachments and pending uploads
    const attachmentsSize = attachments.reduce((acc, attachment) => {
        if (isAttachment(attachment)) return acc + attachment.size;
        return acc;
    }, 0);
    const uploadingQueueSize = uploadingQueue.reduce((acc, file) => acc + file.size, 0);
    const currentTotalSize = attachmentsSize + uploadingQueueSize;

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop: async (acceptedFiles) => {
            // Check cumulative size before uploading
            const newFilesSize = acceptedFiles.reduce((acc, file) => acc + file.size, 0);
            const totalSize = currentTotalSize + newFilesSize;

            if (totalSize > MAX_ATTACHMENT_SIZE) {
                modals.messageModal({
                    title: <span className="c__modal__text--centered">{t("Attachment size limit exceeded")}</span>,
                    children: <span className="c__modal__text--centered">{t("Cannot add attachment(s). Total size would be more than {{maxSize}}.", {
                        maxSize: AttachmentHelper.getFormattedSize(MAX_ATTACHMENT_SIZE, i18n.resolvedLanguage)
                    })}</span>,
                    messageType: VariantType.INFO,
                });
                return;
            }
            await Promise.all(acceptedFiles.map(uploadFile));
        },
        disabled,
        maxSize: MAX_ATTACHMENT_SIZE,
    });

    const addToUploadingQueue = (attachments: File[]) => setUploadingQueue(queue => [...queue, ...attachments]);
    const addToFailedQueue = (attachments: File[]) => setFailedQueue(queue => [...queue, ...attachments]);
    const removeToQueue = (queue: File[], attachments: File[]) => {
        return queue.filter((entry) => !attachments.some(a => a.name === entry.name && a.size === entry.size));
    }
    const removeToUploadingQueue = (attachments: File[]) => setUploadingQueue(uploadingQueue => removeToQueue(uploadingQueue, attachments));
    const removeToFailedQueue = (attachments: File[]) => setFailedQueue(failedQueue => removeToQueue(failedQueue, attachments));
    const appendToAttachments = (newAttachments: (DriveFile | Attachment)[]) => {
        // Append attachments to the end of the list and sort by descending created_at
        setAttachments(
            attachments => [...attachments, ...newAttachments].sort((a, b) => Number(new Date(b.created_at)) - Number(new Date(a.created_at)))
        );
    }

    const removeToAttachments = (entries: (DriveFile | Attachment)[]) => {
        setAttachments(attachments => attachments.filter((a) => !entries.some(e => {
            if ('blobId' in a && 'blobId' in e) return e.blobId === a.blobId;
            if ('id' in e && 'id' in a) return e.id === a.id;
            return false;
        })));
    }

    /**
     * Upload a file to the server,
     * add it to the uploading queue to update th UI and clean the failed queue to manage retry.
     * If the upload failed, add the file to the failed queue and remove it from the uploading queue.
     * If the upload succeed, remove the file from the uploading queue and append it to the attachments list.
     */
    const uploadFile = async (file: File) => {
        addToUploadingQueue([file]);
        removeToFailedQueue([file]);

        const response = await uploadBlob({
            mailboxId:selectedMailbox!.id,
            data: { file },
        });

        if (response.status >= 400) {
            addToFailedQueue([file]);
            removeToUploadingQueue([file]);
            return;
        }

        const newAttachment = { ...response.data, name: file.name, created_at: new Date().toISOString() } as Attachment;
        removeToUploadingQueue([file]);
        appendToAttachments([newAttachment]);
        return newAttachment;
    }

    /**
     * Handle the click event on the attachment uploader
     * If the click is within the bucket list, prevent the default behavior.
     * In this way, if the user clicks, for example, on the button to download an attachment,
     * the file dialog is not opened.
     */
    const handleClick:MouseEventHandler<HTMLElement> = (event) => {
        const hasClickInBucketList = (event.target as HTMLElement).closest('.attachment-bucket__list');
        if (!hasClickInBucketList) {
            getRootProps().onClick?.(event);
        }
    }

    const handleDriveAttachmentPick = (newAttachments: DriveFile[]) => {
        appendToAttachments(newAttachments);
    }
    /**
     * Update the form value when the attachments change.
     */
    useEffect(() => {
        // Only keep local attachments
        const localAttachments = attachments.filter(attachment => 'blobId' in attachment);
        const driveAttachments = attachments.filter(attachment => 'url' in attachment);
        form.setValue('attachments', localAttachments.map((attachment) => ({
            blobId: attachment.blobId,
            name: attachment.name,
            cid: 'cid' in attachment ? attachment.cid : undefined,
        })), { shouldDirty: true });
        form.setValue('driveAttachments', driveAttachments, { shouldDirty: true });
        if (form.formState.dirtyFields.attachments) {
            debouncedOnChange();
        }
    }, [attachments]);

    // Show informational text about the limit
    const infoText = t("Attachments must be less than {{size}}.", { size: AttachmentHelper.getFormattedSize(MAX_ATTACHMENT_SIZE, i18n.resolvedLanguage) });

    return (
        <Field
            text={infoText}
            state="default"
            fullWidth
        >
        <section className={clsx("attachment-uploader", { 'attachment-uploader--disabled': disabled })} {...getRootProps()} onClick={handleClick}>
            <DropZone isHidden={!isDragActive} />
            <div className="attachment-uploader__input">
                <Button
                    variant="secondary"
                    icon={<Icon name="attach_file" />}
                    type="button"
                    disabled={disabled}
                >
                    {t("Add attachments")}
                </Button>
                <DriveAttachmentPicker onPick={handleDriveAttachmentPick} />
                <p className="attachment-uploader__input__helper-text">
                    {t("or drag and drop some files")}
                </p>
                {/* This input is not focusable so we hide it from the screen reader and we give the priority to the button*/}
                <input {...getInputProps()} disabled={disabled} aria-hidden={true} />
            </div>
            { [...attachments, ...uploadingQueue, ...failedQueue].length > 0 && (
                <div className="attachment-uploader__bucket">
                    <p className="attachment-bucket__counter">
                        <strong>
                        {attachments.length > 0
                            ? t("{{count}} attachments", { count: attachments.length, defaultValue_one: "{{count}} attachment" })
                            : t("No attachments")}
                        </strong>{' '}
                        {attachments.filter(isAttachment).length > 0 && (
                            `(${AttachmentHelper.getFormattedTotalSize(attachments.filter(isAttachment), i18n.resolvedLanguage)})`
                        )}
                    </p>
                    <div className="attachment-bucket__list">
                        {failedQueue.map((entry) => (
                            <AttachmentItem
                                key={`failed-${entry.name}-${entry.size}-${entry.lastModified}`}
                                attachment={entry}
                                variant="error"
                                errorAction={() => uploadFile(entry)}
                                onDelete={disabled ? undefined : () => removeToFailedQueue([entry])}
                                canDownload={false}
                                errorMessage={t("The upload failed. Please try again.")}
                            />
                        ))}
                        {uploadingQueue.map((entry) => (
                            <AttachmentItem key={`uploading-${entry.name}-${entry.size}-${entry.lastModified}`} attachment={entry} isLoading />
                        ))}
                        {attachments.map((entry) => (
                            <AttachmentItem
                                key={'blobId' in entry ? entry.blobId : entry.id}
                                canDownload={false}
                                attachment={entry}
                                onDelete={disabled ? undefined : () => removeToAttachments([entry])}
                            />
                        ))}
                    </div>
                </div>
                )}
            </section>
        </Field>
    );
};
