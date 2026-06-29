import { Button } from "@gouvfr-lasuite/cunningham-react"
import { useTranslation } from "react-i18next";
import { Icon, Spinner } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";
import { Attachment } from "@/features/api/gen/models"
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DriveIcon } from "@/features/forms/components/message-form/drive-icon";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";
import { DriveUploadButton } from "./drive-upload-button";
import { DrivePreviewLink } from "./drive-preview-link";
import { useAttachmentPreview } from "@/features/providers/attachment-preview";

type AttachmentItemProps = {
    attachment: Attachment | File | DriveFile;
    isLoading?: boolean;
    canDownload?: boolean;
    /**
     * Whether clicking the item opens the inline preview. Disabled (alongside
     * download) for spam threads, where attachments must not be opened.
     */
    canPreview?: boolean;
    variant?: "error" | "default";
    errorMessage?: string;
    errorAction?: () => void;
    onDelete?: () => void;
    /**
     * Override the preview opener. The default aggregates the current thread's
     * attachments; a draft compose form passes its own files here so its
     * (not-yet-persisted) PJ can be previewed.
     */
    onPreview?: (fileId: string) => void;
}

export const isAttachment = (attachment: Attachment | File | DriveFile): attachment is Attachment => {
    return 'blobId' in attachment;
}
export const isDriveFile = (attachment: Attachment | File | DriveFile): attachment is DriveFile => {
    return 'url' in attachment;
}
export const isInlineImage = (attachment: Attachment | File | DriveFile): boolean => {
    return isAttachment(attachment) && !!attachment.cid;
}

export const AttachmentItem = ({ attachment, isLoading = false, canDownload = true, canPreview = true, variant = "default", errorMessage, errorAction, onDelete, onPreview }: AttachmentItemProps) => {
    const { t, i18n } = useTranslation();
    const { openPreview } = useAttachmentPreview();
    const icon = AttachmentHelper.getIcon(attachment);
    const downloadUrl = isAttachment(attachment) || isDriveFile(attachment) ? AttachmentHelper.getDownloadUrl(attachment) : undefined;

    // The viewer can navigate across every persisted attachment of the
    // thread — including Drive files, which fall back to a "Open in Drive"
    // action inside the modal. Files mid-upload (raw ``File``) and
    // errored items are excluded: there is nothing to render yet.
    const isPreviewable = canPreview && (isAttachment(attachment) || isDriveFile(attachment)) && variant !== "error";
    const previewableId = isAttachment(attachment)
        ? attachment.blobId
        : isDriveFile(attachment)
            ? attachment.id
            : undefined;

    const triggerPreview = () => {
        if (!isPreviewable || !previewableId) return;
        (onPreview ?? openPreview)(previewableId);
    };

    return (
        <div
            // Stable DOM anchor so the preview sidebar's "Show in conversation"
            // can scroll back to this exact attachment. ``previewableId`` is
            // the same id used as ``FilePreviewType.id``.
            id={previewableId ? `attachment-anchor-${previewableId}` : undefined}
            className={clsx("attachment-item", {
                "attachment-item--loading": isLoading,
                "attachment-item--error": variant === "error",
                "attachment-item--previewable": isPreviewable,
            })}
            title={attachment.name}
        >
            {/* Stretched primary action: a real button covering the card opens
                the preview. Keeping it a sibling (not a wrapper) avoids nesting
                interactive controls inside the action buttons below. */}
            {isPreviewable && (
                <button
                    type="button"
                    className="attachment-item-preview-trigger"
                    onClick={triggerPreview}
                    aria-label={t("Preview {{name}}", { name: attachment.name })}
                />
            )}
            <div className="attachment-item-metadata">
                <div className="attachment-item-icon-container">
                    {variant === "error" ?
                        <Icon name="error" className="attachment-item-icon attachment-item-icon--error" />
                        :
                        (
                            <>
                                <img className="attachment-item-icon" src={icon} alt="" />
                                {isDriveFile(attachment) && <DriveIcon className="attachment-item-icon-drive" size="small" />}
                                {isInlineImage(attachment) && <Icon name="wysiwyg" className="attachment-item-icon-inline" />}
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
                                color={variant === "error" ? "error" : "brand"}
                                variant="tertiary"
                                onClick={errorAction}
                            />
                        }
                        {
                            canDownload && downloadUrl && (
                                <>
                                    <Button
                                        aria-label={t("Download")}
                                        title={t("Download")}
                                        size="medium"
                                        icon={<Icon name="download" />}
                                        color={variant === "error" ? "error" : "brand"}
                                        variant="tertiary"
                                        href={downloadUrl}
                                        download={attachment.name}
                                    />
                                    {isAttachment(attachment) && <DriveUploadButton attachment={attachment} />}
                                </>
                            )
                        }
                        {isDriveFile(attachment) && <DrivePreviewLink fileId={attachment.id} />}
                        {
                            onDelete &&
                            <Button
                                aria-label={t("Delete")}
                                title={t("Delete")}
                                icon={<Icon name="close" />}
                                size="medium"
                                color={variant === "error" ? "error" : "brand"}
                                variant="tertiary"
                                onClick={onDelete}
                            />
                        }
                    </>
                )}
            </div>
        </div>
    )
}
