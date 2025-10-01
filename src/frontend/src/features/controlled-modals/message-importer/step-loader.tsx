import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

type StepLoaderProps = {
    taskId: string;
    onComplete: () => void;
    onError: (error: string) => void;
}

type TaskMetadata = {
    current_message: number;
    total_messages: number;
    failure_count: number;
    success_count: number;
    message_status: string;
    type: "string";

}

export const StepLoader = ({ taskId, onComplete, onError }: StepLoaderProps) => {
    const { t } = useTranslation();
    const taskQuery = useTasksRetrieve(taskId, {
        query: {
            refetchInterval: 1000,
            enabled: Boolean(taskId),
            meta: {
                noGlobalError: true,
            }
        }
    });

    const taskMetadata = (taskQuery.data?.data.result) as TaskMetadata | undefined;
    const progress = taskMetadata ? (taskMetadata.success_count / taskMetadata.total_messages * 100) : null;

    useEffect(() => {
        if (taskQuery.data) {
            if (taskQuery.data.data.status === StatusEnum.SUCCESS) {
                onComplete();
            } else if (taskQuery.data.data.status === StatusEnum.FAILURE) {
                const error = taskQuery.data.data.error || '';
                let errorKey = t('An error occurred while importing messages.');
                if (error.includes("AUTHENTICATIONFAILED")) {
                    errorKey = t('Authentication failed. Please check your credentials and ensure you have enabled IMAP connections in your account.');
                }
                onError(errorKey);
            }
        }
    }, [taskQuery.data]);

    return (
        <div className="task-loader">
            <div className="task-loader__progress_bar">
                <div className="task-loader__progress_bar__progress" style={{ width: `${progress || 0}%` }} />
            </div>
            <p className="task-loader__progress_resume">
                <strong>
                    {   progress
                        ? t(
                            '{{count}} messages imported on {{total}}',
                            {
                                count: taskMetadata!.current_message,
                                total: taskMetadata!.total_messages,
                                defaultValue_one: '{{count}} message imported on {{total}}'
                            })
                        : t('Importing...')
                    }
                </strong>
            </p>
            {!!progress && <p>{t('You can close this window and continue using the app.')}</p>}
        </div>
    );
}
