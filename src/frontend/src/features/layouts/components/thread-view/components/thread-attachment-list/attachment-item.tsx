import { Button } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next";
import { Icon, Spinner } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { Attachment } from "@/features/api/gen/models"
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DriveIcon } from "@/features/forms/components/message-form/drive-icon";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";

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
const isDriveFile = (attachment: Attachment | File | DriveFile): attachment is DriveFile => {
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
                        <Icon name="error" className="attachment-item-icon attachment-item-icon--error" />
                    :
                        (
                            <>
                                <img className="attachment-item-icon" src={icon} alt="" />
                                {isDriveFile(attachment) && <DriveIcon className="attachment-item-icon-drive" size="small" />}
                            </>
                        )
                    }
                </div>
                <p className="attachment-item-size">{AttachmentHelper.getFormattedSize(attachment.size, i18n.resolvedLanguage)}</p>
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
                                aria-label={t("Retry")}
                                title={t("Retry")}
                                icon={<Icon name="loop" />}
                                size="medium"
                                color="tertiary-text"
                                onClick={errorAction}
                            />
                        }
                        {
                            canDownload && downloadUrl &&
                            <Button
                                aria-label={t("Download")}
                                title={t("Download")}
                                size="medium"
                                icon={<Icon name="download" />}
                                color="tertiary-text"
                                href={downloadUrl}
                                download={attachment.name}
                            />
                        }
                        {
                            onDelete &&
                            <Button
                                aria-label={t("Delete")}
                                title={t("Delete")}
                                icon={<Icon name="close" />}
                                size="medium"
                                color="tertiary-text"
                                onClick={onDelete}
                            />
                        }
                    </>
                )}
            </div>
        </div>
    )
}
