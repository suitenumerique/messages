import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

type StepLoaderProps = {
    taskId: string;
    onComplete: () => void;
    onError: (error: string) => void;
}

export const StepLoader = ({ taskId, onComplete, onError }: StepLoaderProps) => {
    const { t } = useTranslation();
    const taskQuery = useTasksRetrieve(taskId, {
        query: {
            refetchInterval: 500,
            enabled: Boolean(taskId),
            meta: {
                noGlobalError: true,
            }
        }
    });

    const progress = (taskQuery.data?.data.current / taskQuery.data?.data.total * 100) || null;

    useEffect(() => {
        if (taskQuery.data) {
            if (taskQuery.data.data.status === StatusEnum.SUCCESS) {
                onComplete();
            } else if (taskQuery.data.data.status === StatusEnum.FAILURE) {
                const error = taskQuery.data.data.error || '';
                let errorKey = "message_importer_modal.api_errors.default";
                if (error.includes("AUTHENTICATIONFAILED")) {
                    errorKey = "message_importer_modal.api_errors.authentication_failed";
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
                        ? t('message_importer_modal.progression', { count: taskQuery.data?.data.current, total: taskQuery.data?.data.total })
                        : t('message_importer_modal.progression_default')
                    }
                </strong>
            </p>
            {progress && <p>{t('message_importer_modal.progression_details')}</p>}
        </div>
    );
}
