import { MailboxAdminCreate } from "@/features/api/gen/models/mailbox_admin_create";
import { Banner } from "@/features/ui/components/banner";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

type AdminMailboxCredentialsProps = {
    mailbox: MailboxAdminCreate;
}
const AdminMailboxCredentials = ({ mailbox }: AdminMailboxCredentialsProps) => {
    const { t } = useTranslation();
    const mailboxAddress = mailbox.local_part + "@" + mailbox.domain_name;
    const [clipboardState, setClipboardState] = useState<'idle' | 'copied' | 'error'>('idle');

    const credentialText = useMemo(() => {
        if (!mailbox.one_time_password) return '';
        return t('create_mailbox_modal.success.credential_text', { id: mailboxAddress, password: mailbox.one_time_password });
    }, [mailbox]);

    const handleCopyToClipboard = async () => {
        try {
            await navigator.clipboard.writeText(credentialText);
            setClipboardState('copied');
        } catch (error) {
            setClipboardState('error');
            console.error(error);
        }
        setTimeout(() => setClipboardState('idle'), 1337);
    };

    return (
        <div className="admin-mailbox-credentials">
            <div className="admin-mailbox-credentials__content">
                <dl>
                    <dt>{t('create_mailbox_modal.success.credential_identity')}</dt>
                    <dd>{mailbox.local_part}@{mailbox.domain_name}</dd>
                    <dt>{t('create_mailbox_modal.success.credential_password')}</dt>
                    <dd>{mailbox.one_time_password}</dd>
                </dl>
                <Button
                    color="secondary"
                    icon={<Icon name={clipboardState === 'copied' ? "check" : clipboardState === 'error' ? "close" : "content_copy"} />}
                    onClick={handleCopyToClipboard}
                >
                    {clipboardState === 'idle' && t('create_mailbox_modal.success.copy_to_clipboard')}
                    {clipboardState === 'copied' && t('create_mailbox_modal.success.credentials_copied')}
                    {clipboardState === 'error' && t('create_mailbox_modal.success.credentials_copy_error')}
                </Button>
            </div>
            <Banner type="warning" icon={<Icon name="info" type={IconType.OUTLINED} />}>
                {t('create_mailbox_modal.success.shared_password_info')}
            </Banner>
        </div>
    )
}

export default AdminMailboxCredentials;
