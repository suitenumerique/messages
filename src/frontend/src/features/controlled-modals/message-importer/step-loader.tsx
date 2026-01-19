import { StatusEnum } from "@/features/api/gen";
import ProgressBar from "@/features/ui/components/progress-bar";
import { useImportTaskStatus } from "@/hooks/use-import-task";
import { Spinner } from "@gouvfr-lasuite/ui-kit";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

type StepLoaderProps = {
    taskId: string;
    onComplete: () => void;
    onError: (error: string) => void;
}

export type TaskMetadata = {
    current_message: number;
    total_messages: number;
    failure_count: number;
    success_count: number;
    message_status: string;
    type: "string";

}

export const StepLoader = ({ taskId, onComplete, onError }: StepLoaderProps) => {
    const { t } = useTranslation();
    const importStatus = useImportTaskStatus(taskId)!;

    useEffect(() => {
        if (importStatus?.state === StatusEnum.SUCCESS) {
            onComplete();
        } else if (importStatus?.state === StatusEnum.FAILURE) {
            const error = importStatus?.error || '';
            let errorKey = t('An error occurred while importing messages.');
            if (
                error.includes("AUTHENTICATIONFAILED") ||
                error.includes("IMAP authentication failed")
            ) {
                errorKey = t('Authentication failed. Please check your credentials and ensure you have enabled IMAP connections in your account.');
            }
            onError(errorKey);
        }
    }, [importStatus?.state]);

    return (
        <div className="task-loader">
            <Spinner size="lg" />
            <div className="task-loader__progress_resume">
                <p>{t('Importing...')}</p>
                {importStatus.progress > 0&& t('{{progress}}% imported', { progress: importStatus.progress })}
            </div>
            <ProgressBar progress={importStatus.progress} />
            {importStatus.state === StatusEnum.PROGRESS && <p>{t('You can close this window and continue using the app.')}</p>}
        </div>
    );
}
