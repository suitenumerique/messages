import { useMaildomainsMailboxesResetPassword } from "@/features/api/gen/maildomains/maildomains";
import { MailboxAdmin } from "@/features/api/gen/models/mailbox_admin";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Modal, ModalSize } from "@openfun/cunningham-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import AdminMailboxCredentials from "../mailbox-credentials";
import { ResetPasswordResponse } from "@/features/api/gen/models/reset_password_response";
import { Banner } from "@/features/ui/components/banner";
import MailboxHelper from "@/features/utils/mailbox-helper";

type ModalMailboxResetPasswordProps = {
    isOpen: boolean;
    onClose: () => void;
    mailbox: MailboxAdmin;
    domainId: string;
}

const ModalMailboxResetPassword = ({ isOpen, onClose, mailbox, domainId }: ModalMailboxResetPasswordProps) => {
    const { t } = useTranslation();
    const [state, setState] = useState<"idle" | "success" | "error">("idle");
    const [oneTimePassword, setOneTimePassword] = useState<string | null>(null);
    const { mutateAsync: resetPassword, isPending } = useMaildomainsMailboxesResetPassword();
    const onResetPassword = async () => {
        try {
            const response = await resetPassword({ maildomainPk: domainId, id: mailbox.id });
            setOneTimePassword((response.data as ResetPasswordResponse).one_time_password);
            setState("success");
        } catch (error) {
            console.error(error);
            setState("error");
        }
    }

    /**
     * Effect to reset internal states when the modal is closed
     */
    useEffect(() => {
        if (!isOpen) {
            setState("idle");
            setOneTimePassword(null);
        }
    }, [isOpen]);

    return (
        <Modal
            isOpen={isOpen}
            title={t('Reset password of {{mailbox}}', { mailbox: MailboxHelper.toString(mailbox) })}
            size={ModalSize.MEDIUM}
            onClose={onClose}
        >
            <div className="modal-mailbox-reset-password">
                {['idle', 'error'].includes(state) &&
                    <section className="modal-mailbox-reset-password__idle">
                        <header>
                            <h3>{t('Are you sure you want to reset the password?')}</h3>
                            <p>{t('This action cannot be undone and the user will need the new password to access its mailbox.')}</p>
                        </header>
                        {state === 'error' &&
                            <Banner type="error">{t('An error occurred while resetting the password.')}</Banner>
                        }
                        <footer>
                            <Button
                                color="secondary"
                                onClick={onClose}
                                disabled={isPending}
                                icon={isPending && <Spinner />}
                            >
                                {t('Cancel')}
                            </Button>
                            <Button
                                color="danger"
                                onClick={onResetPassword}
                                disabled={isPending}
                                icon={isPending && <Spinner />}
                            >
                                {t('Reset password')}
                            </Button>
                        </footer>
                    </section>
                }
                {state === 'success' &&
                    <section className="modal-mailbox-reset-password__success">
                        <header>
                            <h3>{t('Password reset successfully!')}</h3>
                            <p>{t('Share the new credentials to the user.')}</p>
                        </header>
                        <AdminMailboxCredentials mailbox={{ ...mailbox, one_time_password: oneTimePassword }} />
                        <footer>
                            <Button onClick={onClose} color="primary">{t('Close')}</Button>
                        </footer>
                    </section>
                }
            </div>
        </Modal>
    )
}

export default ModalMailboxResetPassword;
