import { Attachment } from "@/features/api/gen/models"
import { Button, Tooltip } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { DriveFile } from "@/pages/drive-selection";
import { DriveIcon } from "@/features/forms/components/message-form/drive-attachment";
import { useCallback, useState } from "react";
import { openSaver } from "@gouvfr-lasuite/drive-sdk";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";

type AttachmentItemProps = {
    attachment: Attachment | File | DriveFile;
    isLoading?: boolean;
    canDownload?: boolean;
    variant?: "error" | "default";
    errorMessage?: string;
    errorAction?: () => void;
    onDelete?: () => void;
}

const isAttachment = (attachment: Attachment | File | DriveFile): attachment is Attachment => {
    return 'blobId' in attachment;
}
const isDriveFile = (attachment: Attachment | File | DriveFile): attachment is Attachment => {
    return 'url' in attachment;
}

export const AttachmentItem = ({ attachment, isLoading = false, canDownload = true, variant = "default", errorMessage, errorAction, onDelete }: AttachmentItemProps) => {
    const { t, i18n } = useTranslation();
    const icon = AttachmentHelper.getIcon(attachment);
    const downloadUrl = isAttachment(attachment) || isDriveFile(attachment) ? AttachmentHelper.getDownloadUrl(attachment) : undefined;

    return (
        <div className={clsx("attachment-item", { "attachment-item--loading": isLoading, "attachment-item--error": variant === "error" })} title={attachment.name}>
            <div className="attachment-item-metadata">
                <div className="attachment-item-icon-container">
                    { variant === "error" ?
                        <span className="attachment-item-icon attachment-item-icon--error material-icons">error</span>
                    :
                        (
                            <>
                                <img className="attachment-item-icon" src={icon} alt="" />
                                {isDriveFile(attachment) && <DriveIcon className="attachment-item-icon-drive" size="small" />}
                            </>
                        )
                    }
                </div>
                <p className="attachment-item-size">{AttachmentHelper.getFormattedSize(attachment.size, i18n.language)}</p>
            </div>
            <div className="attachment-item-content">
                <p className="attachment-item-name">{attachment.name}</p>
                {errorMessage && <p className="attachment-item-error-message">{errorMessage}</p>}
            </div>
            <div className="attachment-item-actions">
                {isLoading ? (
                    <Spinner />
                ) : (
                    <>
                        {
                            variant === "error" && errorAction &&
                            <Button
                                aria-label={t("actions.retry")}
                                title={t("actions.retry")}
                                icon={<span className="material-icons">loop</span>}
                                size="medium"
                                color="tertiary-text"
                                onClick={errorAction}
                            />
                        }
                        {
                            canDownload && downloadUrl &&
                            <Button
                                aria-label={t("actions.download")}
                                title={t("actions.download")}
                                size="medium"
                                icon={<span className="material-icons">download</span>}
                                color="tertiary-text"
                                href={downloadUrl}
                                download={attachment.name}
                            />
                        }
                        {
                            onDelete &&
                            <Button
                                aria-label={t("actions.delete")}
                                title={t("actions.delete")}
                                icon={<span className="material-icons">close</span>}
                                size="medium"
                                color="tertiary-text"
                                onClick={onDelete}
                            />
                        }
                        {
                            !isDriveFile(attachment) && (
                                <DriveUploadButton attachment={attachment} />
                            )
                        }
                    </>
                )}
            </div>
        </div>
    )
}

type DriveUploadButtonProps = {
    attachment: Attachment;
}

const DriveUploadButton = ({ attachment }: DriveUploadButtonProps) => {
    const [state, setState] = useState<'idle' | 'busy' | 'success'>('idle');
    const { t } = useTranslation();
    
    const handleUpload = useCallback(async () => {
        setState('busy');
        const file = await fetch(AttachmentHelper.getDownloadUrl(attachment), {
            credentials: "include",
        }).then(res => res.blob());
        await openSaver({
            files: [{
                title: attachment.name,
                object: file
            }]
        }, {
            url: "http://localhost:3001/sdk",
        })
        setTimeout(() => {
            setState('success');
            setTimeout(() => {
                setState('idle');
            }, 1500);
        }, 1000);
    }, []);

    return (
        <div className="attachment-item-upload-button-container">
            <Tooltip content={t("tooltips.upload_to_drive")}>
                <Button
                    aria-label={t("actions.delete")}
                    title={t("actions.delete")}
                    icon={state === 'busy' ? <Spinner /> : state === 'success' ? <span className="material-icons">check</span> : <span className="material-icons">cloud_upload</span>}
                    size="medium"
                    color="tertiary-text"
                    onClick={handleUpload}
                />
            </Tooltip>
        {
            <p className={clsx('attachment-item-upload-success',{ 'attachment-item-upload-success--visible': state === 'success' })}>{t('attachments.upload_to_drive_success')}</p>
        }
        </div>
    )
}
