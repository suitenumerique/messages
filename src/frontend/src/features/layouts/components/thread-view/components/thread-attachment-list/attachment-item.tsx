import { Attachment } from "@/features/api/gen/models"
import { Button } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import clsx from "clsx";

type AttachmentItemProps = {
    attachment: Attachment | File;
    isLoading?: boolean;
    canDownload?: boolean;
    variant?: "error" | "default";
    errorMessage?: string;
    errorAction?: () => void;
    onDelete?: () => void;
}

const isAttachment = (attachment: Attachment | File): attachment is Attachment => {
    return 'blobId' in attachment;
}

export const AttachmentItem = ({ attachment, isLoading = false, canDownload = true, variant = "default", errorMessage, errorAction, onDelete }: AttachmentItemProps) => {
    const { t, i18n } = useTranslation();
    const icon = AttachmentHelper.getIcon(attachment);
    const downloadUrl = isAttachment(attachment) ? AttachmentHelper.getDownloadUrl(attachment) : undefined;

    return (
        <div className={clsx("attachment-item", { "attachment-item--loading": isLoading, "attachment-item--error": variant === "error" })} title={attachment.name}>
            <div className="attachment-item-metadata">
                { variant === "error" ?
                    <span className="attachment-item-icon material-icons">error</span>
                :
                    <img className="attachment-item-icon" src={icon} alt="" />
                }
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
                    </>
                )}
            </div>
        </div>
    )
}
