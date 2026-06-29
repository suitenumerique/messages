import { Attachment } from "@/features/api/gen/models/attachment";
import { AttachmentItem } from "./attachment-item";
import { useTranslation } from "react-i18next";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";
import { useMailboxContext } from "@/features/providers/mailbox";
import { Banner } from "@/features/ui/components/banner";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";


type AttachmentListProps = {
    attachments: readonly (DriveFile | Attachment)[]
}

export const AttachmentList = ({ attachments }: AttachmentListProps) => {
    const { t, i18n } = useTranslation();
    const { selectedThread } = useMailboxContext();

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
            {selectedThread?.is_spam && (
                <div className="mb-sm">
                    <Banner type="info" icon={<Icon name="file_download_off" type={IconType.OUTLINED} />} fullWidth>
                        <p>{t('This thread has been reported as spam. For your security, previewing and downloading attachments has been disabled.')}</p>
                    </Banner>
                </div>
            )}
            <div className="thread-attachment-list__body">
                {attachments.map((attachment) => <AttachmentItem key={`attachment-${attachment.name}-${attachment.size}-${attachment.created_at}`} attachment={attachment} canDownload={!selectedThread?.is_spam} canPreview={!selectedThread?.is_spam} />)}
            </div>
        </section>
    )
}
