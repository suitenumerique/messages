import { MailboxAdminCreate } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { useMemo, useState } from "react";
import { Trans, useTranslation } from "react-i18next";

type MailboxCreationSuccessProps = {
    type: "personal" | "shared" | "redirect";
    mailbox: MailboxAdminCreate;
    onClose: () => void;
}

export const MailboxCreationSuccess = ({ type, mailbox, onClose }: MailboxCreationSuccessProps) => {
    const { t } = useTranslation();
    const mailboxAddress = mailbox.local_part + "@" + mailbox.domain_name;
    const [clipboardState, setClipboardState] = useState<'idle' | 'copied' | 'error'>('idle');

    const credentialText = useMemo(() => {
      if (!mailbox.one_time_password) return '';
      return t('create_address_modal.success.credential_text', { id: mailboxAddress, password: mailbox.one_time_password });
    }, [mailbox]);

    const handleCopyToClipboard = () => {
      try {
        navigator.clipboard.writeText(credentialText);
        setClipboardState('copied');
      } catch (error) {
        setClipboardState('error');
        console.error(error);
      }
      setTimeout(() => setClipboardState('idle'), 1337);
    };

    return (
        <div className="modal-create-address-success">
          <div className="modal-create-address__description">
                <div className="success-img-container">
                  <img src="/images/welcome.webp" alt="" />
                </div>
                {
                  type === "redirect" && (
                    <p><Trans i18nKey="create_address_modal.success.redirect" values={{mailbox:mailboxAddress}}/></p>
                  )
                }
                {
                  type === "shared" && (
                    <p><Trans i18nKey="create_address_modal.success.shared" values={{mailbox:mailboxAddress}}/></p>
                  )
                }
                {
                  type === "personal" && (
                    <>
                      <p><Trans i18nKey="create_address_modal.success.personal" values={{mailbox:mailboxAddress}}/></p>
                      {
                        mailbox.one_time_password ? (
                          <>
                            <div className="modal-create-address__credentials">
                              <dl>
                                <dt>{t('create_address_modal.success.credential_identity')}</dt>
                                <dd>{mailbox.local_part}@{mailbox.domain_name}</dd>
                                <dt>{t('create_address_modal.success.credential_password')}</dt>
                                <dd>{mailbox.one_time_password}</dd>
                              </dl>
                              <Button
                                color="secondary"
                                icon={<Icon name={clipboardState === 'copied' ? "check" : clipboardState === 'error' ? "close" : "content_copy"} />}
                                onClick={handleCopyToClipboard}
                              >
                                {clipboardState === 'idle' && t('create_address_modal.success.copy_to_clipboard')}
                                {clipboardState === 'copied' && t('create_address_modal.success.credentials_copied')}
                                {clipboardState === 'error' && t('create_address_modal.success.credentials_copy_error')}
                              </Button>
                            </div>
                            <Banner type="warning" icon={<Icon name="info" type={IconType.OUTLINED} />}>
                              {t('create_address_modal.success.shared_password_info')}
                            </Banner>
                          </>
                      ) : (
                        <Banner type="warning" icon={<Icon name="info" type={IconType.OUTLINED} />}>
                          {t('create_address_modal.success.credential_info')}
                        </Banner>
                      )}
                    </>
                  )
                }
            </div>
            <Button onClick={onClose} color="primary">{t('actions.close')}</Button>
        </div>
    )
}
