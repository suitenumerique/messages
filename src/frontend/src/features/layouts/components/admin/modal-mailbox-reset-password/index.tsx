import { useMaildomainsMailboxesResetPasswordPartialUpdate } from "@/features/api/gen/maildomains/maildomains";
import { MailboxAdmin } from "@/features/api/gen/models/mailbox_admin";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { Button, Modal, ModalSize } from "@openfun/cunningham-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import AdminMailboxCredentials from "../mailbox-credentials";
import { ResetPasswordResponse } from "@/features/api/gen/models/reset_password_response";
import { Banner } from "@/features/ui/components/banner";

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
    const { mutateAsync: resetPassword, isPending } = useMaildomainsMailboxesResetPasswordPartialUpdate();
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
            title={t('reset_password_modal.modal_title', { mailbox: mailbox.local_part + "@" + mailbox.domain_name })}
            size={ModalSize.MEDIUM}
            onClose={onClose}
        >
            <div className="modal-mailbox-reset-password">
                {['idle', 'error'].includes(state) &&
                    <section className="modal-mailbox-reset-password__idle">
                        <header>
                            <h3>{t('reset_password_modal.idle__title')}</h3>
                            <p>{t('reset_password_modal.idle__description')}</p>
                        </header>
                        {state === 'error' &&
                            <Banner type="error">{t('reset_password_modal.error')}</Banner>
                        }
                        <footer>
                            <Button
                                color="secondary"
                                onClick={onClose}
                                disabled={isPending}
                                icon={isPending && <Spinner />}
                            >
                                {t('actions.cancel')}
                            </Button>
                            <Button
                                color="danger"
                                onClick={onResetPassword}
                                disabled={isPending}
                                icon={isPending && <Spinner />}
                            >
                                {t('reset_password_modal.cta')}
                            </Button>
                        </footer>
                    </section>
                }
                {state === 'success' &&
                    <section className="modal-mailbox-reset-password__success">
                        <header>
                            <h3>{t('reset_password_modal.success__title')}</h3>
                            <p>{t('reset_password_modal.success__description')}</p>
                        </header>
                        <AdminMailboxCredentials mailbox={{ ...mailbox, one_time_password: oneTimePassword }} />
                        <footer>
                            <Button onClick={onClose} color="primary">{t('actions.close')}</Button>
                        </footer>
                    </section>
                }
            </div>
        </Modal>
    )
}

export default ModalMailboxResetPassword;
