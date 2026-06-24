import { StatusEnum, useImportsCancelCreate } from "@/features/api/gen";
import ProgressBar from "@/features/ui/components/progress-bar";
import { useImportStatus } from "@/hooks/use-import-status";
import { ImportTaskRecap } from "@/hooks/use-task-status";
import { Button } from "@gouvfr-lasuite/cunningham-react";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

type StepLoaderProps = {
    importId: string;
    onComplete: (recap: ImportTaskRecap) => void;
    onError: (error: string) => void;
    onCancelled: () => void;
}

const renderProgressText = (
    t: ReturnType<typeof useTranslation>['t'],
    importStatus: NonNullable<ReturnType<typeof useImportStatus>>
) => {
    if (importStatus.progress !== null && importStatus.progress > 0) {
        return <p>{t('{{progress}}% imported', { progress: importStatus.progress })}</p>;
    }
    if (importStatus.currentMessage > 0 && !importStatus.hasKnownTotal) {
        return <p>{t('{{count}} messages imported', { count: importStatus.currentMessage })}</p>;
    }
    return null;
};

export const StepLoader = ({ importId, onComplete, onError, onCancelled }: StepLoaderProps) => {
    const { t } = useTranslation();
    const importStatus = useImportStatus(importId, {
        exhaustedError: t('An error occurred while importing messages.'),
    });

    // Use refs to avoid stale closures without requiring stable callback props
    const onCompleteRef = useRef(onComplete);
    onCompleteRef.current = onComplete;
    const onErrorRef = useRef(onError);
    onErrorRef.current = onError;
    const onCancelledRef = useRef(onCancelled);
    onCancelledRef.current = onCancelled;

    const cancelMutation = useImportsCancelCreate({
        mutation: {
            meta: { noGlobalError: true },
            onSuccess: () => onCancelledRef.current(),
            onError: () => onErrorRef.current(t('An error occurred while cancelling the import.')),
        },
    });

    useEffect(() => {
        if (!importStatus) return;
        if (importStatus.state === StatusEnum.SUCCESS) {
            onCompleteRef.current({
                successCount: importStatus.successCount,
                failureCount: importStatus.failureCount,
                totalMessages: importStatus.totalMessages,
            });
        } else if (importStatus.state === StatusEnum.FAILURE) {
            const error = importStatus.error || '';
            const isAuthError =
                error.includes("AUTHENTICATIONFAILED") ||
                error.includes("IMAP authentication failed");
            const isPstUnreadable = error.includes("PST_UNREADABLE");

            const parts: string[] = [];

            if (isAuthError) {
                parts.push(t('Authentication failed. Please check your credentials and ensure you have enabled IMAP connections in your account.'));
            } else if (isPstUnreadable) {
                parts.push(t('The PST archive is unreadable: the file is corrupt or its internal structure is incomplete. Retrying will not help — please try to re-generate the archive.'));
            } else {
                parts.push(t('An error occurred while importing messages.'));
                if (importStatus.successCount > 0) {
                    parts.push(t('{{count}} messages were imported before the error.', { count: importStatus.successCount }));
                }
                parts.push(t('You can safely retry the import — messages already imported will not be duplicated.'));
            }

            onErrorRef.current(parts.join(' '));
        }
    }, [importStatus?.state, t]);

    if (!importStatus) {
        return (
            <div className="task-loader">
                <Spinner size="lg" />
                <div className="task-loader__progress_resume">
                    <p>{t('Importing...')}</p>
                </div>
            </div>
        );
    }

    return (
        <div className="task-loader">
            <Spinner size="lg" />
            <div className="task-loader__progress_resume">
                <p>{t('Importing...')}</p>
                {renderProgressText(t, importStatus)}
            </div>
            <ProgressBar progress={importStatus.progress} />
            {importStatus.state === StatusEnum.PROGRESS && (
                <>
                    <p>{t('You can close this window and continue using the app.')}</p>
                    <Button
                        type="button"
                        color="brand"
                        variant="tertiary"
                        aria-busy={cancelMutation.isPending}
                        disabled={cancelMutation.isPending}
                        icon={cancelMutation.isPending ? <Spinner size="sm" /> : undefined}
                        onClick={() => cancelMutation.mutate({ id: importId })}
                    >
                        {cancelMutation.isPending ? t('Cancelling the import...') : t('Cancel the import')}
                    </Button>
                </>
            )}
        </div>
    );
}
