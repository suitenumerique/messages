import { Attachment } from "@/features/api/gen/models/attachment";
import { AttachmentItem } from "./attachment-item";
import { useTranslation } from "react-i18next";
type AttachmentListProps = {
    attachments: readonly Attachment[]
}

export const AttachmentList = ({ attachments }: AttachmentListProps) => {
    const { t } = useTranslation();
    
    return (
        <section className="thread-attachment-list">
            <header className="thread-attachment-list__header">
                <p className="m-0"><strong>{t("attachments.counter", { count: attachments.length })}</strong></p>
            </header>
            <div className="thread-attachment-list__body">
                {attachments.map((attachment) => <AttachmentItem key={attachment.id} attachment={attachment} />)}
            </div>
        </section>
    )
}
