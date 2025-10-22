import { Attachment } from "@/features/api/gen/models/attachment";
import { AttachmentItem } from "./attachment-item";
import { useTranslation } from "react-i18next";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";


type AttachmentListProps = {
    attachments: readonly (DriveFile | Attachment)[]
}

export const AttachmentList = ({ attachments }: AttachmentListProps) => {
    const { t, i18n } = useTranslation();

    return (
        <section className="thread-attachment-list">
            <header className="thread-attachment-list__header">
                <p>
                    <strong>
                    {attachments.length > 0
                        ? t("{{count}} attachments", { count: attachments.length, defaultValue_one: "{{count}} attachment" })
                        : t("No attachments")}
                    </strong>{' '}
                    ({AttachmentHelper.getFormattedTotalSize(attachments, i18n.resolvedLanguage)})
                </p>
            </header>
            <div className="thread-attachment-list__body">
                {attachments.map((attachment) => <AttachmentItem key={`attachment-${attachment.name}-${attachment.size}-${attachment.created_at}`} attachment={attachment} />)}
            </div>
        </section>
    )
}
