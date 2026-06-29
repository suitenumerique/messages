import { MouseEventHandler, useCallback, useMemo } from 'react';
import { Attachment } from "@/features/api/gen/models";
import { Button, Field } from '@gouvfr-lasuite/cunningham-react';
import { AttachmentItem, isAttachment, isDriveFile } from '@/features/layouts/components/thread-view/components/thread-attachment-list/attachment-item';
import { useTranslation } from 'react-i18next';
import { useDropzone } from 'react-dropzone';
import { AttachmentHelper } from '@/features/utils/attachment-helper';
import { useAttachmentPreview } from '@/features/providers/attachment-preview';
import { useConfig } from '@/features/providers/config';
import { DropZone } from './dropzone';
import { DriveAttachmentPicker, DriveFile } from './drive-attachment-picker';
import { Icon } from '@gouvfr-lasuite/ui-kit';
import clsx from 'clsx';

interface AttachmentUploaderProps {
    attachments: (DriveFile | Attachment)[];
    uploadingQueue: File[];
    failedQueue: File[];
    onUploadFiles: (files: File[]) => Promise<void>;
    onRemove: (entry: Attachment | DriveFile) => void;
    onRemoveFailedUpload: (file: File) => void;
    onRetry: (file: File) => void;
    onDriveAttachmentPick: (files: DriveFile[]) => void;
    disabled?: boolean;
    maxAttachmentSize: number;
}

export const AttachmentUploader = ({
    attachments,
    uploadingQueue,
    failedQueue,
    onUploadFiles,
    onRemove,
    onRemoveFailedUpload,
    onRetry,
    onDriveAttachmentPick,
    disabled = false,
    maxAttachmentSize,
}: AttachmentUploaderProps) => {
    const { t, i18n } = useTranslation();
    const { openPreview } = useAttachmentPreview();
    const { DRIVE } = useConfig();

    const { getRootProps, getInputProps, isDragActive } = useDropzone({
        onDrop: (acceptedFiles) => onUploadFiles(acceptedFiles),
        disabled,
        maxSize: maxAttachmentSize,
    });

    // The preview modal builds its file list from the thread's persisted
    // messages, which never include a draft's PJ. We therefore feed it the
    // draft's own attachments (local state) so they can be previewed.
    const previewFiles = useMemo(
        () => attachments.map((entry) =>
            isAttachment(entry)
                ? AttachmentHelper.toFilePreviewType(entry)
                : AttachmentHelper.driveFileToFilePreviewType(entry, DRIVE.preview_url),
        ),
        [attachments, DRIVE.preview_url],
    );
    const driveUrlById = useMemo(() => {
        const map = new Map<string, string>();
        for (const entry of attachments) {
            if (isDriveFile(entry)) map.set(entry.id, entry.url);
        }
        return map;
    }, [attachments]);
    const handlePreview = useCallback(
        (fileId: string) => openPreview(fileId, { files: previewFiles, driveUrlById }),
        [openPreview, previewFiles, driveUrlById],
    );

    const handleClick: MouseEventHandler<HTMLElement> = (event) => {
        const hasClickInBucketList = (event.target as HTMLElement).closest('.attachment-bucket__list');
        if (!hasClickInBucketList) {
            getRootProps().onClick?.(event);
        }
    }

    const infoText = t("Attachments must be less than {{size}}.", { size: AttachmentHelper.getFormattedSize(maxAttachmentSize, i18n.resolvedLanguage) });

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
                <DriveAttachmentPicker onPick={onDriveAttachmentPick} disabled={disabled} />
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
                                errorAction={() => onRetry(entry)}
                                onDelete={disabled ? undefined : () => onRemoveFailedUpload(entry)}
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
                                onDelete={disabled ? undefined : () => onRemove(entry)}
                                onPreview={handlePreview}
                            />
                        ))}
                    </div>
                </div>
                )}
            </section>
        </Field>
    );
};
