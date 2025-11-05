import { useMailboxContext } from "@/features/providers/mailbox";
import { ControlledModal, useModalStore } from "@/features/providers/modal-store";
import { ModalSize, useModals } from "@openfun/cunningham-react";
import { useTranslation } from "react-i18next";
import { StepForm } from "./step-form";
import { StepLoader } from "./step-loader";
import { StepCompleted } from "./step-completed";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { TaskImportCacheHelper } from "@/features/utils/task-import-cache";


export const MODAL_MESSAGE_IMPORTER_ID = "modal-message-importer";

export type IMPORT_STEP = 'idle' | 'uploading' | 'importing' | 'completed';

/**
 * A controlled modal to import messages from an archive file or an IMAP server.
 * As a controlled modal, it can be opened from anywhere once the location has contains the modal id.
 * It is divided in 3 steps :
 * - idle : Awaiting user provides a file or IMAP server credentials
 * - importing : Importing messages from the file or the IMAP server (polling the task status)
 * - completed : Importing completed once the task is SUCCESS
 */
export const ModalMessageImporter = () => {
    const { invalidateThreadMessages, invalidateThreadsStats, invalidateLabels, selectedMailbox } = useMailboxContext();
    const { t } = useTranslation();
    const modals = useModals();
    const taskImportCacheHelper = new TaskImportCacheHelper(selectedMailbox?.id);
    const [taskId, setTaskId] = useState<string | null>(taskImportCacheHelper.get());
    const [step, setStep] = useState<IMPORT_STEP>(taskId ? 'importing' : 'idle');
    const [error, setError] = useState<string | null>(null);
    const { closeModal } = useModalStore();
    const handleCompletedStepClose = () => {
        closeModal(MODAL_MESSAGE_IMPORTER_ID);
    }

    const handleImportingStepComplete = async () => {
        taskImportCacheHelper.remove();
        setTaskId(null);
        setStep('completed');
        await Promise.all([
            invalidateThreadMessages(),
            invalidateThreadsStats(),
            invalidateLabels(),
        ]);
    }


    const handleArchiveUploading = () => {
        setStep('uploading');
        setTaskId(null);
        setError(null);
        taskImportCacheHelper.remove();
    }

    const handleFormSuccess = (taskId: string) => {
        setTaskId(taskId);
        setStep('importing');
        taskImportCacheHelper.set(taskId);
    }

    const handleError = (error: string | null) => {
        setStep('idle');
        setTaskId(null);
        taskImportCacheHelper.remove();
        setError(error);
    }

    const handleConfirmCloseModal = async () => {
        const decision = await modals.confirmationModal({
            title: <span className="c__modal__text--centered">{t('An archive is uploading')}</span>,
            children: t('Are you sure you want to close this dialog? Your upload will be aborted!'),
        });

        return decision === 'yes';
    }

    // Effect to prevent the user from leaving the page while an archive is uploading
    useEffect(() => {
        if (step !== 'uploading') return;
        const unloadCallback = async (event: BeforeUnloadEvent) => {
            event.preventDefault();
        };

        window.addEventListener("beforeunload", unloadCallback);
        return () => window.removeEventListener("beforeunload", unloadCallback);
    }, [step]);


    if (!selectedMailbox) return null;

    return (
        <ControlledModal
            title={t('Import your old messages in {{mailbox}}', { mailbox: selectedMailbox.email })}
            modalId={MODAL_MESSAGE_IMPORTER_ID}
            size={ModalSize.LARGE}
            confirmFn={step !== 'uploading' ? undefined : handleConfirmCloseModal}
        >
            <div className="modal-importer">
                {(step === 'idle' || step === 'uploading' || step === 'importing') && (
                    <div
                        className={clsx("flex-column flex-align-center", { "c__offscreen": step === 'importing' })}
                        style={{ gap: 'var(--c--theme--spacings--xl)' }}
                    >
                        <StepForm
                            onUploading={handleArchiveUploading}
                            onSuccess={handleFormSuccess}
                            onError={handleError}
                            step={step}
                            error={error}
                        />
                    </div>
                )}
                {step === 'importing' && (
                    <StepLoader
                        taskId={taskId!}
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
