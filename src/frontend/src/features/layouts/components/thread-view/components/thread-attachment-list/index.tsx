import { Attachment } from "@/features/api/gen/models/attachment";
import { AttachmentItem } from "./attachment-item";
import { useTranslation } from "react-i18next";
import { AttachmentHelper } from "@/features/utils/attachment-helper";
import { DriveFile } from "@/features/forms/components/message-form/drive-attachment-picker";
import { Banner } from "@/features/ui/components/banner";
import { IconType } from "@gouvfr-lasuite/ui-components";
import { Icon } from "@/features/ui/components/icon";
import { SpamReport } from "../thread-message/types";


type AttachmentListProps = {
    attachments: readonly (DriveFile | Attachment)[]
    /** What was reported as spam, which disables these attachments. */
    spamReport?: SpamReport | null;
}

export const AttachmentList = ({ attachments, spamReport = null }: AttachmentListProps) => {
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
            {spamReport && (
                <div className="mb-sm">
                    <Banner type="info" icon={<Icon name="file_download_off" type={IconType.OUTLINED} />} fullWidth>
                        <p>
                            {spamReport === "message"
                                ? t('This message has been reported as spam. For your security, previewing and downloading attachments has been disabled.')
                                : t('This thread has been reported as spam. For your security, previewing and downloading attachments has been disabled.')}
                        </p>
                    </Banner>
                </div>
            )}
            <div className="thread-attachment-list__body">
                {attachments.map((attachment) => <AttachmentItem key={`attachment-${attachment.name}-${attachment.size}-${attachment.created_at}`} attachment={attachment} canDownload={!spamReport} canPreview={!spamReport} />)}
            </div>
        </section>
    )
}
