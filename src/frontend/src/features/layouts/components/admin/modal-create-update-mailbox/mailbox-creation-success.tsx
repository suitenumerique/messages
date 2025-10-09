import { MailboxAdminCreate } from "@/features/api/gen";
import { Banner } from "@/features/ui/components/banner";
import { Icon, IconType } from "@gouvfr-lasuite/ui-kit";
import { Button } from "@openfun/cunningham-react";
import { Trans, useTranslation } from "react-i18next";
import AdminMailboxCredentials from "../mailbox-credentials";
import MailboxHelper from "@/features/utils/mailbox-helper";

type MailboxCreationSuccessProps = {
    type: "personal" | "shared" | "redirect";
    mailbox: MailboxAdminCreate;
    onClose: () => void;
}

export const MailboxCreationSuccess = ({ type, mailbox, onClose }: MailboxCreationSuccessProps) => {
    const { t } = useTranslation();
    const mailboxAddress = MailboxHelper.toString(mailbox);

    return (
        <div className="modal-create-address-success">
          <div className="modal-create-address__description">
                <div className="success-img-container">
                  <img src="/images/welcome.webp" alt="" />
                </div>
                {
                  type === "redirect" && (
                    <p>
                      <Trans i18nKey="The redirect mailbox <strong>{{mailboxAddress}}</strong> has been created successfully." values={{mailboxAddress}}>
                        The redirect mailbox <strong>{mailboxAddress}</strong> has been created successfully.
                      </Trans>
                    </p>
                  )
                }
                {
                  type === "shared" && (
                    <p>
                      <Trans i18nKey="The shared mailbox <strong>{{mailboxAddress}}</strong> has been created successfully." values={{mailboxAddress}}>
                        The shared mailbox <strong>{mailboxAddress}</strong> has been created successfully.
                      </Trans>
                    </p>
                  )
                }
                {
                  type === "personal" && (
                    <>
                      <p>
                        <Trans i18nKey="The personal mailbox <strong>{{mailboxAddress}}</strong> has been created successfully." values={{mailboxAddress}}>
                          The personal mailbox <strong>{mailboxAddress}</strong> has been created successfully.
                        </Trans></p>
                      {
                        mailbox.one_time_password ? (
                          <AdminMailboxCredentials mailbox={mailbox} />
                      ) : (
                        <Banner type="warning" icon={<Icon name="info" type={IconType.OUTLINED} />}>
                          {t('You can now inform the person that their mailbox is ready to be used and communicate the instructions for authentication.')}
                        </Banner>
                      )}
                    </>
                  )
                }
            </div>
            <Button onClick={onClose} color="primary">{t('Close')}</Button>
        </div>
    )
}
