import { createReactBlockSpec } from "@blocknote/react";
import { useTranslation } from "react-i18next";
import { DateHelper } from "@/features/utils/date-helper";

export const QuotedMessageBlock = createReactBlockSpec(
    {
        type: "quoted-message",
        content: "none",
        isSelectable: false,
        propSchema: {
            mode: { default: "reply" }, // reply or forward
            messageId: { default: "" },
            subject: { default: "" },
            sender: { default: "" },
            recipients: { default: "" },
            received_at: { default: "" },
            textBody: { default: "" },
        }
    },
    {
        render: ({ block : { props }}) => {
            // eslint-disable-next-line react-hooks/rules-of-hooks
            const { t, i18n } = useTranslation();

            return (
                <div data-content-type="quote">
                    <blockquote>
                        <p>{props.mode === "reply" ? t('quoted_message_block.replied-message') : t('quoted_message_block.forwarded-message')}</p>
                        <p><strong>{t('quoted_message_block.from')}:</strong> {props.sender}</p>
                        <p><strong>{t('quoted_message_block.subject')}:</strong> {props.subject}</p>
                        <p><strong>{t('quoted_message_block.date')}:</strong> {DateHelper.formatDate(props.received_at, i18n.resolvedLanguage)}</p>
                        <p><strong>{t('quoted_message_block.to')}:</strong> {props.recipients}</p>
                    </blockquote>
                </div>
            )
        },
        // We don't embedded the quoted message as it is done by the backend
        // Take a look at the backend/core/mda/rfc5322/composer.py:477 for more details
        toExternalHTML: () => (<span />)
    }
)
