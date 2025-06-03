import { useState, useEffect, MouseEventHandler } from 'react';
import { Attachment } from "@/features/api/gen/models";
import { useBlobUploadCreate } from "@/features/api/gen/blob/blob";
import { useMailboxContext } from '@/features/providers/mailbox';
import { useFormContext } from 'react-hook-form';
import { Button } from '@openfun/cunningham-react';
import { AttachmentItem } from '@/features/layouts/components/thread-view/components/thread-attachment-list/attachment-item';
import { useTranslation } from 'react-i18next';
import { useDropzone } from 'react-dropzone';
import { AttachmentHelper } from '@/features/utils/attachment-helper';
import { useDebounceCallback } from '@/hooks/use-debounce-callback';
import { DropZone } from './dropzone';
import { DriveAttachment } from './drive-attachment';
import { DriveFile } from '@/pages/drive-selection';

interface AttachmentUploaderProps {
    initialAttachments?: readonly Attachment[];
    onChange: () => void;
}

export const AttachmentUploader = ({
    initialAttachments = [],
    onChange
}: AttachmentUploaderProps) => {
    const form = useFormContext();
    const { t, i18n } = useTranslation();
    const { selectedMailbox } = useMailboxContext();
    const [attachments, setAttachments] = useState<(DriveFile | Attachment)[]>(initialAttachments.map((a) => ({ ...a, state: 'idle' })));
    const [uploadingQueue, setUploadingQueue] = useState<File[]>([]);
    const [failedQueue, setFailedQueue] = useState<File[]>([]);
    const { mutateAsync: uploadBlob } = useBlobUploadCreate();
    const debouncedOnChange = useDebounceCallback(onChange, 1000);
    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop: async (acceptedFiles) => {
            await Promise.all(acceptedFiles.map(uploadFile));
        }
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
            return e.id === a.id;
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

    const handleDriveAttachmentChange = (attachments: DriveFile[]) => {
        appendToAttachments(attachments);
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
            name: attachment.name
        })), { shouldDirty: true });
        form.setValue('driveAttachments', driveAttachments, { shouldDirty: true });
        if (form.formState.dirtyFields.attachments) {
            debouncedOnChange();
        }
    }, [attachments]);

    return (
        <section className="attachment-uploader" {...getRootProps()} onClick={handleClick}>
            <DropZone isHidden={!isDragActive} />
            <div className="attachment-uploader__input">
                <Button
                    color="tertiary"
                    icon={<span className="material-icons">attach_file</span>}
                    type="button"
                >
                    {t("message_form.attachments_uploader.input_label")}
                </Button>
                <DriveAttachment onChange={handleDriveAttachmentChange} />
                <p className="attachment-uploader__input__helper-text">
                    {t("message_form.attachments_uploader.or_drag_and_drop")}
                </p>
                <input {...getInputProps()} />
            </div>
            { [...attachments, ...uploadingQueue, ...failedQueue].length > 0 && (
                <div className="attachment-uploader__bucket">
                    <p className="attachment-bucket__counter">
                        <strong>{t("attachments.counter", { count: attachments.length })}</strong>{' '}
                        ({AttachmentHelper.getFormattedTotalSize(attachments, i18n.language)})
                    </p>
                    <div className="attachment-bucket__list">
                        {failedQueue.map((entry) => (
                            <AttachmentItem
                                key={`failed-${entry.name}-${entry.size}-${entry.lastModified}`}
                                attachment={entry}
                                variant="error"
                                errorAction={() => uploadFile(entry)}
                                onDelete={() => removeToFailedQueue([entry])}
                                canDownload={false}
                                errorMessage={t("message_form.attachments_uploader.error_message")}
                            />
                        ))}
                        {uploadingQueue.map((entry) => (
                            <AttachmentItem key={`uploading-${entry.name}-${entry.size}-${entry.lastModified}`} attachment={entry} isLoading />
                        ))}
                        {attachments.map((entry) => (
                            <AttachmentItem
                                key={'blobId' in entry ? entry.blobId : entry.id}
                                attachment={entry}
                                onDelete={() => removeToAttachments([entry])}
                            />
                        ))}
                    </div>
                </div>
            )}
        </section>
    );
}; 
