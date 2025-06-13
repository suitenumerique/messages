import { useMailboxContext } from "@/features/providers/mailbox";
import { ControlledModal, useModalStore } from "@/features/providers/modal-store";
import { ModalSize } from "@openfun/cunningham-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { StepForm } from "./step-form";
import { StepLoader } from "./step-loader";
import { StepCompleted } from "./step-completed";
import { Banner } from "@/features/ui/components/banner";
import clsx from "clsx";


export const MODAL_MESSAGE_IMPORTER_ID = "modal-message-importer";

type IMPORT_STEP = 'idle' | 'importing' | 'completed';

/**
 * A controlled modal to import messages from an archive file or an IMAP server.
 * As a controlled modal, it can be opened from anywhere once the location has contains the modal id.
 * It is divided in 3 steps :
 * - idle : Awaiting user provides a file or IMAP server credentials
 * - importing : Importing messages from the file or the IMAP server (polling the task status)
 * - completed : Importing completed once the task is SUCCESS
 */
export const ModalMessageImporter = () => {
    const { invalidateThreadMessages, invalidateThreadsStats } = useMailboxContext();
    const { t } = useTranslation();
    const [step, setStep] = useState<IMPORT_STEP>('idle');
    const [error, setError] = useState<string | null>(null);
    const [taskId, setTaskId] = useState<string>('');
    const { closeModal } = useModalStore();
    const onClose = () => {
        setStep('idle');
        setTaskId('');
        setError(null);
    }
    const handleCompletedStepClose = () => {
        onClose();
        closeModal(MODAL_MESSAGE_IMPORTER_ID);
    }

    const handleImportingStepComplete = async () => {
        setStep('completed');
        await Promise.all([
            invalidateThreadMessages(),
            invalidateThreadsStats(),
        ]);
    }

    const handleFormSuccess = (taskId: string) => {
        setStep('importing');
        setTaskId(taskId);
    }

    const handleError = (error: string) => {
        setStep('idle');
        setError(error);
    }


    return (
        <ControlledModal
            title={t('message_importer_modal.title')}
            modalId={MODAL_MESSAGE_IMPORTER_ID}
            size={ModalSize.LARGE}
            onClose={onClose}
        >
            <div className="modal-importer">
                {(step === 'idle' || step === 'importing') && (
                    <div
                        className={clsx("flex-column flex-align-center", { "c__offscreen": step === 'importing' })}
                        style={{ gap: 'var(--c--theme--spacings--xl)' }}
                    >
                        {error && ( <Banner type="error"><p>{t(error)}</p></Banner> )}
                        <StepForm
                            onSuccess={handleFormSuccess}
                            onError={handleError}
                        />
                    </div>
                )}
                {step === 'importing' && (
                    <StepLoader
                        taskId={taskId}
                        onComplete={handleImportingStepComplete}
                        onError={handleError}
                    />
                )}
                {step === 'completed' && (
                    <StepCompleted onClose={handleCompletedStepClose} />
                )}
            </div>
        </ControlledModal>
    );
};
