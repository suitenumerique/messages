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
                        <p>{props.mode === "reply" ? t('In reply to') : t('Forwarded message')}</p>
                        <p><strong>{t('From:')}</strong> {props.sender}</p>
                        <p><strong>{t('Subject:')}</strong> {props.subject}</p>
                        <p><strong>{t('Date:')}</strong> {DateHelper.formatDate(props.received_at, i18n.resolvedLanguage?.split('-')[0])}</p>
                        <p><strong>{t('To:')}</strong> {props.recipients}</p>
                    </blockquote>
                </div>
            )
        },
        // We don't embedded the quoted message as it is done by the backend
        // Take a look at the backend/core/mda/rfc5322/composer.py:477 for more details
        toExternalHTML: () => (<span />)
    }
)
