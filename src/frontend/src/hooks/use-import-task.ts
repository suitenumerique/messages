import { StatusEnum, useTasksRetrieve } from "@/features/api/gen";
import { TaskMetadata } from "@/features/controlled-modals/message-importer/step-loader";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const MAX_POLL_ERRORS = 10;

export function useImportTaskStatus(
  taskId: string | null,
  {
    refetchInterval = 1000,
    enabled = true,
  }: { refetchInterval?: number; enabled?: boolean } = {}
) {
  const { t } = useTranslation();
  const [queryEnabled, setQueryEnabled] = useState(enabled);
  const [hasExhaustedRetries, setHasExhaustedRetries] = useState(false);
  const errorCountRef = useRef(0);
  const taskQuery = useTasksRetrieve(taskId || "", {
    query: {
      enabled: Boolean(taskId) && queryEnabled === true,
      refetchInterval,
      meta: {
        noGlobalError: true,
      },
    },
  });

  const taskStatus = taskQuery.data?.data.status;
  const taskMetadata = taskQuery.data?.data.result as TaskMetadata | undefined;

  const hasKnownTotal = taskMetadata?.total_messages != null && taskMetadata.total_messages > 0;
  const currentMessage = taskMetadata?.current_message ?? 0;

  const progress = useMemo(() => {
    if (taskStatus === StatusEnum.SUCCESS) return 100;
    if (taskStatus && taskStatus !== StatusEnum.PROGRESS) return 0;
    if (!hasKnownTotal) return null;
    if (!taskMetadata?.success_count || !taskMetadata.total_messages)
      return null;
    return (taskMetadata.success_count / taskMetadata.total_messages) * 100;
  }, [taskStatus, taskMetadata, hasKnownTotal]);

  useEffect(() => {
    if (taskQuery.isError) {
      errorCountRef.current += 1;
      if (errorCountRef.current >= MAX_POLL_ERRORS) {
        setHasExhaustedRetries(true);
      }
    } else if (taskQuery.data) {
      errorCountRef.current = 0;
    }
  }, [taskQuery.dataUpdatedAt, taskQuery.errorUpdatedAt]);

  useEffect(() => {
    if (!enabled || taskStatus === StatusEnum.FAILURE || taskStatus === StatusEnum.SUCCESS || hasExhaustedRetries) {
      setQueryEnabled(false);
    } else if (enabled || taskStatus === StatusEnum.PROGRESS || taskStatus === StatusEnum.PENDING) {
      setQueryEnabled(true);
    }
  }, [taskStatus, enabled, hasExhaustedRetries]);

  if (!taskId) return null;
  return {
    progress: progress !== null ? Math.ceil(progress) : null,
    state: hasExhaustedRetries ? StatusEnum.FAILURE : taskQuery.data?.data.status,
    loading: taskQuery.isPending || progress === null,
    error: hasExhaustedRetries
      ? t('An error occurred while importing messages.')
      : taskQuery.data?.data.error,
    hasKnownTotal,
    currentMessage,
    successCount: taskMetadata?.success_count ?? 0,
  };
}
