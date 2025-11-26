import { MailboxAdminCreate } from "@/features/api/gen/models/mailbox_admin_create";
import { Banner } from "@/features/ui/components/banner";
import { handle } from "@/features/utils/errors";
import MailboxHelper from "@/features/utils/mailbox-helper";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

type AdminMailboxCredentialsProps = {
    mailbox: MailboxAdminCreate;
}
const AdminMailboxCredentials = ({ mailbox }: AdminMailboxCredentialsProps) => {
    const { t } = useTranslation();
    const [clipboardState, setClipboardState] = useState<'idle' | 'copied' | 'error'>('idle');

    const credentialText = useMemo(() => {
        if (!mailbox.one_time_password) return '';
        const id = MailboxHelper.toString(mailbox);
        const password = mailbox.one_time_password;
        return t("create_mailbox_modal.success.credential_text",
            {
                id,
                password,
                defaultValue: 'Your Messages credentials are:\n- Email: {{id}}\n- Password: {{password}}\n\nIt will be asked to change your password at your first login.',
            });
    }, [mailbox]);

    const handleCopyToClipboard = async () => {
        try {
            await navigator.clipboard.writeText(credentialText);
            setClipboardState('copied');
        } catch (error) {
            setClipboardState('error');
            handle(new Error("Failed to copy credentials to clipboard."), { extra: { error } });
        }
        setTimeout(() => setClipboardState('idle'), 1337);
    };

    return (
        <div className="admin-mailbox-credentials">
            <div className="admin-mailbox-credentials__content">
                <dl>
                    <dt>{t('Identity')}</dt>
                    <dd>{MailboxHelper.toString(mailbox)}</dd>
                    <dt>{t('Temporary password')}</dt>
                    <dd>{mailbox.one_time_password}</dd>
                </dl>
                <Button
                    color="secondary"
                    icon={<Icon name={clipboardState === 'copied' ? "check" : clipboardState === 'error' ? "close" : "content_copy"} />}
                    onClick={handleCopyToClipboard}
                >
                    {clipboardState === 'idle' && t('Copy to clipboard')}
                    {clipboardState === 'copied' && t('Credentials copied!')}
                    {clipboardState === 'error' && t('Unable to copy credentials.')}
                </Button>
            </div>
            <Banner type="warning" icon={<Icon name="info" type={IconType.OUTLINED} />}>
                {t('Share the credentials of this mailbox with its user. You must transfer them securely, preferably physically.')}
            </Banner>
        </div>
    )
}

export default AdminMailboxCredentials;
