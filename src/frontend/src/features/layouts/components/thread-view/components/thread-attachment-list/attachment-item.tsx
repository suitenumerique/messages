import { Attachment } from "@/features/api/gen/models"
import { Button } from "@openfun/cunningham-react"
import { useTranslation } from "react-i18next";
import { AttachmentHelper } from "@/features/utils/attachment-helper";

export const AttachmentItem = ({ attachment }: { attachment: Attachment }) => {
    const { t } = useTranslation();
    const icon = AttachmentHelper.getIcon(attachment);
    const downloadUrl = AttachmentHelper.getDownloadUrl(attachment);

    return (
        <div className="attachment-item">
            <img className="attachment-item__icon" src={icon} alt="" />
            <div className="attachment-item-info">
                {attachment.name}
            </div>
            <div className="attachment-item-actions">
                <Button
                    aria-label={t("actions.download")}
                    title={t("actions.download")}
                    size="medium"
                    icon={<span className="material-icons">download</span>}
                    color="primary-text"
                    href={downloadUrl}
                    download={attachment.name}
                />
            </div>
        </div>
    )
}
