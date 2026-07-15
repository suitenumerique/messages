import { useMailboxContext } from "@/features/providers/mailbox";
import { ControlledModal, useModalStore } from "@/features/providers/modal-store";
import { ModalSize, useModals } from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { StepForm } from "./step-form";
import { StepLoader } from "./step-loader";
import { StepCompleted } from "./step-completed";
import clsx from "clsx";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getMailboxesImportsListQueryKey } from "@/features/api/gen";
import { TaskImportCacheHelper } from "@/features/utils/task-import-cache";
import { ImportRecap } from "@/hooks/use-import-status";


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
    const { invalidateMailbox, invalidateThreadsStats, invalidateLabels, refetchMailboxes, selectedMailbox } = useMailboxContext();
    const { t } = useTranslation();
    const modals = useModals();
    const queryClient = useQueryClient();
    // Refresh the mailbox's imports list (Imports settings tab) whenever a run
    // is created/finished/cancelled here — its grid stops polling when it has no
    // active row, so it would otherwise miss a newly created import.
    const invalidateImports = () =>
        queryClient.invalidateQueries({
            queryKey: getMailboxesImportsListQueryKey(selectedMailbox?.id),
            exact: false,
        });
    const taskImportCacheHelper = new TaskImportCacheHelper(selectedMailbox?.id);
    const [importId, setImportId] = useState<string | null>(taskImportCacheHelper.get());
    const [step, setStep] = useState<IMPORT_STEP>(importId ? 'importing' : 'idle');
    const [error, setError] = useState<string | null>(null);
    const [recap, setRecap] = useState<ImportRecap | null>(null);
    const { closeModal } = useModalStore();

    // Track Alt key for force-reset on alt+close
    const altKeyRef = useRef(false);
    useEffect(() => {
        const onKey = (e: KeyboardEvent) => { altKeyRef.current = e.altKey; };
        const onBlur = () => { altKeyRef.current = false; };
        window.addEventListener('keydown', onKey);
        window.addEventListener('keyup', onKey);
        window.addEventListener('blur', onBlur);
        return () => {
            window.removeEventListener('keydown', onKey);
            window.removeEventListener('keyup', onKey);
            window.removeEventListener('blur', onBlur);
        };
    }, []);

    const handleClose = () => {
        if (altKeyRef.current && step === 'importing') {
            taskImportCacheHelper.remove();
            setImportId(null);
            setStep('idle');
        }
    };

    const handleCompletedStepClose = () => {
        closeModal(MODAL_MESSAGE_IMPORTER_ID);
    }

    const handleImportingStepComplete = async (taskRecap: ImportRecap) => {
        taskImportCacheHelper.remove();
        setImportId(null);
        setRecap(taskRecap);
        setStep('completed');
        await Promise.all([
            refetchMailboxes(),
            invalidateThreadsStats(),
            invalidateMailbox(),
            invalidateLabels(),
            invalidateImports(),
        ]);
    }

    // The import was cancelled: its messages were deleted server-side, so go
    // back to the form and refresh the mailbox to drop them from the UI.
    const handleImportCancelled = async () => {
        taskImportCacheHelper.remove();
        setImportId(null);
        setError(null);
        setStep('idle');
        await Promise.all([
            refetchMailboxes(),
            invalidateThreadsStats(),
            invalidateMailbox(),
            invalidateLabels(),
            invalidateImports(),
        ]);
    }


    const handleArchiveUploading = () => {
        setStep('uploading');
        setImportId(null);
        setError(null);
        taskImportCacheHelper.remove();
    }

    const handleFormSuccess = (importId: string) => {
        setImportId(importId);
        setStep('importing');
        taskImportCacheHelper.set(importId);
        // New run just created — surface it in the Imports tab immediately.
        invalidateImports();
    }

    const handleError = (error: string | null) => {
        setStep('idle');
        setImportId(null);
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
            aria-label={t('Import your old messages in {{mailbox}}', { mailbox: selectedMailbox.email })}
            modalId={MODAL_MESSAGE_IMPORTER_ID}
            size={ModalSize.LARGE}
            onClose={handleClose}
            confirmFn={step !== 'uploading' ? undefined : handleConfirmCloseModal}
        >
            <div className="modal-importer">
                {(step === 'idle' || step === 'uploading' || step === 'importing') && (
                    <div
                        className={clsx("flex-column flex-align-center", { "c__offscreen": step === 'importing' })}
                        style={{ gap: 'var(--c--globals--spacings--xl)' }}
                    >
                        <StepForm
                            mailboxId={selectedMailbox.id}
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
                        mailboxId={selectedMailbox.id}
                        importId={importId!}
                        onComplete={handleImportingStepComplete}
                        onError={handleError}
                        onCancelled={handleImportCancelled}
                    />
                )}
                {step === 'completed' && (
                    <StepCompleted onClose={handleCompletedStepClose} recap={recap} />
                )}
            </div>
        </ControlledModal>
    );
};
